package engine

import (
	"context"
	"fmt"
	"log/slog"
	"math"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/acestream/acestream/internal/controlplane/circuitbreaker"
	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/metrics"
	"github.com/acestream/acestream/internal/state"
)

type intentAction string

const (
	intentCreate    intentAction = "create"
	intentTerminate intentAction = "terminate"
)

type intent struct {
	id      string
	action  intentAction
	spec    *state.EngineSpec // non-nil for create
	contID  string            // non-nil for terminate
}

// Controller is the Go equivalent of Python's EngineController.
// It runs two goroutines: a reconciler and an intent worker.
type Controller struct {
	cb       *circuitbreaker.Manager
	intents  chan intent
	nudge    chan struct{}
	running  atomic.Bool
	wg       sync.WaitGroup
	intentMu sync.Mutex
	intSeq   atomic.Int64
	vpnNudge func(string)

	lastScaleUp time.Time
}

// NewController creates a new EngineController.
func NewController(cb *circuitbreaker.Manager) *Controller {
	return &Controller{
		cb:      cb,
		intents: make(chan intent, 1024), // Increased buffer for bursts
		nudge:   make(chan struct{}, 1),
	}
}

func (c *Controller) Start(ctx context.Context) {
	if c.running.Swap(true) {
		return
	}
	slog.Info("EngineController started")
	
	numWorkers := 10 // Worker pool size
	c.wg.Add(1 + numWorkers)
	go c.reconcilerLoop(ctx)
	for i := 0; i < numWorkers; i++ {
		go c.intentWorker(ctx)
	}
}

func (c *Controller) Stop() {
	c.running.Store(false)
	c.wg.Wait()
	slog.Info("EngineController stopped")
}

// Nudge triggers an immediate reconciliation pass.
func (c *Controller) Nudge(reason string) {
	slog.Debug("reconciliation nudged", "reason", reason)
	select {
	case c.nudge <- struct{}{}:
	default:
	}
}

func (c *Controller) IsRunning() bool { return c.running.Load() }

func (c *Controller) SetVPNNudger(f func(string)) {
	c.vpnNudge = f
}

// ─── Autoscaler ───────────────────────────────────────────────────────────────

// EnsureMinimum computes the desired replica count and nudges the controller.
// This is the Go equivalent of Python's ensure_minimum().
func (c *Controller) EnsureMinimum() {
	cfg := config.C.Load()
	if cfg.ManualMode {
		slog.Debug("autoscaler paused: manual mode is enabled")
		return
	}
	st := state.Global

	engines := st.ListEngines()
	if len(engines) == 0 {
		st.SetDesiredReplicas(max(0, cfg.MinReplicas))
		c.Nudge("ensure_minimum_no_engines")
		return
	}

	// Compute free engines (healthy/unknown, not draining, not actively used).
	// Monitor sessions count as load alongside active streams.
	streamCounts := st.GetAllStreamCounts()
	monitorCounts := st.GetAllMonitorCounts()
	var freeCount, totalRunning int
	for _, e := range engines {
		if e.HealthStatus == state.HealthUnhealthy || e.Draining {
			continue
		}
		totalRunning++
		load := streamCounts[e.ContainerID] + monitorCounts[e.ContainerID]
		if load == 0 {
			freeCount++
		}
	}

	desired, _ := computeDesiredReplicas(totalRunning, freeCount, streamCounts, monitorCounts, engines)
	prev := st.GetDesiredReplicas()
	pending := st.TotalPending()

	// Never silently undo a NudgeDemand bump while provisioning is in flight.
	// We protect the target if we are still actively starting engines.
	if pending > 0 {
		minTarget := totalRunning + pending
		if desired < minTarget && prev >= minTarget {
			desired = minTarget
		}
	}
	st.SetDesiredReplicas(desired)

	if desired != prev {
		slog.Info("desired replicas updated", "previous", prev, "new", desired, "total", totalRunning, "free", freeCount)
		if desired > prev {
			c.lastScaleUp = time.Now()
			if c.vpnNudge != nil {
				c.vpnNudge("engine_scale_up")
			}
		}
	}
	metrics.CPDesiredReplicas.Set(float64(desired))
	c.Nudge("ensure_minimum")
}

