package engine

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	dockertypes "github.com/docker/docker/api/types"
	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"
	dockerimage "github.com/docker/docker/api/types/image"
	"github.com/docker/docker/api/types/network"
	"github.com/docker/docker/client"
	"github.com/docker/go-connections/nat"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/controlplane/vpn"
	"github.com/acestream/acestream/internal/state"
)

// ResourceScheduler atomically resolves VPN node, port assignments, and
// forwarding election for a new engine — the exact Go equivalent of the
// Python ResourceScheduler.
type ResourceScheduler struct{}

var Scheduler = &ResourceScheduler{}

// ScheduleNewEngine resolves all resources and returns an immutable EngineSpec.
func (rs *ResourceScheduler) ScheduleNewEngine(ctx context.Context) (*state.EngineSpec, error) {
	cfg := config.C.Load()
	st := state.Global

	image := resolveEngineImage()
	memLimit := cfg.EngineMemoryLimit
	variantName := fmt.Sprintf("global-%s", detectPlatform())

	// Select a VPN node and atomically increment its pending counter in one
	// locked operation to prevent TOCTOU over-assignment under burst creation.
	vpnContainer, err := rs.selectVPNContainer()
	if err != nil {
		return nil, err
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

	forwarded, p2pPort := rs.electForwardedEngine(ctx, vpnContainer)

	containerName := generateContainerName("acestream")

	labelKey := cfg.ContainerLabelKey
	labelVal := cfg.ContainerLabelVal
	configHash := ComputeConfigHash()

	labels := map[string]string{
		labelKey:                      labelVal,
		"acestream.http_port":         strconv.Itoa(ports.ContainerHTTPPort),
		"acestream.https_port":        strconv.Itoa(ports.ContainerHTTPSPort),
		"acestream.api_port":          strconv.Itoa(ports.ContainerAPIPort),
		"host.http_port":              strconv.Itoa(ports.HostHTTPPort),
		"host.api_port":               strconv.Itoa(ports.HostAPIPort),
		"acestream.engine_variant":    variantName,
		"acestream.config_hash":       configHash,
		"acestream.config_generation": "1",
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

	cmd := buildCommand(ports.ContainerHTTPPort, ports.ContainerAPIPort, p2pPort, cfg)

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
		if spec.Forwarded {
			// Clear the forwarded-slot claim so a future engine can be elected
			// leader for this VPN. Without this, the slot leaks permanently if
			// the container failed to start.
			state.Global.SetForwardedPending(spec.VPNContainerID, false)
		}
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

	if err := ensureImage(ctx, cli, spec.Image); err != nil {
		ReleaseSpec(spec)
		return "", fmt.Errorf("image pull %s: %w", spec.Image, err)
	}

	net := &network.NetworkingConfig{}

	resp, err := cli.ContainerCreate(ctx, containerConfig, hostConfig, net, nil, spec.ContainerName)
	if err != nil {
		// AutoRemove:true removes the container asynchronously after the process
		// exits.  If the controller deleted the old container a moment ago, its
		// name may still be reserved while Docker's cleanup is in flight.  Detect
		// the conflict, force-remove the stale entry, and retry once.
		if strings.Contains(err.Error(), "already in use") || strings.Contains(err.Error(), "Conflict") {
			slog.Warn("container name conflict; force-removing stale container and retrying", "name", spec.ContainerName)
			_ = cli.ContainerRemove(ctx, spec.ContainerName, container.RemoveOptions{Force: true})
			resp, err = cli.ContainerCreate(ctx, containerConfig, hostConfig, net, nil, spec.ContainerName)
		}
		if err != nil {
			ReleaseSpec(spec)
			return "", fmt.Errorf("container create: %w", err)
		}
	}

	if err := cli.ContainerStart(ctx, resp.ID, container.StartOptions{}); err != nil {
		cli.ContainerRemove(ctx, resp.ID, container.RemoveOptions{Force: true})
		ReleaseSpec(spec)
		return "", fmt.Errorf("container start: %w", err)
	}

	// vpnPending is decremented by AddEngine once the container is registered,
	// not here. Keeping the counter live until registration prevents a window
	// where neither engineCount nor pending reflects the starting container,
	// which would allow over-assignment to the same VPN node.

	slog.Info("engine container started", "name", spec.ContainerName, "id", resp.ID[:12], "vpn", spec.VPNContainerID)
	state.RecordEvent(state.EventEntry{
		EventType: "engine",
		Category:  "created",
		Message:   "Engine container started",
		Details: map[string]any{
			"name": spec.ContainerName,
			"id":   resp.ID[:12],
			"vpn":  spec.VPNContainerID,
		},
	})
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

// StopEnginesByVPN stops all engines associated with the given VPN container.
func StopEnginesByVPN(ctx context.Context, vpnName string) {
	st := state.Global
	engines := st.GetEnginesByVPN(vpnName)
	if len(engines) == 0 {
		return
	}

	slog.Info("stopping engines associated with VPN", "vpn", vpnName, "count", len(engines))
	var wg sync.WaitGroup
	for _, e := range engines {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			if err := StopContainer(ctx, id, true); err != nil {
				slog.Warn("failed to stop engine on VPN destruction", "id", id[:min12(len(id))], "err", err)
			}
		}(e.ContainerID)
	}
	wg.Wait()
}

// ListManagedContainers returns all running containers with the managed label.
func ListManagedContainers(ctx context.Context) ([]dockertypes.Container, error) {
	cli, err := newDockerClient()
	if err != nil {
		return nil, err
	}
	defer cli.Close()

	cfg := config.C.Load()
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

// StopAllManaged stops all managed engines and VPN nodes in parallel.
// VPN containers are force-removed (not just killed) to prevent Docker's
// restart policy from bringing them back up.
func StopAllManaged(ctx context.Context) {
	cli, err := newDockerClient()
	if err != nil {
		slog.Error("StopAllManaged: docker client unavailable", "err", err)
		return
	}
	defer cli.Close()

	engines, _ := ListManagedContainers(ctx)
	vpns, _ := ListManagedVPNContainers(ctx)

	if len(engines) == 0 && len(vpns) == 0 {
		return
	}

	slog.Info("stopping all managed containers", "engines", len(engines), "vpns", len(vpns))

	var wg sync.WaitGroup
	for _, c := range engines {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			if err := StopContainer(ctx, id, true); err != nil {
				slog.Warn("failed to stop engine", "id", id[:min12(len(id))], "err", err)
			}
		}(c.ID)
	}
	for _, c := range vpns {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			if err := cli.ContainerRemove(ctx, id, container.RemoveOptions{Force: true}); err != nil {
				slog.Warn("failed to remove VPN container", "id", id[:min12(len(id))], "err", err)
			}
		}(c.ID)
	}
	wg.Wait()
	slog.Info("all managed containers stopped")
}

// CleanupManaged stops all managed engine containers and destroys all managed
// VPN nodes (releasing credential leases). Called on startup to clear any
// containers left from a previous run before the lifecycle manager starts.
func CleanupManaged(ctx context.Context, prov *vpn.Provisioner) {
	engines, _ := ListManagedContainers(ctx)
	vpns, _ := ListManagedVPNContainers(ctx)

	if len(engines) == 0 && len(vpns) == 0 {
		return
	}

	slog.Info("cleaning up managed containers from previous run", "engines", len(engines), "vpns", len(vpns))

	cli, err := newDockerClient()
	if err != nil {
		slog.Error("CleanupManaged: docker client unavailable", "err", err)
		return
	}
	defer cli.Close()

	var wg sync.WaitGroup
	for _, c := range engines {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			if err := StopContainer(ctx, id, true); err != nil {
				slog.Warn("cleanup: failed to stop engine", "id", id[:min12(len(id))], "err", err)
			}
		}(c.ID)
	}
	for _, c := range vpns {
		wg.Add(1)
		go func(c dockertypes.Container) {
			defer wg.Done()
			name := ""
			for _, n := range c.Names {
				name = strings.TrimPrefix(n, "/")
				break
			}
			if prov != nil && name != "" {
				if err := prov.DestroyNode(ctx, name); err != nil {
					slog.Warn("cleanup: failed to destroy VPN node", "name", name, "err", err)
				}
				return
			}
			// Fallback: no provisioner or no name — force-remove by ID.
			if err := cli.ContainerRemove(ctx, c.ID, container.RemoveOptions{Force: true}); err != nil {
				slog.Warn("cleanup: failed to remove VPN container", "id", c.ID[:min12(len(c.ID))], "err", err)
			}
		}(c)
	}
	wg.Wait()
	slog.Info("managed container cleanup complete")
}

