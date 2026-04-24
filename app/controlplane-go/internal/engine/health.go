package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/acestream/controlplane/internal/config"
	"github.com/acestream/controlplane/internal/metrics"
	"github.com/acestream/controlplane/internal/state"
)

// engineHealthTracker tracks consecutive failure state for one engine.
type engineHealthTracker struct {
	consecutiveFailures int
	firstFailureTime    time.Time
	lastHealthyTime     time.Time
	markedForReplacement bool
	replacementStarted  bool
}

func (t *engineHealthTracker) isConsidered(threshold int) bool {
	return t.consecutiveFailures >= threshold
}

func (t *engineHealthTracker) shouldBeReplaced(threshold int, gracePeriod time.Duration) bool {
	if !t.isConsidered(threshold) || t.firstFailureTime.IsZero() {
		return false
	}
	return time.Since(t.firstFailureTime) > gracePeriod
}

// HealthManager is the Go equivalent of Python's HealthManager.
// It probes all engines concurrently and evicts durably unhealthy ones.
type HealthManager struct {
	controller         *Controller
	trackers           map[string]*engineHealthTracker
	mu                 sync.Mutex
	lastReplacementAt  time.Time
	running            atomic.Bool
	wg                 sync.WaitGroup
}

// NewHealthManager creates a HealthManager wired to the given controller.
func NewHealthManager(ctrl *Controller) *HealthManager {
	return &HealthManager{
		controller: ctrl,
		trackers:   make(map[string]*engineHealthTracker),
	}
}

func (hm *HealthManager) Start(ctx context.Context) {
	if hm.running.Swap(true) {
		return
	}
	cfg := config.C
	slog.Info("HealthManager started", "interval", cfg.HealthCheckInterval)
	hm.wg.Add(1)
	go hm.loop(ctx)
}

func (hm *HealthManager) Stop() {
	hm.running.Store(false)
	hm.wg.Wait()
	slog.Info("HealthManager stopped")
}

func (hm *HealthManager) loop(ctx context.Context) {
	defer hm.wg.Done()
	ticker := time.NewTicker(config.C.HealthCheckInterval)
	defer ticker.Stop()

	for hm.running.Load() {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		hm.checkAndManage(ctx)
	}
}

func (hm *HealthManager) checkAndManage(ctx context.Context) {
	cfg := config.C
	st := state.Global
	engines := st.ListEngines()

	// Clean up trackers for removed engines
	currentIDs := make(map[string]bool, len(engines))
	for _, e := range engines {
		currentIDs[e.ContainerID] = true
	}
	hm.mu.Lock()
	for id := range hm.trackers {
		if !currentIDs[id] {
			delete(hm.trackers, id)
		}
	}
	// Init trackers for new engines
	for _, e := range engines {
		if _, ok := hm.trackers[e.ContainerID]; !ok {
			hm.trackers[e.ContainerID] = &engineHealthTracker{}
		}
	}
	hm.mu.Unlock()

	// Fan out probes concurrently — the key Go improvement over Python's sequential loop
	type probeResult struct {
		engine  *state.Engine
		healthy bool
	}

	results := make(chan probeResult, len(engines))
	var probeWG sync.WaitGroup
	for _, e := range engines {
		probeWG.Add(1)
		go func(eng *state.Engine) {
			defer probeWG.Done()
			ok := probeHealth(eng.Host, eng.Port)
			results <- probeResult{eng, ok}
		}(e)
	}
	probeWG.Wait()
	close(results)

	var healthy, unhealthy []*state.Engine

	for r := range results {
		hm.mu.Lock()
		tracker := hm.trackers[r.engine.ContainerID]
		if tracker == nil {
			tracker = &engineHealthTracker{}
			hm.trackers[r.engine.ContainerID] = tracker
		}

		if r.healthy {
			tracker.consecutiveFailures = 0
			tracker.lastHealthyTime = time.Now().UTC()
			tracker.firstFailureTime = time.Time{}
			st.UpdateEngineHealth(r.engine.ContainerID, state.HealthHealthy)
			healthy = append(healthy, r.engine)
			metrics.HealthCheckTotal.WithLabelValues("healthy").Inc()
		} else {
			tracker.consecutiveFailures++
			if tracker.firstFailureTime.IsZero() {
				tracker.firstFailureTime = time.Now().UTC()
			}
			if tracker.isConsidered(cfg.HealthFailureThreshold) {
				st.UpdateEngineHealth(r.engine.ContainerID, state.HealthUnhealthy)
				unhealthy = append(unhealthy, r.engine)
			} else {
				healthy = append(healthy, r.engine) // transient
			}
			metrics.HealthCheckTotal.WithLabelValues("unhealthy").Inc()
		}
		hm.mu.Unlock()
	}

	slog.Debug("health check complete", "healthy", len(healthy), "unhealthy", len(unhealthy))

	// Detect zero-peer starvation across healthy engines
	hm.detectStarvation(healthy)

	// Wait for VPN recovery if target node is not ready
	if hm.shouldWaitForVPNRecovery(healthy) {
		return
	}

	// Evict durably unhealthy engines (one at a time, with cooldown)
	hm.evictUnhealthy(ctx, unhealthy)
}

// probeHealth checks the AceStream engine's get_status API endpoint.
func probeHealth(host string, port int) bool {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	url := fmt.Sprintf("http://%s:%d/server/api?api_version=3&method=get_status", host, port)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return false
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return false
	}

	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	var v any
	return json.Unmarshal(body, &v) == nil
}

