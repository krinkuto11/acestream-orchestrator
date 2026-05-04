package vpn

import (
	"context"
	"log/slog"
	"math"
	"sort"
	"sync"
	"time"

	"github.com/docker/docker/api/types/container"
	dockerclient "github.com/docker/docker/client"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/state"
)

// LifecycleManager handles:
//   - VPN drain → GC (destroys draining nodes once idle or timed-out)
//   - Auto-drain of long-unhealthy dynamic nodes
//   - VPN scaling: compute desired count from engine demand, provision/destroy accordingly
//   - Healing of notready nodes (unhealthy past grace period → drain)
type LifecycleManager struct {
	pub  *state.RedisPublisher
	prov *Provisioner

	activeDrains   sync.Map // containerName -> struct{}
	activeHealings sync.Map
	nudge          chan struct{}
	nudger         func(string)
	wg             sync.WaitGroup
}

// Wait blocks until the Run goroutine has fully exited. Call after the context
// passed to Run has been cancelled to ensure no in-flight reconciliation races
// with container cleanup during shutdown.
func (lm *LifecycleManager) Wait() {
	lm.wg.Wait()
}

func NewLifecycleManager(pub *state.RedisPublisher, prov *Provisioner) *LifecycleManager {
	return &LifecycleManager{
		pub:   pub,
		prov:  prov,
		nudge: make(chan struct{}, 1),
	}
}

func (lm *LifecycleManager) SetNudger(f func(string)) {
	lm.nudger = f
}

func (lm *LifecycleManager) Nudge(reason string) {
	select {
	case lm.nudge <- struct{}{}:
		slog.Info("VPN lifecycle nudged", "reason", reason)
	default:
	}
}

func (lm *LifecycleManager) Run(ctx context.Context) {
	lm.wg.Add(1)
	defer lm.wg.Done()

	cfg := config.C.Load()
	ticker := time.NewTicker(cfg.VPNControllerInterval)
	defer ticker.Stop()
	healthTicker := time.NewTicker(cfg.GluetunHealthCheckInterval)
	defer healthTicker.Stop()

	slog.Info("VPNLifecycleManager started",
		"check_interval", cfg.VPNControllerInterval,
		"health_interval", cfg.GluetunHealthCheckInterval,
		"vpn_enabled", cfg.VPNEnabled,
	)

	// Immediate sync on startup.
	lm.reconcile(ctx)

	for {
		select {
		case <-ctx.Done():
			slog.Info("VPNLifecycleManager stopped")
			return
		case <-ticker.C:
			lm.reconcile(ctx)
		case <-healthTicker.C:
			lm.monitorHealth(ctx)
		case <-lm.nudge:
			lm.reconcile(ctx)
		}
	}
}

func (lm *LifecycleManager) reconcile(ctx context.Context) {
	cfg := config.C.Load()

	// Phase 1: heal unhealthy nodes.
	lm.healNotReady(ctx)

	// Phase 2: scale up/down if VPN provisioning is enabled.
	if cfg.VPNEnabled && lm.prov != nil {
		lm.reconcileScale(ctx)
	}

	// Phase 3: auto-drain dynamic nodes that have been unhealthy too long.
	for _, node := range state.Global.ListVPNNodes() {
		if node.Lifecycle == "draining" {
			lm.gcDraining(ctx, node)
			continue
		}
		if !node.Healthy && node.ManagedDynamic && node.UnhealthySince != nil {
			if time.Since(*node.UnhealthySince) > cfg.VPNUnhealthyGracePeriod {
				slog.Info("VPN node unhealthy past grace; auto-draining",
					"name", node.ContainerName,
					"since", node.UnhealthySince,
				)
				state.RecordEvent(state.EventEntry{
					EventType: "vpn",
					Category:  "draining",
					Message:   "VPN node unhealthy past grace; auto-draining",
					Details: map[string]any{
						"name": node.ContainerName,
					},
				})
				if state.Global.SetVPNNodeDraining(node.ContainerName) {
					lm.pub.PublishVPNNode(ctx, node)
				}
			}
		}
	}
}

