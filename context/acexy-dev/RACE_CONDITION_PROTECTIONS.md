# Race Condition Protections in Orchestrator Integration

This document describes all race condition protections implemented in the orchestrator integration layer to ensure robust and reliable stream management.

## Overview

The proxy integrates with an orchestrator service to manage multiple AceStream engine instances. Due to concurrent HTTP requests and asynchronous event reporting, several race conditions could occur without proper protections. This document details all implemented safeguards.

## Protection Mechanisms

### 1. Pending Stream Tracking

**Problem**: Multiple concurrent requests could select the same engine before the orchestrator updates its state, leading to over-allocation.

**Solution**: Local pending stream counter
```go
type orchClient struct {
    pendingStreams   map[string]int // containerID -> count
    pendingStreamsMu sync.Mutex
}
```

**How it works**:
1. When `SelectBestEngine()` chooses an engine, it increments the pending counter atomically
2. The counter is included in capacity calculations: `totalStreams = activeStreams + pendingCount`
3. After `EmitStarted()` reports to orchestrator, `ReleasePendingStream()` decrements the counter
4. This ensures only `maxStreamsPerEngine` selections occur per engine, even before orchestrator knows about them

**Code locations**:
- Implementation: `orchestrator_events.go:29-32, 267-283, 629-632`
- Tests: `orchestrator_race_test.go:16-110`

### 2. EmitEnded Idempotency

**Problem**: Multiple code paths (defer statements, error handlers) could call `EmitEnded()` for the same stream, sending duplicate events.

**Solution**: Track ended streams to prevent duplicates
```go
type orchClient struct {
    endedStreams   map[string]bool
    endedStreamsMu sync.Mutex
}
```

**How it works**:
1. Before sending `stream_ended` event, check if streamID is already in `endedStreams` map
2. If found, skip the event (idempotency)
3. If not found, mark as ended and send the event
4. All operations protected by mutex for thread safety

**Code locations**:
- Implementation: `orchestrator_events.go:34-35, 367-388`
- Tests: `orchestrator_improvements_test.go:14-84`

### 3. Engine List Caching

**Problem**: Multiple concurrent `SelectBestEngine()` calls each query orchestrator's `/engines` endpoint, creating unnecessary load and potential rate limiting.

**Solution**: Short-term cache with TTL
```go
type orchClient struct {
    engineCache         []engineState
    engineCacheTime     time.Time
    engineCacheDuration time.Duration // 2 seconds
    engineCacheMu       sync.RWMutex
}
```

**How it works**:
1. `GetEngines()` checks cache first with read lock
2. If cache is fresh (< 2 seconds old), return cached data
3. If cache is stale, fetch fresh data and update cache with write lock
4. Reduces orchestrator queries significantly under concurrent load

**Code locations**:
- Implementation: `orchestrator_events.go:36-40, 379-427`
- Tests: `orchestrator_improvements_test.go:86-170`

### 4. Event Ordering Guarantee

**Problem**: Both `EmitStarted()` and `EmitEnded()` used async posting via goroutines, so orchestrator could receive "ended" before "started".

**Solution**: Synchronous posting for critical events
```go
func (c *orchClient) postSync(path string, body any) {
    // Blocks until HTTP request completes
    resp, err := c.hc.Do(req)
    // ...
}
```

**How it works**:
1. `EmitStarted()` now uses `postSync()` instead of `post()`
2. This blocks until orchestrator acknowledges the `stream_started` event
3. `EmitEnded()` continues to use async `post()` for performance
4. Since `EmitStarted()` completes before function returns, any subsequent `EmitEnded()` will arrive after

**Code locations**:
- Implementation: `orchestrator_events.go:305, 322-356`
- Tests: `orchestrator_improvements_test.go:198-258`

### 5. Cleanup Monitor

**Problem**: Tracking maps could grow unbounded if streams fail before cleanup, or if processes crash.

**Solution**: Background cleanup task
```go
func (c *orchClient) StartCleanupMonitor() {
    ticker := time.NewTicker(5 * time.Minute)
    for {
        select {
        case <-c.ctx.Done():
            return
        case <-ticker.C:
            c.cleanupStaleData()
        }
    }
}
```

**How it works**:
1. Runs every 5 minutes in background goroutine
2. Clears `endedStreams` map if it exceeds 1000 entries
3. Logs warnings if `pendingStreams` has entries (indicates stuck allocations)
4. Prevents unbounded memory growth

**Code locations**:
- Implementation: `orchestrator_events.go:94-121`
- Tests: `orchestrator_improvements_test.go:260-293`

### 6. Empty StreamID Validation

**Problem**: Empty or invalid streamIDs could cause issues in tracking maps and event processing.

**Solution**: Early validation in `EmitEnded()`
```go
func (c *orchClient) EmitEnded(streamID, reason string) {
    if c == nil || streamID == "" {
        return
    }
    // ...
}
```

**How it works**:
1. Check for empty streamID at function entry
2. Return early without adding to tracking maps
3. Prevents map pollution and unnecessary events

**Code locations**:
- Implementation: `orchestrator_events.go:367-370`
- Tests: `orchestrator_improvements_test.go:295-305`

## Race Condition Analysis

### Concurrent SelectBestEngine Calls

**Scenario**: 5 requests arrive within 1ms, all needing an engine.

