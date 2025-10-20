package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"javinator9889/acexy/lib/debug"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

type orchClient struct {
	base string
	key  string
	hc   *http.Client
	// opcional si el proxy conoce el contenedor
	containerID string
	// Maximum streams per engine
	maxStreamsPerEngine int
	// Health monitoring
	health OrchestratorHealth
	// Context for background tasks
	ctx    context.Context
	cancel context.CancelFunc
	// Pending streams tracker to avoid race conditions
	pendingStreams   map[string]int // containerID -> count of streams being allocated
	pendingStreamsMu sync.Mutex
	// Track streams that have already had EmitEnded called to prevent duplicates
	endedStreams   map[string]bool
	endedStreamsMu sync.Mutex
	// Engine list cache to reduce concurrent orchestrator queries
	engineCache         []engineState
	engineCacheTime     time.Time
	engineCacheDuration time.Duration
	engineCacheMu       sync.RWMutex
}

// OrchestratorHealth tracks the health status of the orchestrator
type OrchestratorHealth struct {
	mu                sync.RWMutex
	lastCheck         time.Time
	status            string
	canProvision      bool
	blockedReason     string
	blockedReasonCode string // NEW: Error code for blocked reason
	recoveryETA       int    // NEW: Estimated recovery time in seconds
	shouldWait        bool   // NEW: Whether clients should wait/retry
	vpnConnected      bool
	capacity          CapacityInfo // NEW: Capacity information
}

// CapacityInfo represents orchestrator capacity status
type CapacityInfo struct {
	Total     int
	Used      int
	Available int
}

// orchestratorStatus represents the response from /orchestrator/status endpoint
type orchestratorStatus struct {
	Status string `json:"status"`
	VPN    struct {
		Connected bool `json:"connected"`
	} `json:"vpn"`
	Provisioning struct {
		CanProvision         bool            `json:"can_provision"`
		BlockedReason        string          `json:"blocked_reason"`
		BlockedReasonDetails *ProvisionError `json:"blocked_reason_details"` // NEW: Enhanced error details
	} `json:"provisioning"`
	Capacity struct {
		Total     int `json:"total"`
		Used      int `json:"used"`
		Available int `json:"available"`
	} `json:"capacity"` // NEW: Capacity information
}

// ProvisionError represents structured error details from orchestrator
type ProvisionError struct {
	Error              string `json:"error"`
	Code               string `json:"code"`
	Message            string `json:"message"`
	RecoveryETASeconds int    `json:"recovery_eta_seconds"`
	CanRetry           bool   `json:"can_retry"`
	ShouldWait         bool   `json:"should_wait"`
}

// ProvisioningError wraps a structured provisioning error with HTTP status code
type ProvisioningError struct {
	StatusCode int
	Details    *ProvisionError
}

func (e *ProvisioningError) Error() string {
	if e.Details != nil {
		return fmt.Sprintf("provisioning %s: %s", e.Details.Code, e.Details.Message)
	}
	return fmt.Sprintf("provisioning failed with status %d", e.StatusCode)
}