func computeDesiredReplicas(totalRunning, freeCount int, streamCounts, monitorCounts map[string]int, engines []*state.Engine) (int, string) {
	cfg := config.C.Load()

	// Base requirement: engines currently serving streams or monitor sessions.
	occupiedCount := totalRunning - freeCount
	desired := occupiedCount + cfg.MinFreeReplicas

	idleDeficit := max(0, cfg.MinFreeReplicas-freeCount)
	desc := fmt.Sprintf("load-based scaling (occupied %d, buffer %d, missing %d)", occupiedCount, cfg.MinFreeReplicas, idleDeficit)

	// Lookahead: if ANY engine is near stream capacity, pre-emptively scale out.
	// Mirrors Python autoscaler._compute_desired_replicas exactly.
	st := state.Global
	threshold := cfg.MaxStreamsPerEngine - 1
	var loadList []int
	for _, e := range engines {
		// Use stream load only for lookahead (monitor sessions are overhead, not stream slots)
		loadList = append(loadList, streamCounts[e.ContainerID])
	}
	if len(loadList) > 0 {
		anyNear := false
		minLoad := loadList[0]
		for _, s := range loadList {
			if s >= threshold {
				anyNear = true
			}
			if s < minLoad {
				minLoad = s
			}
		}
		lookahead := st.GetLookaheadLayer()

		if anyNear {
			// Scale out if no lookahead layer is set yet, or we've climbed past the last threshold.
			if lookahead == nil || minLoad >= *lookahead {
				numNear := 0
				for _, s := range loadList {
					if s >= threshold {
						numNear++
					}
				}
				// Proactive burst: add one replica for each engine that is near capacity.
				// Clamp to a maximum of 2 new replicas per cycle to avoid slamming Docker/VPN network.
				maxLookaheadBurst := 2
				burst := min(numNear, maxLookaheadBurst)
				lookaheadDesired := totalRunning + burst
				if lookaheadDesired > desired {
					desired = lookaheadDesired
				}
				desc = fmt.Sprintf("lookahead triggered (%d engines near capacity, adding burst of %d, threshold %d)", numNear, burst, threshold)
				st.SetLookaheadLayer(minLoad)
			}
		} else if lookahead != nil && minLoad < *lookahead {
			st.ResetLookaheadLayer()
		}
	}

	// Clamp to configured min/max
	desired = max(cfg.MinReplicas, min(desired, cfg.MaxReplicas))
	return desired, desc
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// NudgeDemand is called by the proxy when a request finds no eligible engine.
// It increments desiredReplicas by n (clamped to MaxReplicas) and triggers an
// immediate reconciliation pass so a new engine is provisioned without waiting
// for the next autoscaler tick.
func (c *Controller) NudgeDemand(n int) {
	cfg := config.C.Load()
	if cfg.ManualMode {
		return
	}
	newDesired := state.Global.BumpDesiredReplicas(n, cfg.MaxReplicas)
	metrics.CPDesiredReplicas.Set(float64(newDesired))
	slog.Info("demand nudge; scaling UP", "n", n, "desired", newDesired)
	c.lastScaleUp = time.Now()
	if c.vpnNudge != nil {
		c.vpnNudge("demand_spike")
	}
	c.Nudge("demand_spike")
}

// ScaleTo explicitly sets the desired replica count and triggers reconciliation.
func (c *Controller) ScaleTo(n int) {
	cfg := config.C.Load()
	desired := max(cfg.MinReplicas, min(n, cfg.MaxReplicas))
	st := state.Global
	prev := st.GetDesiredReplicas()
	st.SetDesiredReplicas(desired)
	metrics.CPDesiredReplicas.Set(float64(desired))
	slog.Info("manual scale request", "count", desired)
	if desired > prev {
		c.lastScaleUp = time.Now()
		if c.vpnNudge != nil {
			c.vpnNudge("manual_scale")
		}
	}
	c.Nudge("scale_to")
}

// ─── Reconciler loop ─────────────────────────────────────────────────────────

func (c *Controller) reconcilerLoop(ctx context.Context) {
	defer c.wg.Done()
	ticker := time.NewTicker(config.C.Load().AutoscaleInterval)
	defer ticker.Stop()

	var lastReconcileAt time.Time

	for c.running.Load() {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		case <-c.nudge:
		}

		if !c.running.Load() {
			return
		}
		if time.Since(lastReconcileAt) < time.Second {
			continue
		}
		lastReconcileAt = time.Now()
		metrics.CPReconcileTotal.Inc()
		c.doReconcile(ctx)
	}
}

