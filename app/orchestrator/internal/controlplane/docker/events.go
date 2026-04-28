package docker

import (
	"context"
	"log/slog"
	"strconv"
	"strings"
	"time"

	dockertypes "github.com/docker/docker/api/types/events"
	"github.com/docker/docker/api/types/filters"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/controlplane/engine"
	"github.com/acestream/acestream/internal/state"
)

// EventWatcher consumes Docker container lifecycle events in a single goroutine.
// This replaces Python's DockerEventWatcher (which used a blocking thread).
type EventWatcher struct {
	pub          *state.RedisPublisher
	ctrl         *engine.Controller
	mon          *Monitor
	reconnDelay  time.Duration
	hasConnected bool
}

// NewEventWatcher creates an EventWatcher backed by a Redis publisher.
func NewEventWatcher(pub *state.RedisPublisher, ctrl *engine.Controller, mon *Monitor) *EventWatcher {
	return &EventWatcher{
		pub:         pub,
		ctrl:        ctrl,
		mon:         mon,
		reconnDelay: 2 * time.Second,
	}
}

// Run streams Docker events until ctx is cancelled, reconnecting automatically.
func (w *EventWatcher) Run(ctx context.Context) {
	slog.Info("DockerEventWatcher started")

	for {
		select {
		case <-ctx.Done():
			slog.Info("DockerEventWatcher stopped")
			return
		default:
		}

		w.consumeEvents(ctx)

		select {
		case <-ctx.Done():
			return
		case <-time.After(w.reconnDelay):
		}
	}
}

func (w *EventWatcher) consumeEvents(ctx context.Context) {
	cli, err := engine.NewDockerClientExported()
	if err != nil {
		slog.Error("docker client unavailable", "err", err)
		return
	}
	defer cli.Close()

	if _, err := cli.Ping(ctx); err != nil {
		slog.Error("docker ping failed", "err", err)
		return
	}

	f := filters.NewArgs()
	f.Add("type", "container")
	for _, ev := range []string{"start", "die", "destroy", "health_status"} {
		f.Add("event", ev)
	}

	msgCh, errCh := cli.Events(ctx, dockertypes.ListOptions{Filters: f})

	// On reconnect after first connection, trigger a reconciliation to catch missed events.
	if w.hasConnected {
		slog.Warn("Docker event stream reconnected; triggering reconciliation")
		if w.mon != nil {
			w.mon.NotifyReindex(ctx) // resets debounce and runs immediately
		} else {
			Reindex(ctx)
		}
		w.ctrl.Nudge("docker_event_stream_reconnected")
	}
	w.hasConnected = true

	slog.Debug("Docker event stream connected")

	for {
		select {
		case <-ctx.Done():
			return
		case err, ok := <-errCh:
			if !ok {
				return
			}
			if err != nil && !strings.Contains(err.Error(), "context canceled") {
				slog.Warn("Docker event stream error; reconnecting", "err", err)
			}
			return
		case msg, ok := <-msgCh:
			if !ok {
				return
			}
			w.handleEvent(ctx, msg)
		}
	}
}

func (w *EventWatcher) handleEvent(ctx context.Context, msg dockertypes.Message) {
	action := strings.ToLower(strings.TrimSpace(string(msg.Action)))
	containerID := msg.Actor.ID
	attrs := msg.Actor.Attributes
	containerName := strings.TrimPrefix(attrs["name"], "/")

	cfg := config.C.Load()
	isManagedEngine := attrs[cfg.ContainerLabelKey] == cfg.ContainerLabelVal
	isManagedVPN := attrs["acestream-orchestrator.managed"] == "true" && attrs["role"] == "vpn_node"
	isDynamicVPN := strings.HasPrefix(strings.ToLower(containerName), "gluetun-dyn-")

	slog.Debug("docker event", "action", action, "container", containerName, "managed_engine", isManagedEngine, "managed_vpn", isManagedVPN)

	switch {
	case strings.HasPrefix(action, "health_status"):
		status := "unknown"
		if strings.Contains(action, "healthy") && !strings.Contains(action, "unhealthy") {
			status = "healthy"
		} else if strings.Contains(action, "unhealthy") {
			status = "unhealthy"
		}
		w.handleHealthStatus(ctx, containerID, containerName, status, attrs, isManagedVPN || isDynamicVPN)

	case action == "start":
		if isManagedEngine {
			w.handleEngineStart(ctx, containerID, containerName, attrs)
		}
		if isManagedVPN || isDynamicVPN {
			w.handleVPNStart(ctx, containerID, containerName, attrs)
		}

	case action == "die" || action == "destroy":
		if isManagedEngine {
			w.handleEngineStop(ctx, containerID, containerName, attrs)
		}
		if isManagedVPN || isDynamicVPN {
			w.handleVPNStop(ctx, containerName)
		}
	}

	// Notify subscribers (used by monitor for periodic reindex)
	w.ctrl.Nudge("docker_event:" + action)
}

