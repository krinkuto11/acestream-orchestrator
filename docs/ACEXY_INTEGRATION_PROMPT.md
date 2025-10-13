# Acexy Proxy Integration Prompt

## Context

The orchestrator has been enhanced to provide comprehensive status information and structured error responses for better communication with proxies. The acexy proxy needs to be updated to leverage these improvements and handle failure scenarios gracefully.

## Problem Statement

Currently, when the orchestrator experiences temporary issues (VPN disconnection, circuit breaker activation, capacity exhaustion), the acexy proxy treats these as permanent failures and stops streams. This causes:
- Unnecessary stream interruptions during recoverable failures
- Player retries that overwhelm the system
- Poor user experience during temporary outages

## Objective

Update the acexy proxy to:
1. Understand structured error responses from the orchestrator
2. Implement graceful degradation during temporary failures
3. Keep streams alive during short outages
4. Use intelligent retry strategies based on recovery ETAs
5. Prevent race conditions with pending stream tracking (already implemented)

## Current State Analysis

### What Works Well
- Health monitoring (polling /orchestrator/status every 30s)
- Pending stream tracking to prevent race conditions
- Engine selection logic with health awareness
- Idempotent stream event reporting

### What Needs Improvement
- Error handling for provisioning failures
- Stream keep-alive during temporary orchestrator issues
- Retry logic based on error codes and recovery ETAs
- Client communication during degraded states

## Enhanced Orchestrator API

### 1. /orchestrator/status Endpoint

**Before:**
```json
{
  "status": "healthy",
  "provisioning": {
    "can_provision": false,
    "blocked_reason": "Circuit breaker is open"
  }
}
```

**After:**
```json
{
  "status": "degraded",
  "engines": {
    "total": 10,
    "running": 10,
    "healthy": 9,
    "unhealthy": 1
  },
  "provisioning": {
    "can_provision": false,
    "circuit_breaker_state": "open",
    "blocked_reason": "Circuit breaker is open",
    "blocked_reason_details": {
      "code": "circuit_breaker",
      "message": "Circuit breaker is open due to repeated failures",
      "recovery_eta_seconds": 180,
      "can_retry": false,
      "should_wait": true
    }
  },
  "vpn": {
    "enabled": true,
    "connected": true,
    "health": "healthy"
  },
  "capacity": {
    "total": 10,
    "used": 8,
    "available": 2,
    "max_replicas": 20,
    "min_replicas": 10
  },
  "timestamp": "2025-10-13T14:00:00Z"
}
```

### 2. POST /provision/acestream Endpoint

**Before (503):**
```json
{
  "detail": "Provisioning temporarily unavailable: Circuit breaker is open"
}
```

**After (503):**
```json
{
  "detail": {
    "error": "provisioning_blocked",
    "code": "circuit_breaker",
    "message": "Circuit breaker is open due to repeated failures",
    "recovery_eta_seconds": 180,
    "can_retry": false,
    "should_wait": true
  }
}
```

### Error Codes
- `vpn_disconnected`: VPN is down, typically recovers in 60s
- `circuit_breaker`: Too many failures, wait for recovery timeout
- `max_capacity`: All engines at capacity, wait for streams to end
- `vpn_error`: VPN error during provisioning
- `general_error`: Other provisioning errors

## Required Changes to Acexy

### 1. Update Error Handling Structures

Add structured error types:

```go
// orchestrator_events.go

type ProvisionError struct {
    Error                string `json:"error"`
    Code                 string `json:"code"`
    Message              string `json:"message"`
    RecoveryETASeconds   int    `json:"recovery_eta_seconds"`
    CanRetry             bool   `json:"can_retry"`
    ShouldWait           bool   `json:"should_wait"`
}

type HTTPError struct {
    StatusCode int
    Detail     interface{} // Can be string or ProvisionError
}

// Parse error response
func parseProvisionError(resp *http.Response) (*ProvisionError, error) {
    var errorResp struct {
        Detail json.RawMessage `json:"detail"`
    }
    
    if err := json.NewDecoder(resp.Body).Decode(&errorResp); err != nil {
        return nil, err
    }
    
    // Try to parse as structured error
    var provError ProvisionError
    if err := json.Unmarshal(errorResp.Detail, &provError); err == nil {
        return &provError, nil
    }
    
    // Fallback to string error
    var stringDetail string
    if err := json.Unmarshal(errorResp.Detail, &stringDetail); err == nil {
        return &ProvisionError{
            Error:   "provisioning_failed",
            Code:    "general_error",
            Message: stringDetail,
            ShouldWait: false,
        }, nil
    }
    
    return nil, errors.New("failed to parse error response")
}
```

