package engine

import (
	"context"
	"crypto/sha256"
	"fmt"
	"log/slog"
	"math"
	"os"
	"runtime"
	"strconv"
	"strings"

	dockertypes "github.com/docker/docker/api/types"
	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"
	"github.com/docker/docker/api/types/network"
	"github.com/docker/docker/client"
	"github.com/docker/go-connections/nat"

	"github.com/acestream/controlplane/internal/config"
	"github.com/acestream/controlplane/internal/state"
	"github.com/acestream/controlplane/internal/vpn"
)

// ResourceScheduler atomically resolves VPN node, port assignments, and
// forwarding election for a new engine — the exact Go equivalent of the
// Python ResourceScheduler.
type ResourceScheduler struct{}

var Scheduler = &ResourceScheduler{}

// ScheduleNewEngine resolves all resources and returns an immutable EngineSpec.
// extraReservedNames prevents name collisions during burst provisioning.
func (rs *ResourceScheduler) ScheduleNewEngine(extraReservedNames []string) (*state.EngineSpec, error) {
	cfg := config.C
	st := state.Global

	image := resolveEngineImage()
	memLimit := cfg.EngineMemoryLimit
	variantName := fmt.Sprintf("global-%s", detectPlatform())

	// Schedule resources under a lock-free approach: we use atomic state reads
	// combined with per-VPN pending counters to prevent double-allocation.
	vpnContainer, err := rs.selectVPNContainer()
	if err != nil {
		return nil, err
	}

	if vpnContainer != "" {
		if !isVPNHealthy(vpnContainer) {
			return nil, fmt.Errorf("VPN '%s' is not healthy - cannot schedule", vpnContainer)
		}
		st.IncrVPNPending(vpnContainer)
	}

	ports, err := Alloc.AllocateEnginePorts(
		vpnContainer != "",
		vpnContainer,
		0, 0, 0, 0,
		cfg.ACEMapHTTPS,
	)
	if err != nil {
		if vpnContainer != "" {
			st.DecrVPNPending(vpnContainer)
		}
		return nil, fmt.Errorf("port allocation failed: %w", err)
	}

	forwarded, p2pPort := rs.electForwardedEngine(context.Background(), vpnContainer)

	containerName := generateContainerName("acestream", extraReservedNames)

	labelKey := cfg.ContainerLabelKey
	labelVal := cfg.ContainerLabelVal
	configHash := computeConfigHash()

	labels := map[string]string{
		labelKey:                             labelVal,
		"acestream.http_port":                strconv.Itoa(ports.ContainerHTTPPort),
		"acestream.https_port":               strconv.Itoa(ports.ContainerHTTPSPort),
		"acestream.api_port":                 strconv.Itoa(ports.ContainerAPIPort),
		"host.http_port":                     strconv.Itoa(ports.HostHTTPPort),
		"host.api_port":                      strconv.Itoa(ports.HostAPIPort),
		"acestream.engine_variant":           variantName,
		"acestream.config_hash":              configHash,
		"acestream.config_generation":        "1",
	}
	if vpnContainer != "" {
		labels["acestream.vpn_container"] = vpnContainer
	}
	if forwarded {
		labels["acestream.forwarded"] = "true"
	}
	if ports.HostHTTPSPort != 0 {
		labels["host.https_port"] = strconv.Itoa(ports.HostHTTPSPort)
	}

	// Docker port bindings (only when NOT behind a VPN network namespace)
	var dockerPorts map[string]int
	if vpnContainer == "" {
		dockerPorts = map[string]int{
			fmt.Sprintf("%d/tcp", ports.ContainerHTTPPort): ports.HostHTTPPort,
			fmt.Sprintf("%d/tcp", ports.ContainerAPIPort):  ports.HostAPIPort,
		}
		if ports.HostHTTPSPort != 0 {
			dockerPorts[fmt.Sprintf("%d/tcp", ports.ContainerHTTPSPort)] = ports.HostHTTPSPort
		}
	}

	networkMode := "bridge"
	if vpnContainer != "" {
		networkMode = fmt.Sprintf("container:%s", vpnContainer)
	} else if cfg.DockerNetwork != "" {
		networkMode = cfg.DockerNetwork
	}

	cmd := buildCommand(ports.ContainerHTTPPort, ports.ContainerAPIPort, p2pPort)

	spec := &state.EngineSpec{
		ContainerName:      containerName,
		Image:              image,
		Command:            cmd,
		Labels:             labels,
		NetworkMode:        networkMode,
		Ports:              dockerPorts,
		MemLimit:           memLimit,
		VPNContainerID:     vpnContainer,
		HostHTTPPort:       ports.HostHTTPPort,
		ContainerHTTPPort:  ports.ContainerHTTPPort,
		HostAPIPort:        ports.HostAPIPort,
		ContainerAPIPort:   ports.ContainerAPIPort,
		ContainerHTTPSPort: ports.ContainerHTTPSPort,
		HostHTTPSPort:      ports.HostHTTPSPort,
		Forwarded:          forwarded,
		P2PPort:            p2pPort,
	}

	return spec, nil
}