// ─── VPN node selection ──────────────────────────────────────────────────────

// CheckVPNSchedulable returns nil if at least one healthy, non-draining VPN
// node exists, without allocating any resources. Used as a pre-flight check
// before spawning N provisioning goroutines so that "VPN not ready" is logged
// exactly once per reconcile cycle rather than N times.
// PreferredEnginesPerVPN is a scheduling preference, not a hard gate here —
// the actual placement in selectVPNContainer will prefer under-limit nodes but
// will overflow onto the least-loaded node if all are at the soft limit.
func (rs *ResourceScheduler) CheckVPNSchedulable() error {
	cfg := config.C.Load()
	st := state.Global

	nodes := st.ListVPNNodes()
	if len(nodes) == 0 {
		if cfg.VPNEnabled {
			return fmt.Errorf("VPN is enabled; awaiting VPN node provisioning - cannot schedule AceStream engine")
		}
		return nil
	}

	var rejectReasons []string
	for _, n := range nodes {
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
		return nil // at least one healthy, non-draining node available
	}

	diag := strings.Join(rejectReasons, "; ")
	if diag == "" {
		diag = "none found"
	}
	return fmt.Errorf("no healthy active dynamic VPN nodes available (%s) - cannot schedule AceStream engine", diag)
}

func (rs *ResourceScheduler) selectVPNContainer() (string, error) {
	cfg := config.C.Load()
	st := state.Global

	dynamicNodes := st.ListVPNNodes()
	if len(dynamicNodes) == 0 {
		// If VPN is enabled but no nodes are tracked yet, the lifecycle manager
		// is still provisioning the first gluetun container.  Block engine
		// creation with a transient error so we don't accidentally create
		// non-VPN engines while VPN is meant to be active.
		if cfg.VPNEnabled {
			return "", fmt.Errorf("VPN is enabled; awaiting VPN node provisioning - cannot schedule AceStream engine")
		}
		return "", nil // VPN not enabled — no VPN needed
	}

	// Filter to ready, managed, non-draining dynamic nodes.
	var readyNames []string
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
		readyNames = append(readyNames, n.ContainerName)
	}

	if len(readyNames) == 0 {
		diag := strings.Join(rejectReasons, "; ")
		if diag == "" {
			diag = "none found"
		}
		return "", fmt.Errorf("no healthy active dynamic VPN nodes available (%s) - cannot schedule AceStream engine", diag)
	}

	// Balanced density: calculate effective per-node limit.
	maxPerVPN := cfg.PreferredEnginesPerVPN
	desired := st.GetDesiredReplicas()
	effectiveLimit := maxPerVPN
	var requiredNodes int
	if maxPerVPN > 0 && desired > 0 {
		requiredNodes = int(math.Ceil(float64(desired) / float64(maxPerVPN)))
		if requiredNodes < 1 {
			requiredNodes = 1
		}
		effectiveLimit = int(math.Ceil(float64(desired) / float64(requiredNodes)))
		if effectiveLimit > maxPerVPN {
			effectiveLimit = maxPerVPN
		}
	}

	// Prefer under-limit nodes; fall back to least-loaded if all are at the
	// soft limit, but respect MaxEnginesPerVPN as a hard limit.
	chosen, load := st.SelectAndClaimVPN(readyNames, effectiveLimit)

	if chosen == "" {
		// If all healthy nodes are at their preferred density limit, check if
		// we are currently provisioning new VPN nodes. If so, block engine
		// creation with a transient error so that the new engine is scheduled
		// on a fresh, under-limit VPN node once it becomes ready.
		pendingVPNs := st.ListNotReadyVPNNodes()
		if len(pendingVPNs) > 0 {
			return "", fmt.Errorf("all VPN nodes at preferred capacity; awaiting %d pending VPN nodes - cannot overflow yet", len(pendingVPNs))
		}

		if cfg.MaxEnginesPerVPN > effectiveLimit {
			chosen, load = st.SelectAndClaimVPN(readyNames, cfg.MaxEnginesPerVPN)
			if chosen != "" {
				slog.Info("scheduling engine above preferred limit (soft overflow)",
					"vpn", chosen, "load", load, "preferred_limit", effectiveLimit, "hard_limit", cfg.MaxEnginesPerVPN)
			}
		}
	}
	if chosen == "" {
		diag := strings.Join(rejectReasons, "; ")
		if len(readyNames) > 0 {
			diag = fmt.Sprintf("%d ready nodes are all at maximum density limit", len(readyNames))
		}
		return "", fmt.Errorf("resource restriction: %s - cannot schedule AceStream engine", diag)
	}

	slog.Info("scheduling new engine on VPN node", "vpn", chosen, "load", load, "limit", effectiveLimit)
	return chosen, nil
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
		// Early-start: allow engine scheduling while the VPN tunnel is still
		// establishing (~500ms for WireGuard). The engine's init (~5.76s) is
		// entirely local — HELLOTS doesn't need outbound access — so we can
		// overlap the two startups and shave ~500ms off cold-start latency.
		// Guarded to recently-registered nodes so durably-unhealthy VPNs never
		// accumulate engines after the 30s window expires.
		const earlyStartWindow = 30 * time.Second
		if status == "running" && !n.FirstSeen.IsZero() && time.Since(n.FirstSeen) < earlyStartWindow {
			return true, "running_early_start"
		}
		return false, "not_healthy"
	}

	// Docker "running" can precede Gluetun API readiness.
	// We now rely on the background health monitor to update n.Healthy.
	// If the node is not yet marked healthy, we don't block with a synchronous
	// API call here, which keeps the scheduling loop fast.
	if strings.ToLower(n.Status) == "running" && !n.Healthy {
		// Heuristic: if there are healthy engines on this node, it is ready
		engines := state.Global.GetEnginesByVPN(n.ContainerName)
		for _, e := range engines {
			if e.HealthStatus == state.HealthHealthy {
				return true, "ready_via_heuristic"
			}
		}
		return false, "awaiting_background_health_check"
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

	// Atomically claim the forwarded slot. Only the first of N concurrent
	// scheduling goroutines will win; all others fall through to internal port.
	if !st.TryClaimForwardedSlot(vpnContainer) {
		// Slot already taken by an existing engine or a concurrent goroutine.
		return false, Alloc.AllocInternalP2PPort(vpnContainer)
	}

	p := vpn.WaitForForwardedPort(ctx, vpnContainer)
	if p > 0 {
		slog.Info("elected forwarded engine", "vpn", vpnContainer, "p2p_port", p)
		// Leave the pending flag set; it will be cleared when the engine is
		// registered (AddEngine marks forwarded=true) or on cleanup.
		return true, p
	}
	// Could not get port — release the claim so a future engine can try again.
	slog.Warn("could not elect leader for PF-enabled VPN; using internal port", "vpn", vpnContainer)
	st.SetForwardedPending(vpnContainer, false)
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
	cfg := config.C.Load()
	variant := cfg.EngineVariant
	if variant != "" {
		return variant
	}
	arch := detectPlatform()
	switch arch {
	case "arm64":
		return "ghcr.io/krinkuto11/acestream:latest-arm64"
	case "arm32":
		return "ghcr.io/krinkuto11/acestream:latest-arm64"
	default:
		return "ghcr.io/krinkuto11/acestream:latest-amd64"
	}
}