### 2. Enhance Orchestrator Status Monitoring

Update `updateHealth()` to track more state:

```go
// orchestrator_events.go

type OrchestratorHealth struct {
    mu                 sync.RWMutex
    lastCheck          time.Time
    status             string
    canProvision       bool
    blockedReason      string
    blockedReasonCode  string      // NEW
    recoveryETA        int         // NEW
    shouldWait         bool        // NEW
    vpnConnected       bool
    capacity           CapacityInfo // NEW
}

type CapacityInfo struct {
    Total     int
    Used      int
    Available int
}

func (c *orchClient) updateHealth() {
    // ... existing code ...
    
    var status struct {
        Status       string `json:"status"`
        VPN          struct {
            Connected bool `json:"connected"`
        } `json:"vpn"`
        Provisioning struct {
            CanProvision         bool            `json:"can_provision"`
            BlockedReason        string          `json:"blocked_reason"`
            BlockedReasonDetails *ProvisionError `json:"blocked_reason_details"`
        } `json:"provisioning"`
        Capacity struct {
            Total     int `json:"total"`
            Used      int `json:"used"`
            Available int `json:"available"`
        } `json:"capacity"`
    }
    
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
    
    // Extract details from blocked reason
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
        "blocked_code", c.health.blockedReasonCode,
        "recovery_eta", c.health.recoveryETA,
        "capacity_available", c.health.capacity.Available)
}
```

### 3. Implement Intelligent Provisioning with Retry

Update `ProvisionAcestream()` to handle errors better:

```go
// orchestrator_events.go

func (c *orchClient) ProvisionAcestream() (*aceProvisionResponse, error) {
    // ... existing request setup ...
    
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
    
    // Parse error
    provError, parseErr := parseProvisionError(resp)
    if parseErr != nil {
        return nil, fmt.Errorf("provisioning failed with status %d: %v", resp.StatusCode, parseErr)
    }
    
    // Return structured error
    return nil, &ProvisioningError{
        StatusCode: resp.StatusCode,
        Details:    provError,
    }
}

type ProvisioningError struct {
    StatusCode int
    Details    *ProvisionError
}

func (e *ProvisioningError) Error() string {
    return fmt.Sprintf("provisioning %s: %s", e.Details.Code, e.Details.Message)
}
```

### 4. Add Retry Logic with Backoff

```go
// orchestrator_events.go

func (c *orchClient) ProvisionWithIntelligentRetry(maxAttempts int) (*aceProvisionResponse, error) {
    var lastErr error
    
    for attempt := 0; attempt < maxAttempts; attempt++ {
        // Check health before attempting
        canProvision, shouldWait, recoveryETA := c.GetProvisioningStatus()
        
        if !canProvision && !shouldWait {
            // Permanent error, don't retry
            return nil, fmt.Errorf("provisioning blocked permanently: %s", c.health.blockedReason)
        }
        
        if !canProvision && shouldWait && attempt > 0 {
            // Wait based on recovery ETA
            waitTime := calculateWaitTime(recoveryETA, attempt)
            slog.Info("Waiting before retry",
                "attempt", attempt+1,
                "wait_seconds", waitTime,
                "reason", c.health.blockedReasonCode)
            time.Sleep(time.Duration(waitTime) * time.Second)
        }
        
        // Attempt provisioning
        resp, err := c.ProvisionAcestream()
        if err == nil {
            return resp, nil
        }
        
        lastErr = err
        
        // Check if we should retry
        var provErr *ProvisioningError
        if errors.As(err, &provErr) {
            if !provErr.Details.ShouldWait {
                // Don't retry permanent errors
                return nil, err
            }
            
            slog.Warn("Provisioning failed, will retry",
                "attempt", attempt+1,
                "code", provErr.Details.Code,
                "recovery_eta", provErr.Details.RecoveryETASeconds)
        }
    }
    
    return nil, fmt.Errorf("provisioning failed after %d attempts: %w", maxAttempts, lastErr)
}

func calculateWaitTime(recoveryETA, attempt int) int {
    if recoveryETA > 0 {
        // Wait for half the ETA on first retry
        if attempt == 1 {
            return recoveryETA / 2
        }
        // Use full ETA for subsequent retries
        return recoveryETA
    }
    
    // Exponential backoff if no ETA
    return min(30 * (1 << uint(attempt)), 120)
}

func (c *orchClient) GetProvisioningStatus() (canProvision bool, shouldWait bool, recoveryETA int) {
    c.health.mu.RLock()
    defer c.health.mu.RUnlock()
    return c.health.canProvision, c.health.shouldWait, c.health.recoveryETA
}
```