func (c *Controller) doReconcile(ctx context.Context) {
	cfg := config.C.Load()
	if cfg.ManualMode {
		return
	}
	st := state.Global

	desired := st.GetDesiredReplicas()
	engines := st.ListEngines()

	// Only managed (non-manual-label) engines count
	var managed []*state.Engine
	for _, e := range engines {
		if e.Labels["manual"] != "true" {
			managed = append(managed, e)
		}
	}

	// Active = healthy or unknown, not draining
	var active []*state.Engine
	for _, e := range managed {
		if (e.HealthStatus == state.HealthHealthy || e.HealthStatus == state.HealthUnknown) && !e.Draining {
			active = append(active, e)
		}
	}

	actual := len(active)
	targetHash := ComputeConfigHash()
	for _, e := range active {
		if e.Labels["acestream.config_hash"] != targetHash {
			slog.Info("engine configuration drift detected; marking for replacement", "name", e.ContainerName, "current_hash", e.Labels["acestream.config_hash"], "target_hash", targetHash)
			st.MarkEngineDraining(e.ContainerID, "config_drift")
			actual--
		}
	}
	for _, it := range st.ListIntents() {
		if it.Action == "create" {
			actual++
		}
	}
	deficit := desired - actual

	// 1. Scale UP
	if deficit > 0 {
		canCreate := max(0, cfg.MaxReplicas-len(engines))
		createCount := min(deficit, canCreate)
		if createCount > 0 {
			// Pre-flight: check VPN schedulability before spawning N goroutines.
			// This keeps the log clean — one line instead of N identical "blocked" lines.
			if err := Scheduler.CheckVPNSchedulable(); err != nil {
				if isTransientVPNError(err) {
					slog.Info("create intent blocked awaiting VPN readiness", "err", err)
					if c.vpnNudge != nil {
						c.vpnNudge("engine_blocked_by_vpn")
					}
				} else {
					slog.Error("unexpected scheduling error", "err", err)
				}
			} else {
				slog.Info("scaling UP", "deficit", deficit, "creating", createCount)
				// Pre-register placeholder intents so the next reconcile cycle
				// counts them as in-flight and doesn't over-provision. The
				// container_name is filled in once ScheduleNewEngine returns.
				// Goroutines are fire-and-forget; no wg.Wait() so the reconciler
				// is never blocked by a slow WaitForForwardedPort call.
				for i := 0; i < createCount; i++ {
					id := c.nextIntentID()
					st.AddIntent(&state.ScalingIntent{
						ID:     id,
						Action: "create",
						Status: "scheduling",
						Details: map[string]any{},
					})
					go func(intentID string) {
						if !c.cb.CanProvision("general") {
							slog.Warn("circuit breaker OPEN - provisioning blocked")
							st.RemoveIntent(intentID)
							return
						}
						spec, err := Scheduler.ScheduleNewEngine(ctx)
						if err != nil {
							if isTransientVPNError(err) {
								slog.Debug("create intent blocked", "err", err)
							} else {
								slog.Error("unexpected scheduling error", "err", err)
							}
							st.RemoveIntent(intentID)
							return
						}
						st.UpdateIntentDetails(intentID, map[string]any{
							"container_name": spec.ContainerName,
						})
						select {
						case c.intents <- intent{id: intentID, action: intentCreate, spec: spec}:
							metrics.CPIntentQueueDepth.Inc()
						default:
							slog.Warn("intent queue full, dropping create intent")
							st.RemoveIntent(intentID)
							ReleaseSpec(spec)
						}
					}(id)
				}
			}
		}
	}

	// 2. Scale DOWN
	if deficit < 0 {
		surplus := -deficit
		candidates := c.selectTerminationCandidates(active, surplus)
		if len(candidates) > 0 {
			slog.Info("scaling DOWN", "surplus", surplus, "terminating", len(candidates))
			for _, cid := range candidates {
				id := c.nextIntentID()
				st.AddIntent(&state.ScalingIntent{
					ID:     id,
					Action: "terminate",
					Status: "pending",
					Details: map[string]any{
						"container_id": cid,
					},
				})
				select {
				case c.intents <- intent{id: id, action: intentTerminate, contID: cid}:
					metrics.CPIntentQueueDepth.Inc()
				default:
					slog.Warn("intent queue full, dropping terminate intent")
					st.RemoveIntent(id)
				}
			}
		}
	}

	// 3. Density rebalancing & headless VPN correction
	c.rebalanceDensity(active, managed, desired)

	// 4. Clean up idle draining engines
	for _, e := range managed {
		if !e.Draining {
			continue
		}
		load := st.GetEngineTotalLoad(e.ContainerID)
		if load == 0 && canStopEngine(e.ContainerID, false) {
			id := c.nextIntentID()
			select {
			case c.intents <- intent{id: id, action: intentTerminate, contID: e.ContainerID}:
				metrics.CPIntentQueueDepth.Inc()
			default:
			}
		}
	}

	// Update engine metrics
	var healthy, unhealthy, unknown int
	for _, e := range engines {
		switch e.HealthStatus {
		case state.HealthHealthy:
			healthy++
		case state.HealthUnhealthy:
			unhealthy++
		default:
			unknown++
		}
	}
	metrics.CPEnginesTotal.WithLabelValues("healthy").Set(float64(healthy))
	metrics.CPEnginesTotal.WithLabelValues("unhealthy").Set(float64(unhealthy))
	metrics.CPEnginesTotal.WithLabelValues("unknown").Set(float64(unknown))

	var vpnHealthy, vpnDraining, vpnUnhealthy int
	for _, n := range state.Global.ListVPNNodes() {
		switch {
		case n.Lifecycle == "draining":
			vpnDraining++
		case n.Healthy:
			vpnHealthy++
		default:
			vpnUnhealthy++
		}
	}
	metrics.CPVPNNodesTotal.WithLabelValues("healthy").Set(float64(vpnHealthy))
	metrics.CPVPNNodesTotal.WithLabelValues("draining").Set(float64(vpnDraining))
	metrics.CPVPNNodesTotal.WithLabelValues("unhealthy").Set(float64(vpnUnhealthy))
}