func (w *EventWatcher) handleEngineStart(ctx context.Context, containerID, containerName string, attrs map[string]string) {
	st := state.Global
	cfg := config.C.Load()

	host := resolveHost(containerName, attrs)
	// Use IP address for cross-network connectivity when not in VPN mode.
	if attrs["acestream.vpn_container"] == "" {
		if ip := inspectContainerIP(ctx, containerID); ip != "" {
			host = ip
		}
	}
	httpPort := parseInt(attrs["acestream.http_port"])
	apiPort := parseInt(attrs["acestream.api_port"])
	vpnContainer := attrs["acestream.vpn_container"]
	forwarded := attrs["acestream.forwarded"] == "true"

	if httpPort == 0 {
		httpPort = 6878
	}
	if apiPort == 0 {
		apiPort = httpPort
	}

	eng := &state.Engine{
		ContainerID:   containerID,
		ContainerName: containerName,
		Host:          host,
		Port:          httpPort,
		APIPort:       apiPort,
		Labels:        copyAttrs(attrs, cfg.ContainerLabelKey),
		Forwarded:     forwarded,
		VPNContainer:  vpnContainer,
		HealthStatus:  state.HealthUnknown,
	}

	// Reserve ports from label data so allocator state is consistent
	engine.Alloc.ReserveFromLabels(attrs)

	st.AddEngine(eng)
	w.pub.PublishEngine(ctx, eng)
	slog.Info("engine registered", "id", containerID[:min12(len(containerID))], "name", containerName, "host", host, "port", httpPort)
}

func (w *EventWatcher) handleEngineStop(ctx context.Context, containerID, containerName string, attrs map[string]string) {
	if state.Global.RemoveEngine(containerID) {
		engine.Alloc.ReleaseFromLabels(attrs)
		w.pub.RemoveEngine(ctx, containerID)
		slog.Info("engine deregistered", "id", containerID[:min12(len(containerID))], "name", containerName)
	}
}

func (w *EventWatcher) handleVPNStart(ctx context.Context, containerID, containerName string, attrs map[string]string) {
	st := state.Global

	provider := strings.ToLower(strings.TrimSpace(attrs["provider"]))
	node := &state.VPNNode{
		ContainerName:           containerName,
		ContainerID:             containerID,
		Status:                  "running",
		Healthy:                 false, // until health_status:healthy event
		Condition:               "",
		Provider:                provider,
		ManagedDynamic:          strings.HasPrefix(strings.ToLower(containerName), "gluetun-dyn-"),
		PortForwardingSupported: attrs["port_forwarding_supported"] == "true",
		Lifecycle:               "active",
	}

	st.UpsertVPNNode(node)
	w.pub.PublishVPNNode(ctx, node)
	slog.Info("VPN node registered", "name", containerName)
}

func (w *EventWatcher) handleVPNStop(ctx context.Context, containerName string) {
	state.Global.RemoveVPNNode(containerName)
	w.pub.RemoveVPNNode(ctx, containerName)
	slog.Info("VPN node deregistered", "name", containerName)
}

func (w *EventWatcher) handleHealthStatus(ctx context.Context, containerID, containerName, status string, attrs map[string]string, isVPN bool) {
	st := state.Global
	cfg := config.C.Load()

	if isVPN || strings.HasPrefix(strings.ToLower(containerName), "gluetun-dyn-") {
		healthy := status == "healthy"
		st.SetVPNNodeHealthy(containerName, healthy)
		if n, ok := st.GetVPNNode(containerName); ok {
			w.pub.PublishVPNNode(ctx, n)
		}
		slog.Debug("VPN node health updated", "name", containerName, "healthy", healthy)
		return
	}

	if attrs[cfg.ContainerLabelKey] != cfg.ContainerLabelVal {
		return
	}

	healthStatus := state.HealthHealthy
	if status == "unhealthy" {
		healthStatus = state.HealthUnhealthy
	} else if status == "unknown" {
		healthStatus = state.HealthUnknown
	}

	st.UpdateEngineHealth(containerID, healthStatus)
	if e, ok := st.GetEngine(containerID); ok {
		w.pub.PublishEngine(ctx, e)
	}
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

func inspectContainerIP(ctx context.Context, containerID string) string {
	cli, err := engine.NewDockerClientExported()
	if err != nil {
		return ""
	}
	defer cli.Close()
	info, err := cli.ContainerInspect(ctx, containerID)
	if err != nil {
		return ""
	}
	for _, ep := range info.NetworkSettings.Networks {
		if ep != nil && ep.IPAddress != "" {
			return ep.IPAddress
		}
	}
	return ""
}

func resolveHost(containerName string, attrs map[string]string) string {
	// If VPN-networked, the engine is reachable at the VPN container hostname.
	// Otherwise it's reachable at its own container name via Docker DNS.
	vpn := attrs["acestream.vpn_container"]
	if vpn != "" {
		return vpn
	}
	return containerName
}

func parseInt(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}

func min12(n int) int {
	if n < 12 {
		return n
	}
	return 12
}

func copyAttrs(attrs map[string]string, extras ...string) map[string]string {
	out := make(map[string]string, len(attrs))
	for k, v := range attrs {
		out[k] = v
	}
	return out
}