// reconcileScale computes the desired VPN count and provisions/tears down nodes.
func (lm *LifecycleManager) reconcileScale(ctx context.Context) {
	cfg := config.C.Load()
	st := state.Global

	// Sync Docker-running managed nodes into state first.
	if err := lm.syncManagedNodesToState(ctx); err != nil {
		slog.Warn("Failed to sync VPN node state from Docker", "err", err)
	}

	// Compute desired VPN count from engine demand.
	totalVPNEngines := countVPNEngines()
	desiredEngines := st.GetDesiredReplicas()
	engineDemand := max(totalVPNEngines, desiredEngines)

	var desiredVPNs int
	preferred := cfg.PreferredEnginesPerVPN
	if preferred <= 0 {
		preferred = 4
	}
	if engineDemand > 0 {
		// Use a utilization target of 80% to scale up infrastructure BEFORE
		// we hit 100% capacity. This ensures Node 2 is provisioning while
		// Node 1 is still absorbing the burst.
		const utilizationTarget = 0.8
		effectivePreferred := float64(preferred) * utilizationTarget
		if effectivePreferred < 1 {
			effectivePreferred = 1
		}
		desiredVPNs = int(math.Ceil(float64(engineDemand) / effectivePreferred))
	}
	// Cap by available credential slots.
	if lm.prov != nil {
		available := lm.prov.creds.TotalCount()
		if desiredVPNs > available {
			desiredVPNs = available
		}
	}

	// Count active (non-draining) dynamic nodes.
	actualVPNs := st.CountActiveDynamicVPNNodes()

	slog.Info("VPN scaling math",
		"engine_demand", engineDemand,
		"preferred_per_vpn", preferred,
		"desired_vpns", desiredVPNs,
		"actual_vpns", actualVPNs,
	)

	if lm.prov != nil {
		total := lm.prov.creds.TotalCount()
		if desiredVPNs > total && total > 0 {
			slog.Warn("VPN scaling capped by credential limits", "desired", desiredVPNs, "total_creds", total)
			desiredVPNs = total
		}
	}

	switch {
	case actualVPNs < desiredVPNs:
		deficit := desiredVPNs - actualVPNs
		available := lm.prov.creds.AvailableCount()
		slog.Info("VPN scale-up required", "deficit", deficit, "available_creds", available)
		budget := deficit
		if available < budget {
			budget = available
		}
		for i := 0; i < budget; i++ {
			lm.provisionOne(ctx)
		}
	case actualVPNs > desiredVPNs:
		lm.scaleDownIdle(ctx, desiredVPNs)
	}
}

func (lm *LifecycleManager) provisionOne(ctx context.Context) {
	slog.Info("Provisioning dynamic VPN node")
	if _, err := lm.prov.ProvisionNode(ctx); err != nil {
		slog.Error("Failed to provision dynamic VPN node", "err", err)
		state.RecordEvent(state.EventEntry{
			EventType: "vpn",
			Category:  "provision_failed",
			Message:   "VPN node provisioning failed",
			Details: map[string]any{
				"error": err.Error(),
			},
		})
		return
	}
	state.RecordEvent(state.EventEntry{
		EventType: "vpn",
		Category:  "provisioned",
		Message:   "VPN node provisioned",
	})
}

func (lm *LifecycleManager) scaleDownIdle(ctx context.Context, desiredVPNs int) {
	st := state.Global
	nodes := st.ListDynamicVPNNodes()

	// Proactive compaction cooldown: do not scale down nodes that were
	// recently provisioned. This prevents "tug-of-war" churn where a node
	// is killed 10s after it was created during a burst.
	const scaleDownCooldown = 120 * time.Second
	var active []*state.VPNNode
	for _, n := range nodes {
		if n.Lifecycle == "draining" {
			continue
		}
		// If the node is very new, protect it from scale-down.
		if n.HealthySince != nil && time.Since(*n.HealthySince) < scaleDownCooldown {
			continue
		}
		active = append(active, n)
	}

	if len(active) == 0 || (len(nodes)-len(active)) >= desiredVPNs {
		// All nodes that COULD be scaled down are already needed, or
		// we are protecting everything from scale-down to let it settle.
		return
	}

	// Proactive compaction: sort nodes by their "disruption score".
	// We prioritize nodes with zero active streams, then nodes with the fewest engines.
	type scored struct {
		node        *state.VPNNode
		streamCount int
		engineCount int
	}
	var ss []scored
	streamCounts := st.GetAllStreamCounts()
	monitorCounts := st.GetAllMonitorCounts()

	for _, n := range active {
		engines := st.GetEnginesByVPN(n.ContainerName)
		totalStreams := 0
		for _, e := range engines {
			totalStreams += streamCounts[e.ContainerID] + monitorCounts[e.ContainerID]
		}
		ss = append(ss, scored{
			node:        n,
			streamCount: totalStreams,
			engineCount: len(engines),
		})
	}

	sort.Slice(ss, func(i, j int) bool {
		if ss[i].streamCount != ss[j].streamCount {
			return ss[i].streamCount < ss[j].streamCount
		}
		return ss[i].engineCount < ss[j].engineCount
	})

	excess := len(active) - desiredVPNs
	slog.Info("VPN cluster over-balanced; initiating compaction",
		"active", len(active),
		"desired", desiredVPNs,
		"excess", excess,
	)
	state.RecordEvent(state.EventEntry{
		EventType: "vpn",
		Category:  "compaction",
		Message:   "VPN cluster over-balanced; initiating compaction",
		Details: map[string]any{
			"active":  len(active),
			"desired": desiredVPNs,
			"excess":  excess,
		},
	})

	for i := 0; i < excess; i++ {
		node := ss[i].node
		slog.Info("Scaling down VPN node (compaction)",
			"name", node.ContainerName,
			"active_streams", ss[i].streamCount,
			"engines", ss[i].engineCount,
		)
		state.RecordEvent(state.EventEntry{
			EventType: "vpn",
			Category:  "draining",
			Message:   "Scaling down VPN node (compaction)",
			Details: map[string]any{
				"name":           node.ContainerName,
				"active_streams": ss[i].streamCount,
				"engines":        ss[i].engineCount,
			},
		})
		if st.SetVPNNodeDraining(node.ContainerName) {
			lm.pub.PublishVPNNode(ctx, node)
		}
	}
}