func (c *Controller) rebalanceDensity(active, managed []*state.Engine, _ int) {
	cfg := config.C.Load()
	st := state.Global

	maxPerVPN := cfg.PreferredEnginesPerVPN

	// Use the latest desired replicas from state, not the one passed in,
	// to ensure we aren't rebalancing based on a stale decision from the
	// beginning of the reconcile loop.
	desired := st.GetDesiredReplicas()
	if desired <= 0 {
		return
	}

	allVPNNodes := make(map[string]*state.VPNNode)
	for _, n := range st.ListVPNNodes() {
		if n.ManagedDynamic {
			allVPNNodes[n.ContainerName] = n
		}
	}

	readyCount := 0
	for _, n := range allVPNNodes {
		if n.Healthy && !st.IsVPNNodeDraining(n.ContainerName) {
			readyCount++
		}
	}

	requiredNodes := int(math.Ceil(float64(desired) / float64(maxPerVPN)))
	
	// The ideal limit if all required nodes were present.
	idealLimit := int(math.Ceil(float64(desired) / float64(max(1, requiredNodes))))
	if idealLimit > maxPerVPN {
		idealLimit = maxPerVPN
	}

	// The actual fair share given the currently ready nodes.
	actualFairShare := int(math.Ceil(float64(desired) / float64(max(1, readyCount))))

	// We use the higher of the two to prevent thrashing when nodes are still provisioning.
	effectiveLimit := max(idealLimit, actualFairShare)

	// Only enforce the hard max limit if we actually have enough ready VPN nodes
	// to satisfy the total desired engine count. Clamping below the fair-share
	// while nodes are missing triggers a "tug-of-war" loop with the autoscaler.
	if cfg.MaxEnginesPerVPN > 0 && effectiveLimit > cfg.MaxEnginesPerVPN && readyCount >= requiredNodes {
		effectiveLimit = cfg.MaxEnginesPerVPN
	}

	// Group active engines by VPN. Only count Healthy engines for density
	// rebalancing to avoid killing starting engines during burst scaling.
	activeByVPN := make(map[string][]*state.Engine)
	for _, e := range active {
		if e.VPNContainer != "" && e.HealthStatus == state.HealthHealthy {
			activeByVPN[e.VPNContainer] = append(activeByVPN[e.VPNContainer], e)
		}
	}

	// Skip density rebalancing entirely if the VPN cluster is scaling up.
	// This prevents the rebalancer from killing soft-overflow engines that
	// were successfully placed while waiting for new nodes to provision.
	skipDensityRebalancing := requiredNodes > 0 && readyCount < requiredNodes

	// Also skip while any VPN node is draining. A drain compacts engines off
	// that node; starting a parallel density rebalance on another node would
	// double the disruption and can trigger a cascade.
	if !skipDensityRebalancing {
		for _, n := range allVPNNodes {
			if st.IsVPNNodeDraining(n.ContainerName) {
				skipDensityRebalancing = true
				break
			}
		}
	}

	// Stabilization window: if ANY ready node has been healthy for less than
	// rebalanceStabilizeFor, skip rebalancing entirely.
	const rebalanceStabilizeFor = 5 * time.Second
	if !skipDensityRebalancing {
		for _, n := range allVPNNodes {
			if st.IsVPNNodeDraining(n.ContainerName) {
				continue
			}
			if n.HealthySince != nil && time.Since(*n.HealthySince) < rebalanceStabilizeFor {
				// EXCEPTION: If we have all required infrastructure ready, we MUST
				// rebalance to provide relief to over-saturated nodes.
				if readyCount < requiredNodes {
					skipDensityRebalancing = true
					break
				}
			}
		}
	}

	// Cluster-level stabilization: do not rebalance for 10s after a scale-up
	// to prevent thrashing.
	if !skipDensityRebalancing && !c.lastScaleUp.IsZero() && time.Since(c.lastScaleUp) < 10*time.Second {
		// EXCEPTION: Relief hatch for ready infrastructure.
		if readyCount < requiredNodes {
			skipDensityRebalancing = true
		}
	}

	if skipDensityRebalancing {
		return
	}

	for vpnName, vpnEngines := range activeByVPN {
		// Only rebalance if we have more than one engine on this node.
		if len(vpnEngines) <= 1 {
			continue
		}

		if len(vpnEngines) > effectiveLimit {
			// Check if rebalance already in progress
			allOnNode := st.GetEnginesByVPN(vpnName)
			alreadyDraining := false
			for _, e := range allOnNode {
				if st.IsEngineDraining(e.ContainerID) {
					alreadyDraining = true
					break
				}
			}
			if alreadyDraining {
				continue
			}

			excess := len(vpnEngines) - effectiveLimit
			// Drain non-forwarded followers with least workload first
			var followers []*state.Engine
			for _, e := range vpnEngines {
				if !e.Forwarded {
					followers = append(followers, e)
				}
			}
			if len(followers) == 0 {
				continue
			}
			sort.Slice(followers, func(i, j int) bool {
				return st.GetEngineTotalLoad(followers[i].ContainerID) < st.GetEngineTotalLoad(followers[j].ContainerID)
			})

			// Only drain engines that have been around long enough to be stable (> 10s)
			// to avoid killing engines that were just created during a burst.
			var stableFollowers []*state.Engine
			for _, e := range followers {
				if time.Since(e.FirstSeen) > 10*time.Second {
					stableFollowers = append(stableFollowers, e)
				}
			}

			toDrain := stableFollowers
			if len(toDrain) > excess {
				toDrain = toDrain[:excess]
			}
			if len(toDrain) > 0 {
				slog.Info("VPN node over-balanced; rebalancing", "vpn", vpnName, "active", len(vpnEngines), "limit", effectiveLimit, "draining", len(toDrain))
				for _, e := range toDrain {
					st.MarkEngineDraining(e.ContainerID, "density_balanced")
				}
			}
		} else {
			// Check for headless state (PF-capable but no leader)
			node, ok := allVPNNodes[vpnName]
			if !ok {
				continue
			}
			if !nodeSupportsPortForwarding(node.ContainerName) {
				continue
			}

			// We must check ALL active engines on the node for a leader,
			// not just the healthy ones, otherwise we might trigger a headless
			// correction while the leader is still starting up.
			allOnNode := st.GetEnginesByVPN(vpnName)
			hasLeader := false
			for _, e := range allOnNode {
				if e.Forwarded && !e.Draining && (e.HealthStatus == state.HealthHealthy || e.HealthStatus == state.HealthUnknown) {
					hasLeader = true
					break
				}
			}

			// Headless correction: only if there are at least 2 engines on the node
			// (one to replace, one to remain).
			if !hasLeader && !st.IsForwardedPending(vpnName) && len(vpnEngines) >= 2 {
				slog.Warn("VPN node is headless (PF-capable but no leader); marking follower for replacement", "vpn", vpnName)
				// Drain the most idle follower to force a leader replacement
				sort.Slice(vpnEngines, func(i, j int) bool {
					return st.GetEngineTotalLoad(vpnEngines[i].ContainerID) < st.GetEngineTotalLoad(vpnEngines[j].ContainerID)
				})
				st.MarkEngineDraining(vpnEngines[0].ContainerID, "headless_correction")
			}
		}
	}
}

