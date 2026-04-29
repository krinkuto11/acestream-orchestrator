package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/acestream/acestream/internal/config"
	cpdocker "github.com/acestream/acestream/internal/controlplane/docker"
	"github.com/acestream/acestream/internal/state"
)

// sseBroker fans out state-change notifications to connected SSE clients.
type sseBroker struct {
	mu      sync.Mutex
	clients map[chan []byte]struct{}
}

var globalBroker = &sseBroker{
	clients: make(map[chan []byte]struct{}),
}

func (b *sseBroker) subscribe() chan []byte {
	ch := make(chan []byte, 4)
	b.mu.Lock()
	b.clients[ch] = struct{}{}
	b.mu.Unlock()
	return ch
}

func (b *sseBroker) unsubscribe(ch chan []byte) {
	b.mu.Lock()
	delete(b.clients, ch)
	b.mu.Unlock()
}

func (b *sseBroker) publish(msg []byte) {
	b.mu.Lock()
	defer b.mu.Unlock()
	for ch := range b.clients {
		select {
		case ch <- msg:
		default:
			// drop if client is slow
		}
	}
}

// NotifyStateChange serialises the current state and fans it out to SSE clients.
// Called by the bridge/monitor whenever state changes.
func NotifyStateChange(st stateSnapshot) {
	payload, err := buildSSEPayload(st)
	if err != nil {
		return
	}
	msg := formatSSE("full_sync", payload)
	globalBroker.publish([]byte(msg))
}

// stateSnapshot is a subset of the store interface needed to build SSE payloads.
type stateSnapshot interface {
	ListEngines() interface{}
	ListStreams() interface{}
	ListVPNNodes() interface{}
}

func buildSSEPayload(st stateSnapshot) (map[string]any, error) {
	return map[string]any{
		"engines":   st.ListEngines(),
		"streams":   st.ListStreams(),
		"vpn_nodes": st.ListVPNNodes(),
		"timestamp": time.Now().UTC(),
	}, nil
}

func formatSSE(event string, data any) string {
	b, _ := json.Marshal(data)
	return fmt.Sprintf("event: %s\ndata: %s\n\n", event, string(b))
}

// ─── SSE route registration ───────────────────────────────────────────────────

func (s *ProxyServer) registerSSERoutes() {
	s.mux.HandleFunc("GET /api/v1/events/stream", s.handleSSEEventsStream)
	s.mux.HandleFunc("GET /api/v1/events/live", s.handleSSEEventsLive)
	s.mux.HandleFunc("GET /api/v1/metrics/stream", s.handleSSEMetricsStream)
	s.mux.HandleFunc("GET /api/v1/containers/{id}/logs/stream", s.handleSSEContainerLogs)
	s.mux.HandleFunc("GET /api/v1/vpn/leases/stream", s.handleSSEVPNLeases)
	s.mux.HandleFunc("GET /api/v1/ace/monitor/legacy/stream", s.handleSSEMonitorLegacy)
	s.mux.HandleFunc("GET /api/v1/streams/{id}/details/stream", s.handleSSEStreamDetails)
	s.mux.HandleFunc("GET /api/v1/custom-variant/reprovision/status/stream", s.handleSSEReprovisionStatus)
	s.mux.HandleFunc("GET /api/v1/settings/engine/reprovision/status/stream", s.handleSSEReprovisionStatus)
}

// ─── SSE helpers ─────────────────────────────────────────────────────────────

func sseHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
}

func sseAPIKeyOK(r *http.Request) bool {
	key := config.C.Load().APIKey
	if key == "" {
		return true
	}
	kb := []byte(key)
	match := func(provided string) bool {
		return provided != "" && subtle.ConstantTimeCompare([]byte(provided), kb) == 1
	}
	if match(r.URL.Query().Get("api_key")) || match(r.URL.Query().Get("key")) {
		return true
	}
	if match(r.Header.Get("X-API-Key")) {
		return true
	}
	if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
		return match(strings.TrimPrefix(auth, "Bearer "))
	}
	return false
}

// writeSSEEvent writes a single SSE event; returns false if the client is gone.
func writeSSEEvent(w http.ResponseWriter, event string, data any) bool {
	b, err := json.Marshal(data)
	if err != nil {
		return false
	}
	_, err = fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, string(b))
	if err != nil {
		return false
	}
	if f, ok := w.(http.Flusher); ok {
		f.Flush()
	}
	return true
}

func writeSSEKeepAlive(w http.ResponseWriter) bool {
	_, err := fmt.Fprint(w, ": keep-alive\n\n")
	if err != nil {
		return false
	}
	if f, ok := w.(http.Flusher); ok {
		f.Flush()
	}
	return true
}

