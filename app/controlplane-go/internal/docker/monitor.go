package docker

import (
	"context"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/filters"

	"github.com/acestream/controlplane/internal/config"
	"github.com/acestream/controlplane/internal/engine"
	"github.com/acestream/controlplane/internal/state"
)

// Monitor periodically polls Docker for container state, reconciling in-memory
// state with reality. This mirrors Python's DockerMonitor._sync_with_docker.
type Monitor struct {
	pub  *state.RedisPublisher
	ctrl *engine.Controller

	mu            sync.Mutex
	lastReindexAt time.Time
	lastKnownIDs  map[string]bool
}

func NewMonitor(pub *state.RedisPublisher, ctrl *engine.Controller) *Monitor {
	return &Monitor{
		pub:          pub,
		ctrl:         ctrl,
		lastKnownIDs: make(map[string]bool),
	}
}

// Run starts the polling loop. It blocks until ctx is cancelled.
func (m *Monitor) Run(ctx context.Context) {
	cfg := config.C
	ticker := time.NewTicker(cfg.MonitorInterval)
	defer ticker.Stop()

	slog.Info("DockerMonitor started", "interval", cfg.MonitorInterval)

	// Immediate sync on startup (no debounce).
	m.reindex(ctx, true)

	for {
		select {
		case <-ctx.Done():
			slog.Info("DockerMonitor stopped")
			return
		case <-ticker.C:
			m.reindex(ctx, false)
		}
	}
}

const debounceInterval = 3 * time.Second

// reindex reconciles in-memory state with Docker.
// When force=false the call is debounced: if a reindex ran within the last
// 3 seconds (e.g. triggered by a Docker event) the tick is skipped, matching
// Python's DockerMonitor debounce behaviour.
func (m *Monitor) reindex(ctx context.Context, force bool) {
	m.mu.Lock()
	if !force && time.Since(m.lastReindexAt) < debounceInterval {
		m.mu.Unlock()
		slog.Debug("DockerMonitor: debouncing rapid reindex")
		return
	}
	m.lastReindexAt = time.Now()
	m.mu.Unlock()

	changed := Reindex(ctx)
	if changed {
		m.ctrl.Nudge("docker_monitor_change")
	} else {
		// No structural change — update last_seen on all tracked engines.
		now := time.Now().UTC()
		for _, e := range state.Global.ListEngines() {
			_ = now
			state.Global.UpdateEngineLastSeen(e.ContainerID)
		}
	}
}

// NotifyReindex is called by the EventWatcher after a reconnect so the monitor
// debounce clock is reset and a full reindex fires immediately.
func (m *Monitor) NotifyReindex(ctx context.Context) {
	m.mu.Lock()
	m.lastReindexAt = time.Time{} // zero — forces next reindex to run
	m.mu.Unlock()
	m.reindex(ctx, true)
}

// Reindex lists all running Docker containers and reconciles in-memory engine
// and VPN-node state with the live container list. Returns true if any
// structural change was detected (engine added or removed).
func Reindex(ctx context.Context) bool {
	cli, err := engine.NewDockerClientExported()
	if err != nil {
		slog.Error("Reindex: docker client unavailable", "err", err)
		return false
	}
	defer cli.Close()

	cfg := config.C

	f := filters.NewArgs()
	f.Add("status", "running")

	containers, err := cli.ContainerList(ctx, container.ListOptions{Filters: f})
	if err != nil {
		if ctx.Err() != nil {
			return false
		}
		slog.Error("Reindex: ContainerList failed", "err", err)
		return false
	}

	st := state.Global
	changed := false

	// Build sets of container IDs currently running.
	runningEngines := make(map[string]bool)
	runningVPNs := make(map[string]bool)

	for _, c := range containers {
		attrs := c.Labels
		containerName := ""
		for _, name := range c.Names {
			containerName = strings.TrimPrefix(name, "/")
			break
		}

		isManagedEngine := attrs[cfg.ContainerLabelKey] == cfg.ContainerLabelVal
		isManagedVPN := attrs["acestream-orchestrator.managed"] == "true" && attrs["role"] == "vpn_node"
		isDynamicVPN := strings.HasPrefix(strings.ToLower(containerName), "gluetun-dyn-")

		if isManagedEngine {
			runningEngines[c.ID] = true
			if _, exists := st.GetEngine(c.ID); !exists {
				host := resolveHost(containerName, attrs)
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
					ContainerID:   c.ID,
					ContainerName: containerName,
					Host:          host,
					Port:          httpPort,
					APIPort:       apiPort,
					Labels:        copyAttrs(attrs, cfg.ContainerLabelKey),
					Forwarded:     forwarded,
					VPNContainer:  vpnContainer,
					HealthStatus:  state.HealthUnknown,
				}
				engine.Alloc.ReserveFromLabels(attrs)
				st.AddEngine(eng)
				slog.Info("Reindex: discovered untracked engine", "name", containerName)
				changed = true
			} else {
				st.UpdateEngineLastSeen(c.ID)
			}
		}

		if isManagedVPN || isDynamicVPN {
			runningVPNs[containerName] = true
			if _, exists := st.GetVPNNode(containerName); !exists {
				provider := strings.ToLower(strings.TrimSpace(attrs["provider"]))
				node := &state.VPNNode{
					ContainerName:           containerName,
					ContainerID:             c.ID,
					Status:                  "running",
					Healthy:                 false,
					Provider:                provider,
					ManagedDynamic:          isDynamicVPN,
					PortForwardingSupported: attrs["port_forwarding_supported"] == "true",
					Lifecycle:               "active",
				}
				st.UpsertVPNNode(node)
				slog.Info("Reindex: discovered untracked VPN node", "name", containerName)
				changed = true
			}
		}
	}

	// Remove stale engines (tracked but no longer running).
	for _, e := range st.ListEngines() {
		if !runningEngines[e.ContainerID] {
			engine.Alloc.ReleaseFromLabels(e.Labels)
			st.RemoveEngine(e.ContainerID)
			slog.Info("Reindex: removed stale engine", "name", e.ContainerName)
			changed = true
		}
	}

	// Remove stale VPN nodes.
	for _, n := range st.ListVPNNodes() {
		if !runningVPNs[n.ContainerName] {
			st.RemoveVPNNode(n.ContainerName)
			slog.Info("Reindex: removed stale VPN node", "name", n.ContainerName)
			changed = true
		}
	}

	return changed
}