// ReleaseSpec frees all resources reserved by a spec (called on failure).
func ReleaseSpec(spec *state.EngineSpec) {
	if spec == nil {
		return
	}
	Alloc.ReleaseFromLabels(spec.Labels)
	if spec.VPNContainerID != "" {
		state.Global.DecrVPNPending(spec.VPNContainerID)
	}
}

// ExecuteSpec creates the Docker container described by spec.
func ExecuteSpec(ctx context.Context, spec *state.EngineSpec) (string, error) {
	cli, err := newDockerClient()
	if err != nil {
		return "", fmt.Errorf("docker client: %w", err)
	}
	defer cli.Close()

	hostConfig := &container.HostConfig{
		NetworkMode: container.NetworkMode(spec.NetworkMode),
		AutoRemove:  true,
		Init:        boolPtr(true),
	}
	if spec.MemLimit != "" {
		mem, err := parseMemory(spec.MemLimit)
		if err == nil {
			hostConfig.Memory = mem
		}
	}

	// Port bindings
	portBindings := buildPortBindings(spec.Ports)
	hostConfig.PortBindings = portBindings

	containerConfig := &container.Config{
		Image:  spec.Image,
		Cmd:    spec.Command,
		Labels: spec.Labels,
	}

	net := &network.NetworkingConfig{}

	resp, err := cli.ContainerCreate(ctx, containerConfig, hostConfig, net, nil, spec.ContainerName)
	if err != nil {
		ReleaseSpec(spec)
		return "", fmt.Errorf("container create: %w", err)
	}

	if err := cli.ContainerStart(ctx, resp.ID, container.StartOptions{}); err != nil {
		cli.ContainerRemove(ctx, resp.ID, container.RemoveOptions{Force: true})
		ReleaseSpec(spec)
		return "", fmt.Errorf("container start: %w", err)
	}

	// Release pending counter now that Docker accepted the request
	if spec.VPNContainerID != "" {
		state.Global.DecrVPNPending(spec.VPNContainerID)
	}

	slog.Info("engine container started", "name", spec.ContainerName, "id", resp.ID[:12], "vpn", spec.VPNContainerID)
	return resp.ID, nil
}

// StopContainer stops (or force-kills) a container by ID.
func StopContainer(ctx context.Context, containerID string, force bool) error {
	cli, err := newDockerClient()
	if err != nil {
		return err
	}
	defer cli.Close()

	if force {
		return cli.ContainerKill(ctx, containerID, "SIGKILL")
	}
	timeout := 5
	return cli.ContainerStop(ctx, containerID, container.StopOptions{Timeout: &timeout})
}

// ListManagedContainers returns all running containers with the managed label.
func ListManagedContainers(ctx context.Context) ([]dockertypes.Container, error) {
	cli, err := newDockerClient()
	if err != nil {
		return nil, err
	}
	defer cli.Close()

	cfg := config.C
	f := filters.NewArgs()
	f.Add("label", fmt.Sprintf("%s=%s", cfg.ContainerLabelKey, cfg.ContainerLabelVal))
	f.Add("status", "running")

	return cli.ContainerList(ctx, container.ListOptions{Filters: f, All: false})
}

// ListManagedVPNContainers returns running Gluetun dynamic containers.
func ListManagedVPNContainers(ctx context.Context) ([]dockertypes.Container, error) {
	cli, err := newDockerClient()
	if err != nil {
		return nil, err
	}
	defer cli.Close()

	f := filters.NewArgs()
	f.Add("label", "acestream-orchestrator.managed=true")
	f.Add("label", "role=vpn_node")
	f.Add("status", "running")

	return cli.ContainerList(ctx, container.ListOptions{Filters: f, All: false})
}

// ─── VPN node selection ──────────────────────────────────────────────────────