func (c *Controller) selectTerminationCandidates(engines []*state.Engine, count int) []string {
	st := state.Global
	type scored struct {
		e       *state.Engine
		streams int
	}
	var ss []scored
	for _, e := range engines {
		if !canStopEngine(e.ContainerID, false) {
			continue
		}

		// SAFETY: Never kill a Leader if there are other engines on the same VPN node.
		// If we kill the leader while followers remain, the node becomes "headless"
		// and P2P performance collapses for the remaining engines.
		if e.Forwarded && e.VPNContainer != "" {
			others := st.GetEnginesByVPN(e.VPNContainer)
			activeOthers := 0
			for _, o := range others {
				if o.ContainerID != e.ContainerID && !o.Draining {
					activeOthers++
				}
			}
			if activeOthers > 0 {
				continue // Skip this leader; it's still needed.
			}
		}

		ss = append(ss, scored{e, st.GetEngineTotalLoad(e.ContainerID)})
	}

	sort.Slice(ss, func(i, j int) bool {
		// Rule 1: Fewest streams first
		if ss[i].streams != ss[j].streams {
			return ss[i].streams < ss[j].streams
		}
		// Rule 2: Followers before Leaders (Safety second)
		if ss[i].e.Forwarded != ss[j].e.Forwarded {
			return !ss[i].e.Forwarded
		}
		// Rule 3: Oldest first
		return ss[i].e.FirstSeen.Before(ss[j].e.FirstSeen)
	})

	var ids []string
	for _, s := range ss {
		if len(ids) >= count {
			break
		}
		ids = append(ids, s.e.ContainerID)
	}
	return ids
}