### 5. Update SelectBestEngine to Handle Failures Gracefully

```go
// orchestrator_events.go

func (c *orchClient) SelectBestEngine() (string, int, string, error) {
    // ... existing engine selection logic ...
    
    // If no engines have capacity, check if we should try provisioning
    if len(availableEngines) == 0 {
        canProvision, shouldWait, recoveryETA := c.GetProvisioningStatus()
        
        if !canProvision {
            if shouldWait {
                return "", 0, "", &ProvisioningError{
                    StatusCode: 503,
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
        
        slog.Info("No available engines, provisioning new engine")
        
        // Use intelligent retry logic
        provResp, err := c.ProvisionWithIntelligentRetry(3)
        if err != nil {
            return "", 0, "", err
        }
        
        // ... rest of provisioning logic ...
    }
    
    // ... existing engine selection ...
}
```

### 6. Update proxy.go to Handle Errors Better

```go
// proxy.go

func (p *Proxy) HandleStream(w http.ResponseWriter, r *http.Request) {
    // ... existing code up to SelectBestEngine ...
    
    if p.Orch != nil {
        host, port, engineContainerID, err := p.Orch.SelectBestEngine()
        if err != nil {
            // Check if it's a structured provisioning error
            var provErr *ProvisioningError
            if errors.As(err, &provErr) {
                p.handleProvisioningError(w, provErr)
                return
            }
            
            // Check for other specific errors
            if strings.Contains(err.Error(), "VPN") {
                slog.Error("Stream failed due to VPN issue", "error", err)
                http.Error(w, "Service temporarily unavailable: VPN connection required", http.StatusServiceUnavailable)
                return
            }
            
            // ... existing error handling ...
        } else {
            selectedHost = host
            selectedPort = port
            selectedEngineContainerID = engineContainerID
            slog.Info("Selected engine from orchestrator", "host", host, "port", port)
        }
    }
    
    // ... rest of stream handling ...
}

func (p *Proxy) handleProvisioningError(w http.ResponseWriter, err *ProvisioningError) {
    details := err.Details
    
    // Log with structured data
    slog.Error("Provisioning blocked",
        "code", details.Code,
        "message", details.Message,
        "recovery_eta", details.RecoveryETASeconds,
        "should_wait", details.ShouldWait)
    
    // Set Retry-After header if recovery ETA is available
    if details.RecoveryETASeconds > 0 {
        w.Header().Set("Retry-After", fmt.Sprintf("%d", details.RecoveryETASeconds))
    }
    
    // Return user-friendly error based on code
    var userMessage string
    switch details.Code {
    case "vpn_disconnected":
        userMessage = "Service temporarily unavailable: VPN connection is being restored"
    case "circuit_breaker":
        userMessage = "Service temporarily unavailable: System is recovering from errors"
    case "max_capacity":
        userMessage = "Service at capacity: Please try again in a moment"
    default:
        userMessage = "Service temporarily unavailable: " + details.Message
    }
    
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(http.StatusServiceUnavailable)
    json.NewEncoder(w).Encode(map[string]interface{}{
        "error": userMessage,
        "retry_after": details.RecoveryETASeconds,
    })
}
```

### 7. Add Request Queuing for Capacity Issues (Optional)

For production environments, consider adding a request queue:

```go
// proxy.go

type StreamQueue struct {
    requests chan *StreamRequest
    mu       sync.Mutex
}

type StreamRequest struct {
    Writer      http.ResponseWriter
    Request     *http.Request
    AceID       *acexy.AceID
    EnqueueTime time.Time
}

func (p *Proxy) HandleStream(w http.ResponseWriter, r *http.Request) {
    // ... parse request ...
    
    // Check if we should queue due to capacity
    if p.Orch != nil {
        status := p.Orch.GetHealthStatus()
        if status.Provisioning.BlockedReasonCode == "max_capacity" {
            // Queue the request
            req := &StreamRequest{
                Writer:      w,
                Request:     r,
                AceID:       aceId,
                EnqueueTime: time.Now(),
            }
            
            if p.queue.TryEnqueue(req) {
                slog.Info("Request queued due to capacity", "queue_size", p.queue.Size())
                // Don't return yet - wait for capacity
                // This keeps the HTTP connection open
                return
            } else {
                // Queue full
                http.Error(w, "Service overloaded: Queue full", http.StatusServiceUnavailable)
                return
            }
        }
    }
    
    // ... continue with normal flow ...
}
```