func buildCommand(httpPort, apiPort, p2pPort int, cfg *config.Config) []string {
	cmd := []string{
		"python", "main.py",
		"--http-port", strconv.Itoa(httpPort),
		"--api-port", strconv.Itoa(apiPort),
		"--bind-all",
		"--disable-sentry",
		"--log-stdout",
		"--disable-upnp",
	}
	if p2pPort > 0 {
		cmd = append(cmd, "--port", strconv.Itoa(p2pPort))
	}
	if cfg.EngineMaxDownloadRate > 0 {
		cmd = append(cmd, "--max-download-speed", strconv.Itoa(cfg.EngineMaxDownloadRate))
	}
	if cfg.EngineMaxUploadRate > 0 {
		cmd = append(cmd, "--max-upload-speed", strconv.Itoa(cfg.EngineMaxUploadRate))
	}
	if cfg.EngineLiveCacheType != "" {
		cmd = append(cmd, "--live-cache-type", cfg.EngineLiveCacheType)
		if cfg.EngineLiveCacheType == "memory" {
			cmd = append(cmd, "--live-mem-cache-size", "104857600")
		}
	}
	if cfg.EngineBufferTime > 0 {
		cmd = append(cmd, "--live-buffer", strconv.Itoa(cfg.EngineBufferTime))
	}
	return cmd
}