// ─── Intent worker ───────────────────────────────────────────────────────────

func (c *Controller) intentWorker(ctx context.Context) {
	defer c.wg.Done()

	for c.running.Load() {
		select {
		case <-ctx.Done():
			return
		case it, ok := <-c.intents:
			if !ok {
				return
			}
			metrics.CPIntentQueueDepth.Dec()
			c.executeIntent(ctx, it)
		}
	}
}

func (c *Controller) executeIntent(ctx context.Context, it intent) error {
	switch it.action {
	case intentCreate:
		if !c.cb.CanProvision("general") {
			err := fmt.Errorf("provisioning blocked by circuit breaker")
			c.cb.RecordFailure("general")
			metrics.CPProvisioningTotal.WithLabelValues("blocked").Inc()
			state.Global.RemoveIntent(it.id)
			return err
		}
		_, err := ExecuteSpec(ctx, it.spec)
		state.Global.RemoveIntent(it.id)
		if err != nil {
			errMsg := strings.ToLower(err.Error())
			// Detect host-level seccomp/BPF ceiling.
			if strings.Contains(errMsg, "seccomp") || strings.Contains(errMsg, "errno 524") ||
				strings.Contains(errMsg, "failed to create shim task") {
				slog.Error("CRITICAL: host OS seccomp limit reached; new containers cannot start",
					"err", err)
			}
			c.cb.RecordFailure("general")
			metrics.CPProvisioningTotal.WithLabelValues("failed").Inc()
			slog.Error("failed to execute create intent", "name", it.spec.ContainerName, "err", err)
			return err
		}
		c.cb.RecordSuccess("general")
		metrics.CPProvisioningTotal.WithLabelValues("success").Inc()

	case intentTerminate:
		err := StopContainer(ctx, it.contID, false)
		state.Global.RemoveIntent(it.id)
		if err != nil {
			slog.Warn("failed to stop container", "id", it.contID[:min12(len(it.contID))], "err", err)
		}
	}
	return nil
}