// ─── /api/v1/events/stream  &  /api/v1/events/live ───────────────────────────

func (s *ProxyServer) handleSSEEventsStream(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	sseHeaders(w)

	// Send initial full sync.
	initial := s.buildStatePayload()
	if !writeSSEEvent(w, "full_sync", map[string]any{
		"type":    "full_sync",
		"payload": initial,
		"meta":    map[string]any{"reason": "initial_sync"},
	}) {
		return
	}

	ch := globalBroker.subscribe()
	defer globalBroker.unsubscribe(ch)

	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case msg, ok := <-ch:
			if !ok {
				return
			}
			if _, err := w.Write(msg); err != nil {
				return
			}
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
		case <-ticker.C:
			if !writeSSEKeepAlive(w) {
				return
			}
		}
	}
}

// ─── /api/v1/events/live ─────────────────────────────────────────────────────

// handleSSEEventsLive serves the EventsPage which expects "events_snapshot"
// events carrying a log of recent system events (not full state syncs).
func (s *ProxyServer) handleSSEEventsLive(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	sseHeaders(w)

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			snapshot := map[string]any{
				"events":    []any{},
				"stats":     map[string]any{"total": 0},
				"timestamp": time.Now().UTC(),
			}
			if !writeSSEEvent(w, "events_snapshot", map[string]any{"payload": snapshot}) {
				return
			}
		case <-keepalive.C:
			if !writeSSEKeepAlive(w) {
				return
			}
		}
	}
}

// ─── /api/v1/metrics/stream ───────────────────────────────────────────────────

func (s *ProxyServer) handleSSEMetricsStream(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	sseHeaders(w)

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			snapshot := s.buildMetricsSnapshot()
			if !writeSSEEvent(w, "metrics_snapshot", snapshot) {
				return
			}
		case <-keepalive.C:
			if !writeSSEKeepAlive(w) {
				return
			}
		}
	}
}

// ─── /api/v1/containers/{id}/logs/stream ────────────────────────────────────

func (s *ProxyServer) handleSSEContainerLogs(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	id := r.PathValue("id")
	sseHeaders(w)

	sendLogs := func() bool {
		lines, err := cpdocker.GetContainerLogs(r.Context(), id, "100", false, 0)
		if err != nil {
			slog.Warn("SSE logs: get container logs failed", "id", id, "err", err)
			return writeSSEEvent(w, "error", map[string]string{"error": err.Error()})
		}
		return writeSSEEvent(w, "container_logs_snapshot", map[string]any{
			"container_id": id,
			"logs":         strings.Join(lines, "\n"),
			"fetched_at":   time.Now().UTC(),
		})
	}

	sendLogs()

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			if !sendLogs() {
				return
			}
		case <-keepalive.C:
			if !writeSSEKeepAlive(w) {
				return
			}
		}
	}
}

// ─── /api/v1/vpn/leases/stream ───────────────────────────────────────────────

func (s *ProxyServer) handleSSEVPNLeases(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	sseHeaders(w)

	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	sendLeases := func() bool {
		nodes := s.st.ListVPNNodes()
		return writeSSEEvent(w, "vpn_leases_snapshot", map[string]any{"vpn_nodes": nodes})
	}

	sendLeases()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			if !sendLeases() {
				return
			}
		}
	}
}

// ─── /api/v1/ace/monitor/legacy/stream ───────────────────────────────────────

func (s *ProxyServer) handleSSEMonitorLegacy(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	sseHeaders(w)

	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	sendMonitor := func() bool {
		sessions := s.mon.List(false)
		return writeSSEEvent(w, "legacy_monitor_snapshot", map[string]any{"items": sessions})
	}

	sendMonitor()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			if !sendMonitor() {
				return
			}
		case <-keepalive.C:
			if !writeSSEKeepAlive(w) {
				return
			}
		}
	}
}

// ─── /api/v1/streams/{id}/details/stream ────────────────────────────────────

func (s *ProxyServer) handleSSEStreamDetails(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	id := r.PathValue("id")
	sseHeaders(w)

	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	sendDetails := func() bool {
		st, ok := s.st.GetStream(id)
		if !ok {
			writeSSEEvent(w, "stream_not_found", map[string]string{"stream_id": id})
			return false
		}
		return writeSSEEvent(w, "stream_details", st)
	}

	sendDetails()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			if !sendDetails() {
				return
			}
		case <-keepalive.C:
			if !writeSSEKeepAlive(w) {
				return
			}
		}
	}
}

// ─── reprovision status stream ────────────────────────────────────────────────