func (rs *ResourceScheduler) selectVPNContainer() (string, error) {
	cfg := config.C
	st := state.Global

	dynamicNodes := st.ListVPNNodes()
	if len(dynamicNodes) == 0 {
		return "", nil // No VPN management
	}

	// Filter to ready, managed, non-draining dynamic nodes
	var readyNodes []*state.VPNNode
	var rejectReasons []string
	for _, n := range dynamicNodes {
		if !n.ManagedDynamic {
			continue
		}
		ok, reason := isNodeReady(n)
		if !ok {
			rejectReasons = append(rejectReasons, fmt.Sprintf("%s: %s", n.ContainerName, reason))
			continue
		}
		if st.IsVPNNodeDraining(n.ContainerName) {
			rejectReasons = append(rejectReasons, fmt.Sprintf("%s: draining", n.ContainerName))
			continue
		}
		readyNodes = append(readyNodes, n)
	}

	if len(readyNodes) == 0 {
		diag := strings.Join(rejectReasons, "; ")
		if diag == "" {
			diag = "none found"
		}
		return "", fmt.Errorf("no healthy active dynamic VPN nodes available (%s) - cannot schedule AceStream engine", diag)
	}

	// Balanced density: calculate effective per-node limit
	maxPerVPN := cfg.PreferredEnginesPerVPN
	desired := st.GetDesiredReplicas()
	effectiveLimit := maxPerVPN
	if maxPerVPN > 0 && desired > 0 {
		requiredNodes := int(math.Ceil(float64(desired) / float64(maxPerVPN)))
		if requiredNodes < 1 {
			requiredNodes = 1
		}
		effectiveLimit = int(math.Ceil(float64(desired) / float64(requiredNodes)))
		if effectiveLimit > maxPerVPN {
			effectiveLimit = maxPerVPN
		}
	}

	type candidate struct {
		node *state.VPNNode
		load int
	}
	var candidates []candidate
	for _, n := range readyNodes {
		current := len(st.GetEnginesByVPN(n.ContainerName))
		pending := st.GetVPNPending(n.ContainerName)
		total := current + pending
		if effectiveLimit <= 0 || total < effectiveLimit {
			candidates = append(candidates, candidate{n, total})
		} else {
			rejectReasons = append(rejectReasons, fmt.Sprintf("%s: at balanced capacity (%d/%d)", n.ContainerName, total, effectiveLimit))
		}
	}

	if len(candidates) == 0 {
		diag := strings.Join(rejectReasons, "; ")
		return "", fmt.Errorf("resource restriction: %s - cannot schedule AceStream engine", diag)
	}

	// Pick the least-loaded node
	best := candidates[0]
	for _, c := range candidates[1:] {
		if c.load < best.load {
			best = c
		}
	}

	slog.Info("scheduling new engine on VPN node", "vpn", best.node.ContainerName, "load", best.load, "limit", effectiveLimit)
	return best.node.ContainerName, nil
}

func isNodeReady(n *state.VPNNode) (bool, string) {
	if n.ContainerName == "" {
		return false, "no_name"
	}

	cond := strings.ToLower(strings.TrimSpace(n.Condition))
	if cond != "" {
		if cond != "ready" {
			return false, "condition_" + cond
		}
	} else if !n.Healthy {
		status := strings.ToLower(strings.TrimSpace(n.Status))
		if status == "unhealthy" || status == "down" {
			return false, "node_" + status
		}
		// Heuristic: if there are healthy engines on this node, it is ready
		engines := state.Global.GetEnginesByVPN(n.ContainerName)
		for _, e := range engines {
			if e.HealthStatus == state.HealthHealthy {
				return true, "ready_via_heuristic"
			}
		}
		return false, "not_healthy"
	}

	// Docker "running" can precede Gluetun API readiness
	if strings.ToLower(n.Status) == "running" {
		if !vpn.IsControlAPIReachable(n.ContainerName, true) {
			engines := state.Global.GetEnginesByVPN(n.ContainerName)
			for _, e := range engines {
				if e.HealthStatus == state.HealthHealthy {
					return true, "ready_via_heuristic_api_down"
				}
			}
			return false, "api_unreachable/not_connected"
		}
	}

	return true, "ready"
}

func isVPNHealthy(vpnContainer string) bool {
	n, ok := state.Global.GetVPNNode(vpnContainer)
	if ok && !n.Healthy {
		return false
	}
	return vpn.IsControlAPIReachable(vpnContainer, false)
}

// ─── Forwarded engine election ───────────────────────────────────────────────