// healNotReady marks dynamic VPN nodes as draining if they have been notready
// (unhealthy + not yet draining) past the heal grace period.
func (lm *LifecycleManager) healNotReady(ctx context.Context) {
	cfg := config.C.Load()
	for _, node := range state.Global.ListNotReadyVPNNodes() {
		name := node.ContainerName
		if _, alreadyHealing := lm.activeHealings.Load(name); alreadyHealing {
			continue
		}
		// Grace: only heal if last-seen is past the configured threshold.
		if time.Since(node.LastSeen) < cfg.VPNHealGracePeriod {
			continue
		}
		lm.activeHealings.Store(name, struct{}{})
		slog.Info("VPN node notready past grace period; draining", "name", name)
		state.RecordEvent(state.EventEntry{
			EventType: "vpn",
			Category:  "draining",
			Message:   "VPN node notready past grace period; draining",
			Details: map[string]any{
				"name": name,
			},
		})
		if state.Global.SetVPNNodeDraining(name) {
			lm.pub.PublishVPNNode(ctx, node)
		}
		lm.activeHealings.Delete(name)
	}
}

// gcDraining checks whether all engines attached to a draining VPN node are
// idle, then stops and removes the VPN container.
func (lm *LifecycleManager) gcDraining(ctx context.Context, node *state.VPNNode) {
	if _, active := lm.activeDrains.Load(node.ContainerName); active {
		return
	}
	lm.activeDrains.Store(node.ContainerName, struct{}{})
	defer lm.activeDrains.Delete(node.ContainerName)

	st := state.Global
	cfg := config.C.Load()

	engines := st.GetEnginesByVPN(node.ContainerName)
	streamCounts := st.GetAllStreamCounts()
	monitorCounts := st.GetAllMonitorCounts()

	for _, e := range engines {
		st.MarkEngineDraining(e.ContainerID, "vpn_draining")
	}

	allIdle := true
	for _, e := range engines {
		if streamCounts[e.ContainerID]+monitorCounts[e.ContainerID] > 0 {
			allIdle = false
			break
		}
	}

	hardTimeout := node.DrainingSince != nil && time.Since(*node.DrainingSince) > cfg.VPNDrainingHardTimeout
	if !allIdle && !hardTimeout {
		return
	}

	if hardTimeout && !allIdle {
		slog.Warn("VPN drain hard timeout; force-stopping",
			"name", node.ContainerName,
		)
		state.RecordEvent(state.EventEntry{
			EventType: "vpn",
			Category:  "force_stop",
			Message:   "VPN drain hard timeout; force-stopping",
			Details: map[string]any{
				"name": node.ContainerName,
			},
		})
	}

	lm.destroyVPN(ctx, node)
}

