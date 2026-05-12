package telemetry

import (
	"fmt"
	"math"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/acestream/acestream/internal/metrics"
)

const maxHistorySize = 5000

type timedRequestEvent struct {
	RequestEvent
	at time.Time
}

// WindowStats holds aggregated request statistics over a time window.
type WindowStats struct {
	TotalRequests int
	Errors4xx     int
	Errors5xx     int
	TTFBAvgMs     float64
	TTFBP95Ms     float64
}

// RequestEvent represents a single request observation.
type RequestEvent struct {
	Mode         string  `json:"mode"`
	Endpoint     string  `json:"endpoint"`
	DurationSecs float64 `json:"duration_seconds"`
	Success      bool    `json:"success"`
	StatusCode   int     `json:"status_code"`
	TTFBSecs     float64 `json:"ttfb_seconds,omitempty"` // 0 if not applicable
}

// TelemetryBatch holds aggregated telemetry data.
type TelemetryBatch struct {
	Requests    []RequestEvent `json:"requests"`
	Connects    []string       `json:"connects"`    // modes, e.g. "TS", "HLS"
	Disconnects []string       `json:"disconnects"` // modes
	TotalEgress int64          `json:"total_egress"`
}

// TelemetryTracker aggregates RED metrics and periodically pushes to the Orchestrator.
type TelemetryTracker struct {
	mu          sync.Mutex
	batch       TelemetryBatch
	stopCh      chan struct{}
	totalEgress int64
	egressRate  float64 // Mbps
	lastEgress  int64
	lastTime    time.Time

	historyMu sync.RWMutex
	history   []timedRequestEvent
}

// Global instance. In a full refactor we might pass this around, but
// for metrics a singleton is often practical and aligns with the existing architecture.
var DefaultTelemetry = NewTelemetryTracker()

func NewTelemetryTracker() *TelemetryTracker {
	t := &TelemetryTracker{
		batch: TelemetryBatch{
			Requests:    make([]RequestEvent, 0, 100),
			Connects:    make([]string, 0, 10),
			Disconnects: make([]string, 0, 10),
		},
		stopCh:   make(chan struct{}),
		lastTime: time.Now(),
		history:  make([]timedRequestEvent, 0, 200),
	}
	go t.worker()
	return t
}

func (t *TelemetryTracker) ObserveRequest(mode, endpoint string, durationSecs float64, success bool, statusCode int, ttfbSecs float64) {
	metrics.HttpRequestsTotal.WithLabelValues(mode, endpoint, fmt.Sprintf("%d", statusCode)).Inc()
	metrics.HttpRequestDurationSeconds.WithLabelValues(mode, endpoint).Observe(durationSecs)
	if ttfbSecs > 0 {
		metrics.HttpTtfbSeconds.WithLabelValues(mode, endpoint).Observe(ttfbSecs)
	}

	ev := RequestEvent{
		Mode:         mode,
		Endpoint:     endpoint,
		DurationSecs: durationSecs,
		Success:      success,
		StatusCode:   statusCode,
		TTFBSecs:     ttfbSecs,
	}

	t.mu.Lock()
	t.batch.Requests = append(t.batch.Requests, ev)
	t.mu.Unlock()

	t.historyMu.Lock()
	t.history = append(t.history, timedRequestEvent{RequestEvent: ev, at: time.Now()})
	if len(t.history) > maxHistorySize {
		t.history = t.history[len(t.history)-maxHistorySize:]
	}
	t.historyMu.Unlock()
}