**Without Protection**:
```
Time    Request A    Request B    Request C    Request D    Request E    Orchestrator
----    ---------    ---------    ---------    ---------    ---------    ------------
T0      GetEngines                                                        engine-1: 0 streams
T1      sees 0       GetEngines                                           engine-1: 0 streams
T2                   sees 0       GetEngines                              engine-1: 0 streams
T3                                sees 0       GetEngines                 engine-1: 0 streams
T4                                             sees 0       GetEngines    engine-1: 0 streams
T5                                                          sees 0
T6      Select 1     Select 1     Select 1     Select 1     Select 1     engine-1: 0 streams
T7      EmitStarted                                                       engine-1: 1 stream
T8                   EmitStarted                                          engine-1: 2 streams
T9                                EmitStarted                             engine-1: 3 streams!
```
Result: Engine gets 5 streams despite max of 2

**With Protection**:
```
Time    Request A    Request B    Request C    Request D    Request E    Pending Count
----    ---------    ---------    ---------    ---------    ---------    -------------
T0      GetEngines                                                        engine-1: 0
T1      sees 0                                                           
        Select 1                                                         
        Increment →                                                       engine-1: 1
T2                   GetEngines                                           
                     sees 0+1=1                                          
                     Select 1                                            
                     Increment →                                          engine-1: 2
T3                                GetEngines                              
                                 sees 0+2=2 (AT LIMIT!)                 
                                 Cannot select!                          
T4                                             GetEngines                 
                                             sees 0+2=2 (AT LIMIT!)      
                                             Cannot select!              
T5                                                          GetEngines    
                                                          sees 0+2=2     
                                                          Cannot select!  
```
Result: Only 2 streams allocated (correct!)

### Multiple EmitEnded Calls

**Scenario**: Stream fails to start, multiple error handlers try to clean up.

**Without Protection**:
```go
// In proxy.go HandleStream
stream, err := p.Acexy.FetchStream(aceId, q)
if err != nil {
    p.Orch.EmitEnded(streamID, "fetch_failed")  // Call 1
}

defer func() {
    p.Orch.EmitEnded(streamID, "handler_exit")   // Call 2
}()

if err := p.Acexy.StartStream(stream, w); err != nil {
    p.Orch.EmitEnded(streamID, "start_failed")   // Call 3
}
```
Result: 3 `stream_ended` events sent to orchestrator

**With Protection**:
```
Call 1: EmitEnded("stream-123", "fetch_failed")
        → Check map: not found
        → Mark as ended
        → Send event ✓

Call 2: EmitEnded("stream-123", "handler_exit")
        → Check map: found!
        → Skip event (logged) ✗

Call 3: EmitEnded("stream-123", "start_failed")
        → Check map: found!
        → Skip event (logged) ✗
```
Result: Only 1 event sent (correct!)

### Concurrent GetEngines Queries

**Scenario**: 20 concurrent SelectBestEngine calls each query `/engines`.

**Without Caching**:
- 20 HTTP requests to orchestrator
- Potential rate limiting or performance degradation
- Unnecessary load on orchestrator

**With Caching**:
- First request: cache miss → HTTP request
- Next 19 requests: cache hit → local data
- Result: 1 HTTP request instead of 20 (95% reduction)

## Thread Safety

All protections use proper synchronization:

1. **Mutex**: Used for simple maps (`pendingStreamsMu`, `endedStreamsMu`)
2. **RWMutex**: Used for cache that's read-heavy (`engineCacheMu`)
3. **Context**: Used for graceful shutdown of background tasks

## Performance Impact

| Protection | Performance Impact | Justification |
|------------|-------------------|---------------|
| Pending Tracking | Minimal (mutex lock) | Prevents over-allocation |
| Idempotency | Minimal (map lookup) | Prevents duplicate events |
| Caching | Positive (reduces HTTP) | Reduces orchestrator load by ~95% |
| Event Ordering | Small (sync HTTP) | Only for EmitStarted, critical for correctness |
| Cleanup | Negligible (5min interval) | Prevents memory leaks |

## Testing

Each protection has dedicated tests:

1. `TestPendingStreamTracking`: Verifies only max streams allocated
2. `TestPendingStreamRelease`: Verifies cleanup after EmitStarted
3. `TestEmitEndedIdempotency`: Verifies duplicate prevention
4. `TestEngineListCaching`: Verifies cache reduces queries
5. `TestEventOrdering`: Verifies started-before-ended
6. `TestCleanupMonitor`: Verifies map cleanup
7. `TestEmitEndedWithEmptyStreamID`: Verifies input validation

All tests run with `-race` flag to detect data races.

## Monitoring

The implementation includes extensive logging:

- Debug logs for cache hits/misses
- Debug logs for idempotency skips
- Info logs for engine selection with full details
- Warn logs for stuck pending streams

Example log output:
```
DEBUG Returning cached engine list count=3 age=1.2s
DEBUG Stream already ended, skipping duplicate EmitEnded stream_id=abc123 reason=handler_exit
INFO Selected best available engine container_id=engine-1 active_streams=1 pending_streams=1 total=2
WARN Pending streams still tracked count=2 engines=map[engine-1:2]
```

## Future Improvements

Potential enhancements:

1. **Configurable cache duration**: Allow tuning based on deployment
2. **Metrics export**: Expose cache hit rate, duplicate events prevented
3. **Circuit breaker**: Fail fast if orchestrator is down (partially implemented via health monitoring)
4. **Request deduplication**: Combine identical concurrent GetEngines calls
5. **Persistent tracking**: Survive process restarts (use external store)

## Summary

The orchestrator integration now has comprehensive race condition protections:

✅ Pending stream tracking prevents over-allocation
✅ Idempotency prevents duplicate events
✅ Caching reduces orchestrator load
✅ Event ordering ensures correctness
✅ Cleanup prevents memory leaks
✅ Validation prevents invalid data
✅ All protections are thread-safe
✅ All protections are tested with race detection

These protections ensure the proxy can handle high concurrency while maintaining data integrity and avoiding race conditions that could lead to incorrect stream allocation or duplicate event reporting.