func (hm *HealthManager) detectStarvation(healthy []*state.Engine) {
	st := state.Global
	now := time.Now().UTC()

	vpnNodesByName := make(map[string]*state.VPNNode)
	for _, n := range st.ListVPNNodes() {
		vpnNodesByName[n.ContainerName] = n
	}

	for _, e := range healthy {
		streamCount := st.GetStreamCount(e.ContainerID)
		if streamCount == 0 {
			continue
		}

		_ = now // placeholder: real starvation check requires per-stream peer stats
		// from the data plane's Redis keys. We skip it here unless stream stats
		// are present in the control plane state. The data plane handles its own
		// starvation detection via the proxy.
		//
		// If integration is needed, read from Redis:
		//   ace_proxy:stream:{key}:metadata and check peers/speed_down.
		//
		// For Phase 1, we mark VPN node draining only when the data plane
		// publishes "stream_starved:{containerID}" to Redis channel cp:state_changed.
		// This is handled in docker/events.go handleStateChange().
	}
}

func (hm *HealthManager) shouldWaitForVPNRecovery(healthy []*state.Engine) bool {
	st := state.Global
	vpnNodes := st.ListVPNNodes()
	if len(vpnNodes) == 0 {
		return false
	}

	// Find least-loaded healthy VPN node (likely provisioning target)
	var targetVPN *state.VPNNode
	for _, n := range vpnNodes {
		if !n.Healthy {
			continue
		}
		if targetVPN == nil || len(st.GetEnginesByVPN(n.ContainerName)) < len(st.GetEnginesByVPN(targetVPN.ContainerName)) {
			targetVPN = n
		}
	}

	if targetVPN == nil {
		return false
	}

	cond := strings.ToLower(strings.TrimSpace(targetVPN.Condition))
	if cond != "" && cond != "ready" {
		slog.Info("target VPN not ready; deferring evictions", "vpn", targetVPN.ContainerName, "condition", cond, "healthy_engines", len(healthy))
		return true
	}

	return false
}

func (hm *HealthManager) evictUnhealthy(ctx context.Context, unhealthy []*state.Engine) {
	if len(unhealthy) == 0 {
		return
	}

	cfg := config.C

	// Cooldown between replacements
	hm.mu.Lock()
	timeSinceLast := time.Since(hm.lastReplacementAt)
	hm.mu.Unlock()
	if timeSinceLast < cfg.HealthReplacementCooldown {
		return
	}

	// Find first engine that has crossed grace period and is not already being replaced
	hm.mu.Lock()
	var candidate *state.Engine
	for _, e := range unhealthy {
		t := hm.trackers[e.ContainerID]
		if t == nil {
			continue
		}
		if t.shouldBeReplaced(cfg.HealthFailureThreshold, cfg.HealthUnhealthyGracePeriod) && !t.markedForReplacement {
			candidate = e
			t.markedForReplacement = true
			break
		}
	}
	hm.mu.Unlock()

	if candidate == nil {
		return
	}

	slog.Info("evicting unhealthy engine", "id", candidate.ContainerID[:min12(len(candidate.ContainerID))])

	hm.wg.Add(1)
	go func(e *state.Engine) {
		defer hm.wg.Done()
		hm.mu.Lock()
		t := hm.trackers[e.ContainerID]
		if t != nil {
			t.replacementStarted = true
		}
		hm.mu.Unlock()

		if err := StopContainer(ctx, e.ContainerID, false); err != nil {
			slog.Error("failed to evict unhealthy engine", "id", e.ContainerID[:min12(len(e.ContainerID))], "err", err)
			hm.mu.Lock()
			if t := hm.trackers[e.ContainerID]; t != nil {
				t.markedForReplacement = false
				t.replacementStarted = false
			}
			hm.mu.Unlock()
			return
		}

		state.Global.RemoveEngine(e.ContainerID)
		metrics.ProvisioningTotal.WithLabelValues("evicted").Inc()

		hm.mu.Lock()
		delete(hm.trackers, e.ContainerID)
		hm.lastReplacementAt = time.Now().UTC()
		hm.mu.Unlock()

		slog.Info("unhealthy engine evicted; controller will replace it", "id", e.ContainerID[:min12(len(e.ContainerID))])
		// Nudge the controller so it immediately fills the deficit
		if hm.controller != nil {
			hm.controller.Nudge("unhealthy_eviction")
		}
	}(candidate)
}

// GetSummary returns a health summary map for the HTTP API.
func (hm *HealthManager) GetSummary() map[string]any {
	cfg := config.C
	st := state.Global
	engines := st.ListEngines()

	var healthy, unhealthy, markedCount int
	hm.mu.Lock()
	for _, e := range engines {
		t := hm.trackers[e.ContainerID]
		if t == nil || !t.isConsidered(cfg.HealthFailureThreshold) {
			healthy++
		} else {
			unhealthy++
			if t.markedForReplacement {
				markedCount++
			}
		}
	}
	hm.mu.Unlock()

	return map[string]any{
		"total_engines":         len(engines),
		"healthy_engines":       healthy,
		"unhealthy_engines":     unhealthy,
		"marked_for_replacement": markedCount,
		"minimum_required":      cfg.MinReplicas,
		"health_check_interval": cfg.HealthCheckInterval.Seconds(),
	}
}