// WindowStats returns aggregated request statistics over the given time window.
func (t *TelemetryTracker) WindowStats(window time.Duration) WindowStats {
	cutoff := time.Now().Add(-window)
	t.historyMu.RLock()
	defer t.historyMu.RUnlock()

	var stats WindowStats
	var ttfbs []float64
	for _, e := range t.history {
		if e.at.Before(cutoff) {
			continue
		}
		stats.TotalRequests++
		switch {
		case e.StatusCode >= 500:
			stats.Errors5xx++
		case e.StatusCode >= 400:
			stats.Errors4xx++
		}
		if e.TTFBSecs > 0 {
			ttfbs = append(ttfbs, e.TTFBSecs*1000)
		}
	}
	if len(ttfbs) > 0 {
		sort.Float64s(ttfbs)
		var sum float64
		for _, v := range ttfbs {
			sum += v
		}
		stats.TTFBAvgMs = sum / float64(len(ttfbs))
		p95idx := int(math.Ceil(float64(len(ttfbs))*0.95)) - 1
		if p95idx >= len(ttfbs) {
			p95idx = len(ttfbs) - 1
		}
		stats.TTFBP95Ms = ttfbs[p95idx]
	}
	return stats
}

func (t *TelemetryTracker) ObserveConnect(mode string) {
	metrics.ActiveSessions.WithLabelValues(mode).Inc()
	metrics.ConnectionsTotal.WithLabelValues(mode).Inc()

	t.mu.Lock()
	defer t.mu.Unlock()
	t.batch.Connects = append(t.batch.Connects, mode)
}

func (t *TelemetryTracker) ObserveDisconnect(mode string) {
	metrics.ActiveSessions.WithLabelValues(mode).Dec()

	t.mu.Lock()
	defer t.mu.Unlock()
	t.batch.Disconnects = append(t.batch.Disconnects, mode)
}

func (t *TelemetryTracker) ObserveIngress(mode string, bytes int64) {
	metrics.BytesIngressTotal.WithLabelValues(mode).Add(float64(bytes))
}

func (t *TelemetryTracker) ObserveEgress(mode string, bytes int64) {
	metrics.BytesEgressTotal.WithLabelValues(mode).Add(float64(bytes))
	atomic.AddInt64(&t.totalEgress, bytes)
}

func (t *TelemetryTracker) GetTotalEgress() int64 {
	return atomic.LoadInt64(&t.totalEgress)
}

func (t *TelemetryTracker) GetEgressMbps() float64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.egressRate
}

func (t *TelemetryTracker) updateRate() {
	t.mu.Lock()
	defer t.mu.Unlock()

	now := time.Now()
	elapsed := now.Sub(t.lastTime).Seconds()
	if elapsed <= 0 {
		return
	}

	current := atomic.LoadInt64(&t.totalEgress)
	delta := current - t.lastEgress
	if delta < 0 {
		delta = 0
	}

	// Calculate Mbps: (bytes * 8) / (seconds * 1024 * 1024)
	instant := (float64(delta) * 8.0) / (elapsed * 1024.0 * 1024.0)

	// EMA filter
	if t.egressRate == 0 {
		t.egressRate = instant
	} else {
		t.egressRate = 0.3*instant + 0.7*t.egressRate
	}

	t.lastEgress = current
	t.lastTime = now
}

func (t *TelemetryTracker) worker() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-t.stopCh:
			return
		case <-ticker.C:
			t.updateRate()
			t.push()
			t.pruneHistory()
		}
	}
}

func (t *TelemetryTracker) pruneHistory() {
	cutoff := time.Now().Add(-2 * time.Minute)
	t.historyMu.Lock()
	i := 0
	for i < len(t.history) && t.history[i].at.Before(cutoff) {
		i++
	}
	if i > 0 {
		t.history = t.history[i:]
	}
	t.historyMu.Unlock()
}

func (t *TelemetryTracker) push() {
	// Prometheus metrics are updated in-process on every Observe* call.
	// The batch exists only to drain the accumulation; no external push needed
	// in the unified binary.
	t.mu.Lock()
	t.batch = TelemetryBatch{
		Requests:    make([]RequestEvent, 0, 100),
		Connects:    make([]string, 0, 10),
		Disconnects: make([]string, 0, 10),
	}
	t.mu.Unlock()
}
