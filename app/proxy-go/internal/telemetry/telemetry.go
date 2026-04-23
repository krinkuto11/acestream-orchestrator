package telemetry

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/metrics"
)

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
}

// TelemetryTracker aggregates RED metrics and periodically pushes to the Orchestrator.
type TelemetryTracker struct {
	mu     sync.Mutex
	batch  TelemetryBatch
	stopCh chan struct{}
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
		stopCh: make(chan struct{}),
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

	t.mu.Lock()
	defer t.mu.Unlock()
	t.batch.Requests = append(t.batch.Requests, RequestEvent{
		Mode:         mode,
		Endpoint:     endpoint,
		DurationSecs: durationSecs,
		Success:      success,
		StatusCode:   statusCode,
		TTFBSecs:     ttfbSecs,
	})
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
}

func (t *TelemetryTracker) worker() {
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-t.stopCh:
			return
		case <-ticker.C:
			t.push()
		}
	}
}

func (t *TelemetryTracker) push() {
	t.mu.Lock()
	if len(t.batch.Requests) == 0 && len(t.batch.Connects) == 0 && len(t.batch.Disconnects) == 0 {
		t.mu.Unlock()
		return
	}
	payload := t.batch
	t.batch = TelemetryBatch{
		Requests:    make([]RequestEvent, 0, 100),
		Connects:    make([]string, 0, 10),
		Disconnects: make([]string, 0, 10),
	}
	t.mu.Unlock()

	body, err := json.Marshal(payload)
	if err != nil {
		slog.Error("failed to marshal telemetry batch", "err", err)
		return
	}

	url := config.C.Load().OrchestratorURL + "/internal/proxy/go/telemetry"
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if config.C.Load().APIKey != "" {
		req.Header.Set("X-API-Key", config.C.Load().APIKey)
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		slog.Debug("failed to push telemetry", "err", err)
		return
	}
	resp.Body.Close()
}