## Testing Recommendations

### Unit Tests

1. Test error parsing:
```go
func TestParseProvisionError(t *testing.T) {
    testCases := []struct {
        name     string
        response string
        expected *ProvisionError
    }{
        {
            name: "VPN disconnected",
            response: `{"detail": {"error": "provisioning_blocked", "code": "vpn_disconnected", "message": "VPN down", "recovery_eta_seconds": 60, "should_wait": true}}`,
            expected: &ProvisionError{
                Code:               "vpn_disconnected",
                RecoveryETASeconds: 60,
                ShouldWait:         true,
            },
        },
        // ... more cases
    }
    
    // ... test implementation
}
```

2. Test retry logic:
```go
func TestProvisionWithIntelligentRetry(t *testing.T) {
    // Mock orchestrator with various error scenarios
    // Verify retry behavior, backoff, and eventual success/failure
}
```

### Integration Tests

1. Test with orchestrator in various states:
   - VPN down
   - Circuit breaker open
   - At capacity
   - Recovering from errors

2. Load test to verify race condition prevention still works

3. Test graceful degradation:
   - Start streams
   - Trigger VPN disconnect
   - Verify streams don't fail immediately
   - Verify recovery after VPN reconnects

## Migration Strategy

### Phase 1: Add New Types and Parsing (Non-Breaking)
- Add new error types
- Update parsing functions
- Keep existing behavior as fallback

### Phase 2: Enhance Health Monitoring
- Update `updateHealth()` to track new fields
- Add helper methods for provisioning status

### Phase 3: Update Error Handling
- Implement `handleProvisioningError()`
- Add retry logic with intelligent backoff
- Update `SelectBestEngine()` to use new error handling

### Phase 4: Testing and Rollout
- Test with orchestrator v2
- Deploy to staging
- Monitor metrics
- Gradual rollout to production

## Monitoring and Observability

Add metrics to track:

```go
var (
    provisioningBlockedCounter = prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "acexy_provisioning_blocked_total",
            Help: "Number of times provisioning was blocked",
        },
        []string{"code"},
    )
    
    provisioningRetryCounter = prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "acexy_provisioning_retries_total",
            Help: "Number of provisioning retries",
        },
        []string{"code", "success"},
    )
    
    orchestratorDegradedGauge = prometheus.NewGauge(
        prometheus.GaugeOpts{
            Name: "acexy_orchestrator_degraded",
            Help: "1 if orchestrator is in degraded state, 0 otherwise",
        },
    )
)

// Update in health monitor
func (c *orchClient) updateHealth() {
    // ... existing code ...
    
    // Update metrics
    if c.health.status == "degraded" {
        orchestratorDegradedGauge.Set(1)
    } else {
        orchestratorDegradedGauge.Set(0)
    }
}

// Track provisioning blocks
func (c *orchClient) SelectBestEngine() (string, int, string, error) {
    // ... when provisioning is blocked ...
    provisioningBlockedCounter.WithLabelValues(c.health.blockedReasonCode).Inc()
}
```

## Success Criteria

The integration is successful when:

1. ✅ Proxy handles all error codes correctly
2. ✅ Streams don't fail during temporary VPN disconnections
3. ✅ Circuit breaker states are respected (no retry when open)
4. ✅ Capacity errors result in queuing or graceful rejection
5. ✅ Recovery ETAs are used for intelligent retry timing
6. ✅ Existing race condition prevention still works
7. ✅ Metrics show improved success rates during failures
8. ✅ User experience improved during temporary outages

## Additional Considerations

### Backward Compatibility

The orchestrator changes are backward compatible:
- Old error format (string) still works
- New format (structured) provides more detail
- Proxy should handle both gracefully

### Performance Impact

- Health monitoring: No change (already polling every 30s)
- Error parsing: Minimal overhead (JSON parsing)
- Retry logic: Only activates on errors
- Pending tracking: Already implemented

### Configuration

Consider adding configuration for:
```go
const (
    maxProvisionRetries = 3
    maxQueueSize        = 100
    maxQueueWaitTime    = 5 * time.Minute
    healthPollInterval  = 30 * time.Second
)
```

## Summary

This integration enhances the acexy proxy to:
1. Understand structured orchestrator errors
2. Make intelligent retry decisions
3. Keep streams alive during temporary failures
4. Provide better user experience
5. Reduce system overload during outages

The changes are incremental and can be tested at each phase. The orchestrator is already updated and backward compatible, so the proxy can be updated gradually.