func ComputeConfigHash() string {
	cfg := config.C.Load()
	s := fmt.Sprintf("%s|%s|%s|%t|%t|%s|%s",
		cfg.EngineMemoryLimit,
		cfg.DockerNetwork,
		cfg.EngineVariant,
		cfg.ACEMapHTTPS,
		cfg.VPNEnabled,
		cfg.VPNProvider,
		cfg.VPNProtocol,
	)
	h := sha256.Sum256([]byte(s))
	return fmt.Sprintf("%x", h[:8])
}

func generateContainerName(prefix string) string {
	hostname, _ := os.Hostname()
	index := state.Global.GetNextEngineIndex()
	base := fmt.Sprintf("%s-%s", prefix, hostname[:min8(len(hostname))])
	return fmt.Sprintf("%s-%d", base, index)
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

func ensureImage(ctx context.Context, cli *client.Client, ref string) error {
	imgs, err := cli.ImageList(ctx, dockerimage.ListOptions{
		Filters: filters.NewArgs(filters.Arg("reference", ref)),
	})
	if err != nil {
		return err
	}
	if len(imgs) > 0 {
		return nil
	}
	slog.Info("pulling image", "image", ref)
	rc, err := cli.ImagePull(ctx, ref, dockerimage.PullOptions{})
	if err != nil {
		return err
	}
	defer rc.Close()
	dec := json.NewDecoder(rc)
	for {
		var msg struct {
			Error string `json:"error"`
		}
		if err := dec.Decode(&msg); err != nil {
			if err == io.EOF {
				break
			}
			return err
		}
		if msg.Error != "" {
			return fmt.Errorf("%s", msg.Error)
		}
	}
	slog.Info("image pulled", "image", ref)
	return nil
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