func (s *ProxyServer) handleSSEReprovisionStatus(w http.ResponseWriter, r *http.Request) {
	if !sseAPIKeyOK(r) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	sseHeaders(w)

	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			if !writeSSEEvent(w, "reprovision_status", reprovisionStatus) {
				return
			}
		case <-keepalive.C:
			if !writeSSEKeepAlive(w) {
				return
			}
		}
	}
}

// ─── State snapshot helpers ───────────────────────────────────────────────────

func (s *ProxyServer) buildStatePayload() map[string]any {
	cfg := config.C.Load()
	engines := s.st.ListEnginesWithCounts()
	streams := s.st.ListStreams()
	vpns := s.st.ListVPNNodes()

	var healthy, unhealthy, draining int
	for _, e := range engines {
		switch {
		case e.Draining:
			draining++
		case e.HealthStatus == state.HealthHealthy:
			healthy++
		default:
			unhealthy++
		}
	}

	activeStreams := 0
	for _, st := range streams {
		if st.Status == "started" {
			activeStreams++
		}
	}

	var vpnHealthy int
	for _, n := range vpns {
		if n.Healthy {
			vpnHealthy++
		}
	}

	// Circuit-breaker state for provisioning section.
	cbState := "closed"
	var lastFailure any
	if s.cb != nil {
		cbStatus := s.cb.GetStatus()
		if gen, ok := cbStatus["general"].(map[string]any); ok {
			if st, ok := gen["state"].(string); ok && st != "" {
				cbState = st
			}
			lastFailure = gen["last_failure_time"]
		}
	}
	canProvision := cbState == "closed"

	streamCounts := s.st.GetStreamCounts()
	monCounts := s.st.GetMonitorCounts()
	usedCapacity := 0
	for _, e := range engines {
		if streamCounts[e.ContainerID]+monCounts[e.ContainerID] > 0 {
			usedCapacity++
		}
	}
	totalCapacity := len(engines)
	availableCapacity := totalCapacity - usedCapacity
	if availableCapacity < 0 {
		availableCapacity = 0
	}

	var overallStatus string
	switch {
	case len(engines) == 0:
		overallStatus = "unavailable"
	case !canProvision:
		overallStatus = "degraded"
	default:
		overallStatus = "healthy"
	}

	orchestratorStatus := map[string]any{
		"status": overallStatus,
		"engines": map[string]any{
			"total":     len(engines),
			"healthy":   healthy,
			"unhealthy": unhealthy,
			"draining":  draining,
		},
		"streams": map[string]any{
			"active": activeStreams,
			"total":  len(streams),
		},
		"capacity": map[string]any{
			"total":        totalCapacity,
			"used":         usedCapacity,
			"available":    availableCapacity,
			"max_replicas": cfg.MaxReplicas,
			"min_replicas": cfg.MinReplicas,
		},
		"vpn": map[string]any{
			"enabled":     cfg.VPNEnabled,
			"nodes_total": len(vpns),
			"healthy":     vpnHealthy,
		},
		"provisioning": map[string]any{
			"can_provision":         canProvision,
			"circuit_breaker_state": cbState,
			"last_failure":          lastFailure,
		},
		"timestamp": time.Now().UTC(),
	}

	return map[string]any{
		"engines":             engines,
		"streams":             streams,
		"vpn_nodes":           vpns,
		"engines_total":       len(engines),
		"engines_healthy":     healthy,
		"engines_unhealthy":   unhealthy,
		"engines_draining":    draining,
		"streams_active":      activeStreams,
		"orchestrator_status": orchestratorStatus,
		"health": map[string]any{
			"status":            overallStatus,
			"healthy_engines":   healthy,
			"unhealthy_engines": unhealthy,
		},
		"timestamp": time.Now().UTC(),
	}
}

func (s *ProxyServer) buildMetricsSnapshot() map[string]any {
	return s.buildStatePayload()
}

// RunSSEPublisher polls for state changes and notifies the SSE broker.
// Call this in a background goroutine.
func (s *ProxyServer) RunSSEPublisher(ctx context.Context) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	var lastHash string
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			payload := s.buildStatePayload()
			h := stateHash(payload)
			if h != lastHash {
				lastHash = h
				msg := formatSSE("full_sync", map[string]any{
					"type":    "full_sync",
					"payload": payload,
					"meta":    map[string]any{"reason": "state_change"},
				})
				globalBroker.publish([]byte(msg))
			}
		}
	}
}

func stateHash(m map[string]any) string {
	b, _ := json.Marshal(m)
	h := fnv.New64a()
	h.Write(b)
	return strconv.FormatUint(h.Sum64(), 16)
}