func (rs *ResourceScheduler) electForwardedEngine(ctx context.Context, vpnContainer string) (forwarded bool, p2pPort int) {
	if vpnContainer == "" {
		return false, 0
	}

	st := state.Global

	if !nodeSupportsPortForwarding(vpnContainer) {
		slog.Warn("VPN does not support port forwarding; using internal P2P port", "vpn", vpnContainer)
		return false, Alloc.AllocInternalP2PPort(vpnContainer)
	}

	hasExisting := st.HasForwardedEngineForVPN(vpnContainer)
	hasPending := st.IsForwardedPending(vpnContainer)

	if !hasExisting && !hasPending {
		p := vpn.WaitForForwardedPort(ctx, vpnContainer)
		if p > 0 {
			slog.Info("elected forwarded engine", "vpn", vpnContainer, "p2p_port", p)
			return true, p
		}
		// Could not get port; use internal port, no forwarding
		slog.Warn("could not elect leader for PF-enabled VPN; using internal port", "vpn", vpnContainer)
		return false, Alloc.AllocInternalP2PPort(vpnContainer)
	}

	// Slot already taken or pending
	return false, Alloc.AllocInternalP2PPort(vpnContainer)
}

func nodeSupportsPortForwarding(vpnContainer string) bool {
	n, ok := state.Global.GetVPNNode(vpnContainer)
	if !ok {
		return true // assume yes when not tracked
	}
	if n.PortForwardingSupported {
		return true
	}
	if n.Provider != "" {
		return vpn.ProviderSupportsForwarding(n.Provider)
	}
	return true
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

func detectPlatform() string {
	arch := runtime.GOARCH
	switch arch {
	case "arm64":
		return "arm64"
	case "arm":
		return "arm32"
	default:
		return "x86_64"
	}
}

func resolveEngineImage() string {
	cfg := config.C
	variant := cfg.EngineVariant
	arch := detectPlatform()
	switch arch {
	case "arm64":
		if strings.Contains(strings.ToLower(variant), "arm64") {
			return variant
		}
		return "acestream/aceserve-arm64:" + cfg.EngineARM64Version
	case "arm32":
		return "acestream/aceserve-arm32:" + cfg.EngineARM32Version
	default:
		return variant
	}
}

func buildCommand(httpPort, apiPort, p2pPort int) []string {
	cmd := []string{
		"python", "main.py",
		"--http-port", strconv.Itoa(httpPort),
		"--api-port", strconv.Itoa(apiPort),
	}
	if p2pPort > 0 {
		cmd = append(cmd, "--port", strconv.Itoa(p2pPort))
	}
	return cmd
}

func computeConfigHash() string {
	cfg := config.C
	s := fmt.Sprintf("%s|%s|%s|%t",
		cfg.EngineMemoryLimit,
		cfg.DockerNetwork,
		cfg.EngineVariant,
		cfg.ACEMapHTTPS,
	)
	h := sha256.Sum256([]byte(s))
	return fmt.Sprintf("%x", h[:8])
}

func generateContainerName(prefix string, excluded []string) string {
	excSet := make(map[string]struct{}, len(excluded))
	for _, n := range excluded {
		excSet[n] = struct{}{}
	}
	hostname, _ := os.Hostname()
	base := fmt.Sprintf("%s-%s", prefix, hostname[:min8(len(hostname))])
	for i := 1; i < 1000; i++ {
		candidate := fmt.Sprintf("%s-%d", base, i)
		if _, taken := excSet[candidate]; !taken {
			return candidate
		}
	}
	return fmt.Sprintf("%s-%d", base, 999)
}

func min8(n int) int {
	if n < 8 {
		return n
	}
	return 8
}

func newDockerClient() (*client.Client, error) {
	opts := []client.Opt{client.FromEnv, client.WithAPIVersionNegotiation()}
	return client.NewClientWithOpts(opts...)
}

// NewDockerClientExported is the exported form of newDockerClient, used by
// packages outside this one (e.g. docker/events.go) that need a raw client.
func NewDockerClientExported() (*client.Client, error) {
	return newDockerClient()
}

func boolPtr(b bool) *bool { return &b }

func parseMemory(s string) (int64, error) {
	s = strings.TrimSpace(strings.ToUpper(s))
	multiplier := int64(1)
	switch {
	case strings.HasSuffix(s, "G"):
		multiplier = 1 << 30
		s = s[:len(s)-1]
	case strings.HasSuffix(s, "M"):
		multiplier = 1 << 20
		s = s[:len(s)-1]
	case strings.HasSuffix(s, "K"):
		multiplier = 1 << 10
		s = s[:len(s)-1]
	}
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0, err
	}
	return n * multiplier, nil
}

func buildPortBindings(ports map[string]int) nat.PortMap {
	if len(ports) == 0 {
		return nil
	}
	pm := make(nat.PortMap, len(ports))
	for containerPort, hostPort := range ports {
		pm[nat.Port(containerPort)] = []nat.PortBinding{
			{HostIP: "0.0.0.0", HostPort: strconv.Itoa(hostPort)},
		}
	}
	return pm
}
