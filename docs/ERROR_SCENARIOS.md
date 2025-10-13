# Error Scenarios and Recovery Patterns

This document provides detailed sequence diagrams and recovery patterns for various failure scenarios in the orchestrator-proxy communication.

## Table of Contents
1. [VPN Disconnection](#vpn-disconnection)
2. [Circuit Breaker Activation](#circuit-breaker-activation)
3. [Maximum Capacity](#maximum-capacity)
4. [Engine Startup Timeout](#engine-startup-timeout)
5. [Race Condition Prevention](#race-condition-prevention)

---

## VPN Disconnection

### Sequence Diagram

```
┌──────┐          ┌──────────────┐          ┌────────┐          ┌────────┐
│Client│          │    Proxy     │          │ Orch   │          │ Gluetun│
└──┬───┘          └──────┬───────┘          └───┬────┘          └───┬────┘
   │                     │                      │                    │
   │  1. Stream Request  │                      │                    │
   ├────────────────────>│                      │                    │
   │                     │                      │                    │
   │                     │  2. No engines free  │                    │
   │                     │     need provision   │                    │
   │                     │                      │                    │
   │                     │  3. POST /provision  │                    │
   │                     ├─────────────────────>│                    │
   │                     │                      │                    │
   │                     │                      │  4. Check VPN      │
   │                     │                      ├───────────────────>│
   │                     │                      │                    │
   │                     │                      │ 5. VPN DOWN ❌     │
   │                     │                      │<───────────────────┤
   │                     │                      │                    │
   │                     │  6. 503 Error        │                    │
   │                     │  {                   │                    │
   │                     │   code: "vpn_down"   │                    │
   │                     │   recovery_eta: 60   │                    │
   │                     │   should_wait: true  │                    │
   │                     │  }                   │                    │
   │                     │<─────────────────────┤                    │
   │                     │                      │                    │
   │  7. Keep stream     │                      │                    │
   │     alive with      │                      │                    │
   │     buffer/loading  │                      │                    │
   │<────────────────────┤                      │                    │
   │                     │                      │                    │
   │                     │  8. Poll status      │                    │
   │                     │     every 10s        │                    │
   │                     ├─────────────────────>│                    │
   │                     │                      │                    │
   │                     │  9. Still degraded   │                    │
   │                     │<─────────────────────┤                    │
   │                     │                      │                    │
   │                     │      ... wait ...    │                    │
   │                     │                      │                    │
   │                     │                      │ 10. VPN UP ✓       │
   │                     │                      │<───────────────────┤
   │                     │                      │                    │
   │                     │ 11. Poll status      │                    │
   │                     ├─────────────────────>│                    │
   │                     │                      │                    │
   │                     │ 12. Healthy ✓        │                    │
   │                     │<─────────────────────┤                    │
   │                     │                      │                    │
   │                     │ 13. Retry provision  │                    │
   │                     ├─────────────────────>│                    │
   │                     │                      │                    │
   │                     │ 14. Success          │                    │
   │                     │<─────────────────────┤                    │
   │                     │                      │                    │
   │ 15. Start stream    │                      │                    │
   │<────────────────────┤                      │                    │
   │                     │                      │                    │
```

### Key Points
- **Detection**: Orchestrator detects VPN down via health checks
- **Communication**: Returns 503 with recovery ETA
- **Proxy Action**: Keeps stream alive, polls for recovery
- **Recovery**: Automatic when VPN reconnects
- **Duration**: Typically < 60 seconds

### Code Example (Proxy)
```go
func (p *Proxy) handleProvisionError(err error) error {
    var httpErr *HTTPError
    if errors.As(err, &httpErr) && httpErr.StatusCode == 503 {
        details := httpErr.Details
        
        if details.Code == "vpn_disconnected" && details.ShouldWait {
            slog.Info("VPN disconnected, waiting for recovery",
                "eta_seconds", details.RecoveryETASeconds)
            
            // Keep stream alive with loading indicator
            go p.showLoadingState()
            
            // Wait and retry
            time.Sleep(time.Duration(details.RecoveryETASeconds/2) * time.Second)
            return p.retryProvisioning()
        }
    }
    return err
}
```

---

## Circuit Breaker Activation

### Sequence Diagram

```
┌──────┐          ┌──────────────┐          ┌────────────────┐
│Client│          │    Proxy     │          │  Orchestrator  │
└──┬───┘          └──────┬───────┘          └───────┬────────┘
   │                     │                          │
   │  Multiple provision │                          │
   │  failures occur     │                          │
   │                     │  1. Provision attempt 1  │
   │                     ├─────────────────────────>│
   │                     │  ❌ Failed                │
   │                     │<─────────────────────────┤
   │                     │                          │
   │                     │  2. Provision attempt 2  │
   │                     ├─────────────────────────>│
   │                     │  ❌ Failed                │
   │                     │<─────────────────────────┤
   │                     │                          │
   │                     │        ... (3 more)      │
   │                     │                          │
   │                     │                          │  5 failures
   │                     │                          │  → Open circuit
   │                     │                          │
   │  New request        │                          │
   ├────────────────────>│                          │
   │                     │                          │
   │                     │  6. POST /provision      │
   │                     ├─────────────────────────>│
   │                     │                          │
   │                     │  7. 503 Circuit Open     │
   │                     │  {                       │
   │                     │   code: "circuit_breaker"│
   │                     │   state: "open"          │
   │                     │   recovery_eta: 180      │
   │                     │   can_retry: false       │
   │                     │  }                       │
   │                     │<─────────────────────────┤
   │                     │                          │
   │  Service busy       │                          │
   │  (503)              │                          │
   │<────────────────────┤                          │
   │                     │                          │
   │                     │  8. Monitor status       │
   │                     │     every 30s            │
   │                     ├─────────────────────────>│
   │                     │                          │
   │                     │     ... wait 180s ...    │
   │                     │                          │
   │                     │                          │  Circuit → 
   │                     │                          │  half_open
   │                     │                          │
   │  New request        │                          │
   ├────────────────────>│                          │
   │                     │                          │
   │                     │  9. Retry provision      │
   │                     ├─────────────────────────>│
   │                     │                          │
   │                     │  10. Success ✓           │
   │                     │<─────────────────────────┤
   │                     │                          │
   │                     │                          │  Circuit →
   │                     │                          │  closed
   │  Stream starts      │                          │
   │<────────────────────┤                          │
   │                     │                          │
```

### Key Points
- **Trigger**: 5 consecutive provisioning failures (configurable)
- **States**: CLOSED → OPEN → HALF_OPEN → CLOSED
- **Recovery**: Automatic after timeout (default 300s)
- **Proxy Action**: Queue requests, don't fail immediately
- **Manual Override**: `/health/circuit-breaker/reset` (admin only)

### Code Example (Proxy)
```go
type RequestQueue struct {
    requests chan *StreamRequest
    maxSize  int
}

func (p *Proxy) handleCircuitBreakerOpen(eta int) error {
    if p.queue.Len() >= p.queue.maxSize {
        return errors.New("service overloaded, try again later")
    }
    
    // Enqueue request
    req := &StreamRequest{
        ID:          generateID(),
        EnqueueTime: time.Now(),
    }
    
    p.queue.Enqueue(req)
    
    // Return 503 to client with Retry-After
    return &HTTPError{
        StatusCode: 503,
        Message:    "Service temporarily unavailable",
        RetryAfter: eta,
    }
}
```

---

## Maximum Capacity

### Sequence Diagram

```
┌──────┐          ┌──────────────┐          ┌────────────────┐
│Client│          │    Proxy     │          │  Orchestrator  │
└──┬───┘          └──────┬───────┘          └───────┬────────┘
   │                     │                          │
   │                     │  1. Get status           │
   │                     ├─────────────────────────>│
   │                     │                          │
   │                     │  2. Status               │
   │                     │  {                       │
   │                     │   engines: 20/20 (max)   │
   │                     │   capacity: 0 available  │
   │                     │   streams: 40 active     │
   │                     │  }                       │
   │                     │<─────────────────────────┤
   │                     │                          │
   │  Stream request     │                          │
   ├────────────────────>│                          │
   │                     │                          │
   │                     │  3. Check: No capacity   │
   │                     │                          │
   │                     │  4. POST /provision      │
   │                     ├─────────────────────────>│
   │                     │                          │
   │                     │  5. 503 Max capacity     │
   │                     │  {                       │
   │                     │   code: "max_capacity"   │
   │                     │   recovery_eta: 120      │
   │                     │   should_wait: true      │
   │                     │  }                       │
   │                     │<─────────────────────────┤
   │                     │                          │
   │  503 Service Busy   │                          │
   │  Retry-After: 120   │                          │
   │<────────────────────┤                          │
   │                     │                          │
   │                     │  6. Enqueue request      │
   │                     │                          │
   │                     │     ... streams end ...  │
   │                     │                          │
   │                     │  7. Stream ended event   │
   │                     │<─────────────────────────┤
   │                     │                          │
   │                     │  8. Capacity available   │
   │                     │     process queue        │
   │                     │                          │
   │                     │  9. Select engine        │
   │                     ├─────────────────────────>│
   │                     │                          │
   │                     │  10. Engine available    │
   │                     │<─────────────────────────┤
   │                     │                          │
   │  Stream ready       │                          │
   │<────────────────────┤                          │
   │                     │                          │
```

### Key Points
- **Cause**: All engines at MAX_REPLICAS, all slots used
- **Not an Error**: System is working, just at capacity
- **Proxy Action**: Queue requests, set Retry-After header
- **Recovery**: Natural as streams end and free capacity
- **ETA**: Based on ENGINE_GRACE_PERIOD_S if AUTO_DELETE enabled

### Code Example (Proxy)
```go
func (p *Proxy) handleMaxCapacity(recoveryETA int) error {
    // Check queue size
    if p.queue.Len() >= maxQueueSize {
        return &HTTPError{
            StatusCode: 503,
            Message:    "Service at maximum capacity, queue full",
            RetryAfter: recoveryETA * 2,
        }
    }
    
    // Enqueue with timeout
    req := &StreamRequest{
        ID:          generateID(),
        EnqueueTime: time.Now(),
        MaxWait:     time.Duration(recoveryETA*2) * time.Second,
    }
    
    // Process queue when capacity becomes available
    go p.processQueueWhenAvailable()
    
    return &HTTPError{
        StatusCode: 503,
        Message:    "Service at maximum capacity, request queued",
        RetryAfter: recoveryETA,
    }
}
```

---

## Engine Startup Timeout

### Sequence Diagram

```
┌──────┐          ┌──────────────┐          ┌────────────────┐          ┌────────┐
│Client│          │    Proxy     │          │  Orchestrator  │          │ Engine │
└──┬───┘          └──────┬───────┘          └───────┬────────┘          └───┬────┘
   │                     │                          │                       │
   │  Stream request     │                          │                       │
   ├────────────────────>│                          │                       │
   │                     │                          │                       │
   │                     │  1. POST /provision      │                       │
   │                     ├─────────────────────────>│                       │
   │                     │                          │                       │
   │                     │  2. Container created    │  3. Starting...       │
   │                     │<─────────────────────────┤──────────────────────>│
   │                     │  {container_id, port}    │                       │
   │                     │                          │                       │
   │                     │  4. Try connect          │                       │
   │                     ├──────────────────────────┼──────────────────────>│
   │                     │                          │                       │
   │                     │  ❌ Timeout (not ready)  │                       │
   │                     │<─────────────────────────┼───────────────────────┤
   │                     │                          │                       │
   │                     │  5. Wait 2s & retry      │                       │
   │                     │                          │                       │
   │                     │  6. Try connect          │                       │
   │                     ├──────────────────────────┼──────────────────────>│
   │                     │                          │                       │
   │                     │  ❌ Timeout              │                       │
   │                     │<─────────────────────────┼───────────────────────┤
   │                     │                          │                       │
   │                     │  7. Wait 4s & retry      │                       │
   │                     │                          │                       │
   │                     │  8. Try connect          │                       │
   │                     ├──────────────────────────┼──────────────────────>│
   │                     │                          │                       │
   │                     │  ✓ Connected!            │                       │
   │                     │<─────────────────────────┼───────────────────────┤
   │                     │                          │                       │
   │  Stream ready       │                          │                       │
   │<────────────────────┤                          │                       │
   │                     │                          │                       │
```

### Key Points
- **Cause**: Engine provisioned but not yet ready
- **Expected**: Normal during high load
- **Retry Pattern**: Exponential backoff (1s, 2s, 4s, 8s)
- **Max Retries**: 4-5 attempts
- **Fallback**: Try different engine if available
- **Failure**: Emit stream_ended if all retries exhausted

### Code Example (Proxy)
```go
func (p *Proxy) connectToEngineWithRetry(host string, port int, maxRetries int) error {
    backoff := time.Second
    
    for i := 0; i < maxRetries; i++ {
        err := p.tryConnect(host, port)
        if err == nil {
            return nil // Success
        }
        
        if i < maxRetries-1 {
            slog.Warn("Engine not ready, retrying",
                "attempt", i+1,
                "backoff", backoff,
                "error", err)
            time.Sleep(backoff)
            backoff *= 2
        }
    }
    
    // All retries failed, try fallback
    return p.selectAlternativeEngine()
}
```

---

## Race Condition Prevention

### The Problem

Multiple concurrent requests selecting the same engine:

```
Time  Request A         Request B         Request C         Orchestrator
T0    Query engines                                         Engine-1: 0 streams
T1    See 0 streams     Query engines                       Engine-1: 0 streams
T2    Select Engine-1   See 0 streams     Query engines     Engine-1: 0 streams
T3                      Select Engine-1   See 0 streams
T4                                        Select Engine-1
T5    Start stream                                          Engine-1: 1 stream
T6                      Start stream                        Engine-1: 2 streams
T7                                        Start stream      Engine-1: 3 streams ❌
```

### The Solution: Pending Stream Tracking

```
┌──────────────┐          ┌────────────────┐          ┌────────────────┐
│   Request A  │          │   Request B    │          │   Request C    │
└──────┬───────┘          └───────┬────────┘          └───────┬────────┘
       │                          │                           │
       │  1. Get engines          │                           │
       │  + pending counts        │                           │
       ├─────────────────────────>│                           │
       │                          │                           │
       │  Engine-1: 0 active      │                           │
       │           0 pending      │                           │
       │  = 0 total ✓             │                           │
       │                          │                           │
       │  2. Select Engine-1      │                           │
       │  3. ++ pending[Engine-1] │  4. Get engines          │
       │     = 1 ✓                │     + pending counts     │
       │                          ├─────────────────────────>│
       │                          │                          │
       │                          │  Engine-1: 0 active      │
       │                          │           1 pending      │
       │                          │  = 1 total ✓             │
       │                          │                          │
       │                          │  5. Select Engine-1      │
       │                          │  6. ++ pending[Engine-1] │  7. Get engines
       │                          │     = 2 ✓ (at limit)    │     + pending
       │                          │                          ├────────────────>
       │                          │                          │
       │                          │                          │  Engine-1: 0 active
       │                          │                          │           2 pending
       │                          │                          │  = 2 total ❌
       │                          │                          │
       │                          │                          │  8. Select Engine-2
       │                          │                          │     (Engine-1 full)
       │                          │                          │
       │  9. Emit stream_started  │                          │
       │     -- pending[Engine-1] │                          │
       │        = 1                │                          │
       │                          │                          │
       │                          │  10. Emit stream_started │
       │                          │      -- pending[Engine-1]│
       │                          │         = 0              │
```

### Key Points
- **Local Tracking**: Proxy maintains pending allocation map
- **Atomic Operations**: Increment when selected, decrement when reported
- **Thread Safety**: Protected by mutex
- **Cleanup**: Release pending count after emitting stream_started
- **Race-Free**: Multiple concurrent requests won't overload same engine

### Code Example (Proxy)
```go
type orchClient struct {
    pendingStreams   map[string]int // containerID -> count
    pendingStreamsMu sync.Mutex
    // ... other fields
}

func (c *orchClient) SelectBestEngine() (string, int, string, error) {
    engines, _ := c.GetEngines()
    
    for _, engine := range engines {
        activeStreams := len(engine.Streams)
        
        // Add pending allocations to total
        c.pendingStreamsMu.Lock()
        pendingCount := c.pendingStreams[engine.ContainerID]
        totalStreams := activeStreams + pendingCount
        c.pendingStreamsMu.Unlock()
        
        if totalStreams < c.maxStreamsPerEngine {
            // Atomically reserve this engine
            c.pendingStreamsMu.Lock()
            c.pendingStreams[engine.ContainerID]++
            c.pendingStreamsMu.Unlock()
            
            return engine.Host, engine.Port, engine.ContainerID, nil
        }
    }
    
    return "", 0, "", errors.New("no available engines")
}

func (c *orchClient) EmitStarted(..., engineContainerID string) {
    // ... emit event ...
    
    // Release pending allocation
    c.ReleasePendingStream(engineContainerID)
}

func (c *orchClient) ReleasePendingStream(containerID string) {
    c.pendingStreamsMu.Lock()
    defer c.pendingStreamsMu.Unlock()
    
    if c.pendingStreams[containerID] > 0 {
        c.pendingStreams[containerID]--
        if c.pendingStreams[containerID] == 0 {
            delete(c.pendingStreams, containerID)
        }
    }
}
```

---

## Summary

### Error Recovery Matrix

| Scenario | Should Wait | Can Retry | Typical ETA | Proxy Action |
|----------|-------------|-----------|-------------|--------------|
| VPN Down | Yes | Yes | 60s | Buffer, poll status |
| Circuit Breaker | Yes | No (when open) | 180-300s | Queue, poll status |
| Max Capacity | Yes | No | 120s | Queue, set Retry-After |
| Engine Timeout | No | Yes | N/A | Exponential backoff |
| Race Condition | N/A | N/A | N/A | Pending tracking |

### Key Takeaways

1. **Never fail immediately** - Most errors are temporary
2. **Use recovery ETAs** - Inform retry timing decisions
3. **Poll for status** - Monitor orchestrator health
4. **Queue requests** - Handle capacity gracefully
5. **Track locally** - Prevent race conditions
6. **Communicate clearly** - Return meaningful errors to clients