func (c *Controller) nextIntentID() string {
	return fmt.Sprintf("intent-%d", c.intSeq.Add(1))
}

// ─── Grace period helpers ────────────────────────────────────────────────────

func canStopEngine(containerID string, bypassGrace bool) bool {
	cfg := config.C.Load()
	st := state.Global

	load := st.GetEngineTotalLoad(containerID)
	if load > 0 {
		st.ClearEmpty(containerID)
		return false
	}

	// Replica floor checks
	engines := st.ListEngines()
	total := len(engines)
	if total > 0 && total-1 < cfg.MinReplicas {
		st.ClearEmpty(containerID)
		return false
	}

	// Free replica floor (stream + monitor load both count as "used")
	if cfg.MinFreeReplicas > 0 {
		allCounts := st.GetAllStreamCounts()
		monCounts := st.GetAllMonitorCounts()
		freeCount := 0
		for _, e := range engines {
			if allCounts[e.ContainerID]+monCounts[e.ContainerID] == 0 && !e.Draining {
				freeCount++
			}
		}
		if freeCount > 0 && freeCount-1 < cfg.MinFreeReplicas {
			st.ClearEmpty(containerID)
			return false
		}
	}

	// VPN distribution balance check
	e, ok := st.GetEngine(containerID)
	if ok && e.VPNContainer != "" {
		healthyVPNs := make(map[string]bool)
		for _, n := range st.ListVPNNodes() {
			if n.Healthy && n.ContainerName != "" {
				healthyVPNs[n.ContainerName] = true
			}
		}
		if len(healthyVPNs) > 1 && healthyVPNs[e.VPNContainer] {
			counts := make(map[string]int)
			for _, eng := range engines {
				if eng.VPNContainer != "" && healthyVPNs[eng.VPNContainer] {
					counts[eng.VPNContainer]++
				}
			}
			current := counts[e.VPNContainer]
			lowestOther := math.MaxInt
			for vpn, cnt := range counts {
				if vpn != e.VPNContainer && cnt < lowestOther {
					lowestOther = cnt
				}
			}
			if lowestOther != math.MaxInt && current < lowestOther {
				st.ClearEmpty(containerID)
				return false
			}
		}
	}

	if bypassGrace || cfg.GracePeriod == 0 {
		st.ClearEmpty(containerID)
		return true
	}

	t, exists := st.EmptySince(containerID)
	if !exists {
		st.RecordEmpty(containerID)
		return false
	}

	if time.Since(t) >= cfg.GracePeriod {
		slog.Info("engine grace period elapsed; eligible for stop", "id", containerID[:min12(len(containerID))])
		st.ClearEmpty(containerID)
		return true
	}
	return false
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

func isTransientVPNError(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return containsAny(msg, []string{
		"no healthy active dynamic vpn nodes",
		"cannot schedule acestream engine",
		"control api not reachable",
		"awaiting vpn node provisioning",
	})
}

func containsAny(s string, subs []string) bool {
	for _, sub := range subs {
		if len(s) >= len(sub) {
			for i := 0; i <= len(s)-len(sub); i++ {
				if s[i:i+len(sub)] == sub {
					return true
				}
			}
		}
	}
	return false
}

func min12(n int) int {
	if n < 12 {
		return n
	}
	return 12
}