func newOrchClient(base string) *orchClient {
	if base == "" {
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	client := &orchClient{
		base:                base,
		key:                 os.Getenv("ACEXY_ORCH_APIKEY"),
		containerID:         os.Getenv("ACEXY_CONTAINER_ID"),
		maxStreamsPerEngine: 1, // Default value, will be set from main
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
		pendingStreams:      make(map[string]int),
		endedStreams:        make(map[string]bool),
		engineCacheDuration: 2 * time.Second, // Cache engines for 2 seconds to reduce concurrent queries
	}

	// Start health monitoring in background
	go client.StartHealthMonitor()
	
	// Start background cleanup for stale tracking data
	go client.StartCleanupMonitor()

	return client
}

// Close stops the health monitor and cleanup tasks
func (c *orchClient) Close() {
	if c != nil && c.cancel != nil {
		c.cancel()
	}
}

// StartCleanupMonitor periodically cleans up stale tracking data
func (c *orchClient) StartCleanupMonitor() {
	if c == nil {
		return
	}

	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()

	for {
		select {
		case <-c.ctx.Done():
			return
		case <-ticker.C:
			c.cleanupStaleData()
		}
	}
}

// cleanupStaleData removes old entries from tracking maps
func (c *orchClient) cleanupStaleData() {
	if c == nil {
		return
	}

	// Clean up ended streams tracking (keep only last 1000 entries)
	c.endedStreamsMu.Lock()
	if len(c.endedStreams) > 1000 {
		// Clear all to prevent unbounded growth
		// This is safe because streams that ended >5 minutes ago don't need tracking
		slog.Debug("Cleaning up ended streams tracking map", "size", len(c.endedStreams))
		c.endedStreams = make(map[string]bool)
	}
	c.endedStreamsMu.Unlock()

	// Log any pending streams that might be stuck
	c.pendingStreamsMu.Lock()
	if len(c.pendingStreams) > 0 {
		slog.Warn("Pending streams still tracked", "count", len(c.pendingStreams), "engines", c.pendingStreams)
	}
	c.pendingStreamsMu.Unlock()
}

// SetMaxStreamsPerEngine sets the maximum streams per engine configuration
func (c *orchClient) SetMaxStreamsPerEngine(max int) {
	if c != nil && max > 0 {
		c.maxStreamsPerEngine = max
	}
}

// StartHealthMonitor periodically checks orchestrator health
func (c *orchClient) StartHealthMonitor() {
	if c == nil {
		return
	}

	// Do initial health check immediately
	c.updateHealth()

	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-c.ctx.Done():
			return
		case <-ticker.C:
			c.updateHealth()
		}
	}
}

// updateHealth fetches and updates the orchestrator health status
func (c *orchClient) updateHealth() {
	debugLog := debug.GetDebugLogger()
	
	if c == nil {
		return
	}

	resp, err := c.hc.Get(c.base + "/orchestrator/status")
	if err != nil {
		slog.Warn("Health check failed", "error", err)
		return
	}
	defer resp.Body.Close()

	var status orchestratorStatus
	if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
		slog.Warn("Failed to decode health status", "error", err)
		return
	}

	c.health.mu.Lock()
	defer c.health.mu.Unlock()
	c.health.lastCheck = time.Now()
	c.health.status = status.Status
	c.health.canProvision = status.Provisioning.CanProvision
	c.health.blockedReason = status.Provisioning.BlockedReason
	c.health.vpnConnected = status.VPN.Connected
	c.health.capacity = CapacityInfo{
		Total:     status.Capacity.Total,
		Used:      status.Capacity.Used,
		Available: status.Capacity.Available,
	}

	// Extract details from blocked reason if available
	if status.Provisioning.BlockedReasonDetails != nil {
		c.health.blockedReasonCode = status.Provisioning.BlockedReasonDetails.Code
		c.health.recoveryETA = status.Provisioning.BlockedReasonDetails.RecoveryETASeconds
		c.health.shouldWait = status.Provisioning.BlockedReasonDetails.ShouldWait
	} else {
		c.health.blockedReasonCode = ""
		c.health.recoveryETA = 0
		c.health.shouldWait = false
	}

	slog.Debug("Orchestrator health updated",
		"status", status.Status,
		"can_provision", status.Provisioning.CanProvision,
		"vpn_connected", status.VPN.Connected,
		"blocked_code", c.health.blockedReasonCode,
		"recovery_eta", c.health.recoveryETA,
		"capacity_available", c.health.capacity.Available)
	
	// Log orchestrator health for debugging
	debugLog.LogOrchestratorHealth(
		status.Status,
		status.Provisioning.CanProvision,
		status.Provisioning.BlockedReason,
		c.health.blockedReasonCode,
		c.health.recoveryETA,
		status.VPN.Connected,
		c.health.capacity.Total,
		c.health.capacity.Used,
		c.health.capacity.Available,
	)
	
	// Detect degraded state
	if status.Status == "degraded" {
		debugLog.LogStressEvent(
			"orchestrator_degraded",
			"warning",
			fmt.Sprintf("Orchestrator is degraded: %s", status.Provisioning.BlockedReason),
			map[string]interface{}{
				"blocked_reason": status.Provisioning.BlockedReason,
				"blocked_code":   c.health.blockedReasonCode,
				"capacity_used":  c.health.capacity.Used,
				"capacity_total": c.health.capacity.Total,
			},
		)
	}
}