func (lm *LifecycleManager) destroyVPN(ctx context.Context, node *state.VPNNode) {
	slog.Info("Destroying draining VPN node", "name", node.ContainerName)
	state.RecordEvent(state.EventEntry{
		EventType: "vpn",
		Category:  "destroying",
		Message:   "Destroying draining VPN node",
		Details: map[string]any{
			"name": node.ContainerName,
		},
	})

	if lm.prov != nil {
		if err := lm.prov.DestroyNode(ctx, node.ContainerName); err != nil {
			slog.Error("Failed to destroy VPN node", "name", node.ContainerName, "err", err)
			state.RecordEvent(state.EventEntry{
				EventType: "vpn",
				Category:  "destroy_failed",
				Message:   "Failed to destroy VPN node",
				Details: map[string]any{
					"name":  node.ContainerName,
					"error": err.Error(),
				},
			})
		}
	} else {
		// Fallback: direct Docker stop (no credential management).
		if err := stopVPNContainer(ctx, node.ContainerID); err != nil {
			slog.Error("Failed to stop VPN container", "name", node.ContainerName, "err", err)
			state.RecordEvent(state.EventEntry{
				EventType: "vpn",
				Category:  "destroy_failed",
				Message:   "Failed to stop VPN container",
				Details: map[string]any{
					"name":  node.ContainerName,
					"error": err.Error(),
				},
			})
		}
	}

	state.Global.RemoveEnginesByVPN(node.ContainerName)
	state.Global.RemoveVPNNode(node.ContainerName)
	lm.pub.RemoveVPNNode(ctx, node.ContainerName)
	slog.Info("VPN node destroyed", "name", node.ContainerName)
	state.RecordEvent(state.EventEntry{
		EventType: "vpn",
		Category:  "destroyed",
		Message:   "VPN node destroyed",
		Details: map[string]any{
			"name": node.ContainerName,
		},
	})
}

// monitorHealth probes all active dynamic VPN nodes and updates their health in state.
func (lm *LifecycleManager) monitorHealth(ctx context.Context) {
	st := state.Global
	for _, node := range st.ListVPNNodes() {
		if !node.ManagedDynamic || node.Lifecycle == "draining" {
			continue
		}
		// Probe Gluetun API.
		healthy := IsControlAPIReachable(node.ContainerName, true)
		if node.Healthy != healthy {
			slog.Info("VPN node health changed", "name", node.ContainerName, "healthy", healthy)
			st.SetVPNNodeHealthy(node.ContainerName, healthy)
			state.RecordEvent(state.EventEntry{
				EventType: "vpn",
				Category:  "health",
				Message:   "VPN node health changed",
				Details: map[string]any{
					"name":    node.ContainerName,
					"healthy": healthy,
				},
			})
			// Publish update so proxy event stream reflects health.
			if updated, ok := st.GetVPNNode(node.ContainerName); ok {
				lm.pub.PublishVPNNode(ctx, updated)
			}
			if healthy && lm.nudger != nil {
				lm.nudger("vpn_node_healthy")
			}
		}
	}
}

// syncManagedNodesToState reconciles Docker-reported managed VPN containers into
// the in-memory state store.
func (lm *LifecycleManager) syncManagedNodesToState(ctx context.Context) error {
	if lm.prov == nil {
		return nil
	}
	nodes, err := lm.prov.ListManagedNodes(ctx, true)
	if err != nil {
		return err
	}

	st := state.Global
	observed := make(map[string]bool)
	now := time.Now().UTC()

	for _, n := range nodes {
		name, _ := n["container_name"].(string)
		if name == "" {
			continue
		}
		observed[name] = true

		existing, exists := st.GetVPNNode(name)
		if exists {
			// Update status/LastSeen but don't overwrite lifecycle/health state.
			existing.Status, _ = n["status"].(string)
			existing.LastSeen = now
			continue
		}

		// New node observed from Docker — register it.
		provider, _ := n["provider"].(string)
		protocol, _ := n["protocol"].(string)
		credID, _ := n["credential_id"].(string)
		pfSupported, _ := n["port_forwarding_supported"].(bool)
		containerID, _ := n["container_id"].(string)

		st.UpsertVPNNode(&state.VPNNode{
			ContainerName:           name,
			ContainerID:             containerID,
			Status:                  strVal(n["status"]),
			Provider:                provider,
			Protocol:                protocol,
			CredentialID:            credID,
			ManagedDynamic:          true,
			PortForwardingSupported: pfSupported,
			FirstSeen:               now,
			LastSeen:                now,
		})
	}

	// Mark nodes gone from Docker as down.
	for _, node := range st.ListDynamicVPNNodes() {
		if !observed[node.ContainerName] && node.Lifecycle != "draining" {
			node.Status = "down"
			node.LastSeen = now
		}
	}

	return nil
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func countVPNEngines() int {
	n := 0
	for _, e := range state.Global.ListEngines() {
		if e.VPNContainer != "" && !e.Draining {
			n++
		}
	}
	return n
}

// stopVPNContainer is the fallback when no Provisioner is available.
func stopVPNContainer(ctx context.Context, containerID string) error {
	if containerID == "" {
		return nil
	}
	cli, err := dockerclient.NewClientWithOpts(
		dockerclient.FromEnv,
		dockerclient.WithAPIVersionNegotiation(),
	)
	if err != nil {
		return err
	}
	defer cli.Close()
	timeout := 15
	return cli.ContainerStop(ctx, containerID, container.StopOptions{Timeout: &timeout})
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