// CanProvision checks if orchestrator can provision new engines
func (c *orchClient) CanProvision() (bool, string) {
	if c == nil {
		return false, "orchestrator not configured"
	}

	c.health.mu.RLock()
	defer c.health.mu.RUnlock()

	return c.health.canProvision, c.health.blockedReason
}

// GetProvisioningStatus returns detailed provisioning status including recovery information
func (c *orchClient) GetProvisioningStatus() (canProvision bool, shouldWait bool, recoveryETA int) {
	if c == nil {
		return false, false, 0
	}

	c.health.mu.RLock()
	defer c.health.mu.RUnlock()

	return c.health.canProvision, c.health.shouldWait, c.health.recoveryETA
}

// parseProvisionError parses error response from provisioning endpoint
// Handles both structured (new) and legacy (string) error formats
func parseProvisionError(resp *http.Response) (*ProvisionError, error) {
	var errorResp struct {
		Detail json.RawMessage `json:"detail"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&errorResp); err != nil {
		return nil, fmt.Errorf("failed to decode error response: %w", err)
	}

	// Try to parse as structured error (new format)
	var provError ProvisionError
	if err := json.Unmarshal(errorResp.Detail, &provError); err == nil && provError.Code != "" {
		return &provError, nil
	}

	// Fallback to string error (legacy format)
	var stringDetail string
	if err := json.Unmarshal(errorResp.Detail, &stringDetail); err == nil {
		// Parse common error patterns to provide better error codes
		code := "general_error"
		shouldWait := false
		recoveryETA := 0

		if strings.Contains(stringDetail, "VPN") {
			code = "vpn_disconnected"
			shouldWait = true
			recoveryETA = 60
		} else if strings.Contains(stringDetail, "circuit breaker") || strings.Contains(stringDetail, "Circuit breaker") {
			code = "circuit_breaker"
			shouldWait = true
			recoveryETA = 180
		} else if strings.Contains(stringDetail, "capacity") {
			code = "max_capacity"
			shouldWait = true
			recoveryETA = 30
		}

		return &ProvisionError{
			Error:              "provisioning_failed",
			Code:               code,
			Message:            stringDetail,
			RecoveryETASeconds: recoveryETA,
			ShouldWait:         shouldWait,
			CanRetry:           shouldWait,
		}, nil
	}

	return nil, fmt.Errorf("failed to parse error response")
}

type startedEvent struct {
	ContainerID string `json:"container_id,omitempty"`
	Engine      struct {
		Host string `json:"host"`
		Port int    `json:"port"`
	} `json:"engine"`
	Stream struct {
		KeyType string `json:"key_type"`
		Key     string `json:"key"`
	} `json:"stream"`
	Session struct {
		PlaybackSessionID string `json:"playback_session_id"`
		StatURL           string `json:"stat_url"`
		CommandURL        string `json:"command_url"`
		IsLive            int    `json:"is_live"`
	} `json:"session"`
	Labels map[string]string `json:"labels,omitempty"`
}

type endedEvent struct {
	ContainerID string `json:"container_id,omitempty"`
	StreamID    string `json:"stream_id,omitempty"`
	Reason      string `json:"reason,omitempty"`
}

// New types for engine selection and orchestrator API
type engineState struct {
	ContainerID     string            `json:"container_id"`
	ContainerName   string            `json:"container_name,omitempty"`
	Host            string            `json:"host"`
	Port            int               `json:"port"`
	Labels          map[string]string `json:"labels"`
	FirstSeen       time.Time         `json:"first_seen"`
	LastSeen        time.Time         `json:"last_seen"`
	HealthStatus    string            `json:"health_status"`
	LastHealthCheck time.Time         `json:"last_health_check"`
	LastStreamUsage time.Time         `json:"last_stream_usage"`
	Streams         []string          `json:"streams"`
}

type streamState struct {
	ID                string     `json:"id"`
	KeyType           string     `json:"key_type"`
	Key               string     `json:"key"`
	ContainerID       string     `json:"container_id"`
	PlaybackSessionID string     `json:"playback_session_id"`
	StatURL           string     `json:"stat_url"`
	CommandURL        string     `json:"command_url"`
	IsLive            bool       `json:"is_live"`
	StartedAt         time.Time  `json:"started_at"`
	EndedAt           *time.Time `json:"ended_at,omitempty"`
	Status            string     `json:"status"`
}

type aceProvisionRequest struct {
	Image    string            `json:"image,omitempty"`
	Labels   map[string]string `json:"labels"`
	Env      map[string]string `json:"env"`
	HostPort *int              `json:"host_port,omitempty"`
}

type aceProvisionResponse struct {
	ContainerID        string `json:"container_id"`
	ContainerName      string `json:"container_name"`
	HostHTTPPort       int    `json:"host_http_port"`
	ContainerHTTPPort  int    `json:"container_http_port"`
	ContainerHTTPSPort int    `json:"container_https_port"`
}

func (c *orchClient) post(path string, body any) {
	if c == nil {
		return
	}
	b, err := json.Marshal(body)
	if err != nil {
		slog.Warn("Failed to marshal orchestrator event", "error", err, "path", path)
		return
	}

	req, err := http.NewRequest(http.MethodPost, c.base+path, bytes.NewReader(b))
	if err != nil {
		slog.Warn("Failed to create orchestrator request", "error", err, "path", path)
		return
	}

	req.Header.Set("Content-Type", "application/json")
	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	go func() {
		slog.Debug("Sending event to orchestrator", "url", c.base+path)
		resp, err := c.hc.Do(req)
		if err != nil {
			slog.Warn("Failed to send event to orchestrator", "error", err, "url", c.base+path)
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			slog.Warn("Orchestrator returned error status", "status", resp.StatusCode, "url", c.base+path)
		} else {
			slog.Debug("Successfully sent event to orchestrator", "status", resp.StatusCode, "url", c.base+path)
		}
	}()
}

// postSync sends a synchronous POST request to orchestrator (blocks until complete)
// Used for critical events where ordering matters (e.g., stream_started)
func (c *orchClient) postSync(path string, body any) {
	if c == nil {
		return
	}
	b, err := json.Marshal(body)
	if err != nil {
		slog.Warn("Failed to marshal orchestrator event", "error", err, "path", path)
		return
	}

	req, err := http.NewRequest(http.MethodPost, c.base+path, bytes.NewReader(b))
	if err != nil {
		slog.Warn("Failed to create orchestrator request", "error", err, "path", path)
		return
	}

	req.Header.Set("Content-Type", "application/json")
	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	slog.Debug("Sending synchronous event to orchestrator", "url", c.base+path)
	resp, err := c.hc.Do(req)
	if err != nil {
		slog.Warn("Failed to send event to orchestrator", "error", err, "url", c.base+path)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		slog.Warn("Orchestrator returned error status", "status", resp.StatusCode, "url", c.base+path)
	} else {
		slog.Debug("Successfully sent synchronous event to orchestrator", "status", resp.StatusCode, "url", c.base+path)
	}
}

// ReleasePendingStream decrements the pending stream count for an engine
// This should be called after a stream has been reported to the orchestrator or on failure
func (c *orchClient) ReleasePendingStream(engineContainerID string) {
	if c == nil || engineContainerID == "" {
		return
	}

	c.pendingStreamsMu.Lock()
	defer c.pendingStreamsMu.Unlock()

	if count, exists := c.pendingStreams[engineContainerID]; exists && count > 0 {
		c.pendingStreams[engineContainerID]--
		if c.pendingStreams[engineContainerID] == 0 {
			delete(c.pendingStreams, engineContainerID)
		}
		slog.Debug("Released pending stream allocation", "engine_container_id", engineContainerID, "remaining_pending", c.pendingStreams[engineContainerID])
	}
}

func (c *orchClient) EmitStarted(host string, port int, keyType, key, playbackID, statURL, cmdURL, streamID, engineContainerID string) {
	debugLog := debug.GetDebugLogger()
	startTime := time.Now()
	
	if c == nil {
		return
	}

	ev := startedEvent{ContainerID: c.containerID}
	ev.Engine.Host, ev.Engine.Port = host, port
	ev.Stream.KeyType, ev.Stream.Key = keyType, key
	ev.Session.PlaybackSessionID = playbackID
	ev.Session.StatURL, ev.Session.CommandURL = statURL, cmdURL
	ev.Session.IsLive = 1
	ev.Labels = map[string]string{"stream_id": streamID}

	// Add debug logging for orchestrator integration
	slog.Debug("Emitting stream_started event to orchestrator",
		"stream_id", streamID, "key_type", keyType, "key", key,
		"host", host, "port", port, "playback_id", playbackID)

	// Post event synchronously to ensure ordering (started before ended)
	c.postSync("/events/stream_started", ev)
	
	duration := time.Since(startTime)
	debugLog.LogStreamEvent("stream_started", streamID, engineContainerID, duration, map[string]interface{}{
		"host":        host,
		"port":        port,
		"key_type":    keyType,
		"key":         key,
		"playback_id": playbackID,
	})
	
	// Release the pending stream allocation after reporting to orchestrator
	c.ReleasePendingStream(engineContainerID)
}

func (c *orchClient) EmitEnded(streamID, reason string) {
	debugLog := debug.GetDebugLogger()
	startTime := time.Now()
	
	if c == nil || streamID == "" {
		return
	}

	// Check if we've already emitted ended for this stream (idempotency protection)
	c.endedStreamsMu.Lock()
	if c.endedStreams[streamID] {
		c.endedStreamsMu.Unlock()
		slog.Debug("Stream already ended, skipping duplicate EmitEnded",
			"stream_id", streamID, "reason", reason)
		return
	}
	// Mark as ended before releasing lock to prevent race
	c.endedStreams[streamID] = true
	c.endedStreamsMu.Unlock()

	ev := endedEvent{ContainerID: c.containerID, StreamID: streamID, Reason: reason}

	// Add debug logging for orchestrator integration
	slog.Debug("Emitting stream_ended event to orchestrator",
		"stream_id", streamID, "reason", reason, "container_id", c.containerID)

	c.post("/events/stream_ended", ev)
	
	duration := time.Since(startTime)
	debugLog.LogStreamEvent("stream_ended", streamID, c.containerID, duration, map[string]interface{}{
		"reason": reason,
	})
}

// GetEngines retrieves all available engines from the orchestrator
// Results are cached for a short duration to reduce concurrent query load
func (c *orchClient) GetEngines() ([]engineState, error) {
	if c == nil {
		return nil, fmt.Errorf("orchestrator client not configured")
	}

	// Check cache first with read lock
	c.engineCacheMu.RLock()
	if time.Since(c.engineCacheTime) < c.engineCacheDuration && c.engineCache != nil {
		cachedEngines := make([]engineState, len(c.engineCache))
		copy(cachedEngines, c.engineCache)
		c.engineCacheMu.RUnlock()
		slog.Debug("Returning cached engine list", "count", len(cachedEngines), "age", time.Since(c.engineCacheTime))
		return cachedEngines, nil
	}
	c.engineCacheMu.RUnlock()

	// Cache miss or expired, fetch fresh data
	req, err := http.NewRequest(http.MethodGet, c.base+"/engines", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to get engines: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("orchestrator returned status %d", resp.StatusCode)
	}

	var engines []engineState
	if err := json.NewDecoder(resp.Body).Decode(&engines); err != nil {
		return nil, fmt.Errorf("failed to decode engines response: %w", err)
	}

	// Update cache with write lock
	c.engineCacheMu.Lock()
	c.engineCache = engines
	c.engineCacheTime = time.Now()
	c.engineCacheMu.Unlock()

	slog.Debug("Fetched and cached engine list", "count", len(engines))
	return engines, nil
}

// GetEngineStreams retrieves streams for a specific engine
func (c *orchClient) GetEngineStreams(containerID string) ([]streamState, error) {
	if c == nil {
		return nil, fmt.Errorf("orchestrator client not configured")
	}

	req, err := http.NewRequest(http.MethodGet, c.base+"/streams?container_id="+containerID+"&status=started", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to get streams: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("orchestrator returned status %d", resp.StatusCode)
	}

	var streams []streamState
	if err := json.NewDecoder(resp.Body).Decode(&streams); err != nil {
		return nil, fmt.Errorf("failed to decode streams response: %w", err)
	}

	return streams, nil
}

// calculateWaitTime determines how long to wait before retrying based on recovery ETA
func calculateWaitTime(recoveryETA, attempt int) int {
	if recoveryETA > 0 {
		// Wait for half the ETA on first retry
		if attempt == 1 {
			return recoveryETA / 2
		}
		// Use full ETA for subsequent retries
		return recoveryETA
	}

	// Exponential backoff if no ETA provided
	waitTime := 30 * (1 << uint(attempt))
	if waitTime > 120 {
		return 120
	}
	return waitTime
}

// ProvisionWithRetry provisions a new acestream engine with intelligent retry logic
func (c *orchClient) ProvisionWithRetry(maxRetries int) (*aceProvisionResponse, error) {
	debugLog := debug.GetDebugLogger()
	startTime := time.Now()
	
	if c == nil {
		return nil, fmt.Errorf("orchestrator client not configured")
	}

	var lastErr error

	for attempt := 0; attempt < maxRetries; attempt++ {
		// Wait before retry if we had a structured error with recovery ETA
		// (we extract this from the previous error, not from health check)
		if attempt > 0 && lastErr != nil {
			var prevErr *ProvisioningError
			if errors.As(lastErr, &prevErr) && prevErr.Details.RecoveryETASeconds > 0 {
				waitTime := calculateWaitTime(prevErr.Details.RecoveryETASeconds, attempt)
				slog.Info("Waiting before retry based on previous error",
					"attempt", attempt+1,
					"wait_seconds", waitTime,
					"reason", prevErr.Details.Code)
				time.Sleep(time.Duration(waitTime) * time.Second)
			}
		}

		attemptStart := time.Now()
		// Attempt provisioning
		resp, err := c.ProvisionAcestream()
		attemptDuration := time.Since(attemptStart)
		
		if err == nil {
			totalDuration := time.Since(startTime)
			debugLog.LogProvisioning("provision_success", totalDuration, true, "", attempt)
			return resp, nil
		}

		lastErr = err
		
		// Log the failed attempt
		debugLog.LogProvisioning("provision_attempt_failed", attemptDuration, false, err.Error(), attempt+1)

		// Check if we should retry based on error type
		var provErr *ProvisioningError
		if errors.As(err, &provErr) {
			// Structured error
			if !provErr.Details.ShouldWait {
				// Don't retry permanent errors
				totalDuration := time.Since(startTime)
				debugLog.LogProvisioning("provision_failed_permanent", totalDuration, false, err.Error(), attempt+1)
				return nil, err
			}

			slog.Warn("Provisioning failed, will retry",
				"attempt", attempt+1,
				"code", provErr.Details.Code,
				"recovery_eta", provErr.Details.RecoveryETASeconds)
			
			// Log stress events for specific error codes
			if provErr.Details.Code == "circuit_breaker" {
				debugLog.LogStressEvent(
					"provisioning_circuit_breaker",
					"critical",
					"Provisioning blocked by circuit breaker",
					map[string]interface{}{
						"attempt": attempt + 1,
						"error":   err.Error(),
					},
				)
			}
		} else {
			// Legacy error handling - retry on temporary errors
			slog.Warn("Provision attempt failed", "attempt", attempt+1, "error", err)
		}
	}

	totalDuration := time.Since(startTime)
	debugLog.LogProvisioning("provision_failed", totalDuration, false, lastErr.Error(), maxRetries)
	return nil, fmt.Errorf("provisioning failed after %d attempts: %w", maxRetries, lastErr)
}

// ProvisionAcestream provisions a new acestream engine
func (c *orchClient) ProvisionAcestream() (*aceProvisionResponse, error) {
	if c == nil {
		return nil, fmt.Errorf("orchestrator client not configured")
	}

	reqData := aceProvisionRequest{
		Labels: map[string]string{},
		Env:    map[string]string{},
	}

	body, err := json.Marshal(reqData)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal provision request: %w", err)
	}

	req, err := http.NewRequest(http.MethodPost, c.base+"/provision/acestream", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create provision request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to provision acestream: %w", err)
	}
	defer resp.Body.Close()

	// Success
	if resp.StatusCode == http.StatusOK {
		var provResp aceProvisionResponse
		if err := json.NewDecoder(resp.Body).Decode(&provResp); err != nil {
			return nil, fmt.Errorf("failed to decode provision response: %w", err)
		}
		return &provResp, nil
	}

	// Parse error response (supports both structured and legacy formats)
	provError, parseErr := parseProvisionError(resp)
	if parseErr != nil {
		// Fallback if parsing fails
		return nil, fmt.Errorf("provisioning failed with status %d: %v", resp.StatusCode, parseErr)
	}

	// Return structured error
	return nil, &ProvisioningError{
		StatusCode: resp.StatusCode,
		Details:    provError,
	}
}

// SelectBestEngine selects the best available engine based on load balancing rules
// Returns host, port, containerID, and error. Prioritizes healthy engines first, then among engines with the same
// health status and stream count, chooses the one with the oldest last_stream_usage timestamp.
// The containerID is used internally to track pending stream allocations and prevent race conditions.
func (c *orchClient) SelectBestEngine() (string, int, string, error) {
	debugLog := debug.GetDebugLogger()
	startTime := time.Now()
	
	if c == nil {
		return "", 0, "", fmt.Errorf("orchestrator client not configured")
	}

	// Get all available engines
	engines, err := c.GetEngines()
	if err != nil {
		duration := time.Since(startTime)
		debugLog.LogEngineSelection("select_best_engine", "", 0, "", duration, err.Error())
		return "", 0, "", fmt.Errorf("failed to get engines: %w", err)
	}

	slog.Debug("Found engines from orchestrator", "count", len(engines), "max_streams_per_engine", c.maxStreamsPerEngine)

	// Collect engines with their stream counts for prioritization
	type engineWithLoad struct {
		engine        engineState
		activeStreams int
	}

	var availableEngines []engineWithLoad

	// Check stream count for each engine
	for _, engine := range engines {
		streams, err := c.GetEngineStreams(engine.ContainerID)
		if err != nil {
			slog.Warn("Failed to get streams for engine", "container_id", engine.ContainerID, "error", err)
			continue
		}

		activeStreams := 0
		for _, stream := range streams {
			if stream.Status == "started" {
				activeStreams++
			}
		}

		// Add pending streams to get total load
		c.pendingStreamsMu.Lock()
		pendingCount := c.pendingStreams[engine.ContainerID]
		c.pendingStreamsMu.Unlock()
		
		totalStreams := activeStreams + pendingCount

		slog.Debug("Engine stream count", "container_id", engine.ContainerID, "active_streams", activeStreams, "pending_streams", pendingCount, "total_streams", totalStreams, "host", engine.Host, "port", engine.Port, "max_allowed", c.maxStreamsPerEngine, "health_status", engine.HealthStatus, "last_health_check", engine.LastHealthCheck.Format(time.RFC3339), "last_stream_usage", engine.LastStreamUsage.Format(time.RFC3339))

		// Only consider engines that have capacity (including pending allocations)
		if totalStreams < c.maxStreamsPerEngine {
			availableEngines = append(availableEngines, engineWithLoad{
				engine:        engine,
				activeStreams: totalStreams,
			})
		}
	}

	// If no engines have capacity, provision a new one
	if len(availableEngines) == 0 {
		// Check if we can provision before attempting
		canProvision, shouldWait, recoveryETA := c.GetProvisioningStatus()

		if !canProvision {
			if shouldWait {
				// Return structured error with recovery information
				return "", 0, "", &ProvisioningError{
					StatusCode: http.StatusServiceUnavailable,
					Details: &ProvisionError{
						Code:               c.health.blockedReasonCode,
						Message:            c.health.blockedReason,
						RecoveryETASeconds: recoveryETA,
						ShouldWait:         true,
						CanRetry:           true,
					},
				}
			}
			return "", 0, "", fmt.Errorf("cannot provision: %s", c.health.blockedReason)
		}

		slog.Info("No available engines found (all at capacity), provisioning new acestream engine")

		// Use retry logic for provisioning
		provResp, err := c.ProvisionWithRetry(3)
		if err != nil {
			return "", 0, "", err
		}

		// Increment pending streams for the new engine
		c.pendingStreamsMu.Lock()
		c.pendingStreams[provResp.ContainerID]++
		c.pendingStreamsMu.Unlock()

		// Shorter wait since orchestrator now syncs state immediately
		time.Sleep(5 * time.Second)

		// Verify engine appears in list
		engines, err := c.GetEngines()
		if err == nil {
			for _, eng := range engines {
				if eng.ContainerID == provResp.ContainerID {
					slog.Info("Provisioned engine found in orchestrator",
						"container_id", provResp.ContainerID,
						"container_name", provResp.ContainerName)
					return "localhost", provResp.HostHTTPPort, provResp.ContainerID, nil
				}
			}
		}

		// Still not found, wait a bit more and return anyway
		slog.Warn("Engine not immediately available, continuing anyway")
		time.Sleep(5 * time.Second)

		slog.Info("Provisioned new engine", "container_id", provResp.ContainerID, "container_name", provResp.ContainerName, "host_port", provResp.HostHTTPPort, "container_port", provResp.ContainerHTTPPort)

		// Use orchestrator-provided host port mapping directly
		return "localhost", provResp.HostHTTPPort, provResp.ContainerID, nil
	}

	// Sort engines by health status first (healthy engines prioritized),
	// then by stream count (ascending), then by last_stream_usage (ascending - oldest first)
	for i := 0; i < len(availableEngines); i++ {
		for j := i + 1; j < len(availableEngines); j++ {
			iEngine := availableEngines[i]
			jEngine := availableEngines[j]

			// Primary sort: by health status (healthy engines first)
			iHealthy := iEngine.engine.HealthStatus == "healthy"
			jHealthy := jEngine.engine.HealthStatus == "healthy"

			if iHealthy != jHealthy {
				// If one is healthy and other is not, prioritize healthy
				if jHealthy && !iHealthy {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				}
			} else {
				// Both have same health status, sort by active stream count
				if iEngine.activeStreams > jEngine.activeStreams {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				} else if iEngine.activeStreams == jEngine.activeStreams {
					// Same health and stream count, sort by last_stream_usage (ascending - oldest first)
					// This ensures that among engines with same health and stream count, we pick the one unused the longest
					if iEngine.engine.LastStreamUsage.After(jEngine.engine.LastStreamUsage) {
						availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
					}
				}
			}
		}
	}

	// Select the engine with the least active streams (empty engines are prioritized)
	bestEngine := availableEngines[0]
	host := bestEngine.engine.Host
	port := bestEngine.engine.Port
	containerID := bestEngine.engine.ContainerID

	// Increment pending streams counter to prevent race conditions
	c.pendingStreamsMu.Lock()
	c.pendingStreams[containerID]++
	c.pendingStreamsMu.Unlock()

	slog.Info("Selected best available engine",
		"container_id", containerID,
		"container_name", bestEngine.engine.ContainerName,
		"host", host,
		"port", port,
		"active_streams", bestEngine.activeStreams,
		"max_streams", c.maxStreamsPerEngine,
		"health_status", bestEngine.engine.HealthStatus,
		"last_health_check", bestEngine.engine.LastHealthCheck.Format(time.RFC3339),
		"last_stream_usage", bestEngine.engine.LastStreamUsage.Format(time.RFC3339))

	// Log engine selection for debugging
	duration := time.Since(startTime)
	debugLog.LogEngineSelection("select_best_engine", host, port, containerID, duration, "")

	// Detect slow engine selection
	if duration > 2*time.Second {
		debugLog.LogStressEvent(
			"slow_engine_selection",
			"warning",
			fmt.Sprintf("Engine selection took %.2fs", duration.Seconds()),
			map[string]interface{}{
				"duration":     duration.Seconds(),
				"engine_count": len(engines),
			},
		)
	}

	return host, port, containerID, nil
}
