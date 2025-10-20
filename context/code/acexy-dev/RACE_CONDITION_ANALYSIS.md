# Race Condition Analysis: Engine Stream Assignment

## Problem Statement

Even with `max_streams_per_engine=2` configured, some engines in the orchestrator were getting assigned more than the maximum number of streams. For example, the engine on port 19014 (acestream-15) received at least 6 stream assignment requests when it should have been limited to 2.

## Root Cause

The issue is a **race condition** between concurrent stream requests:

### How the Race Condition Occurs

1. **Request A** calls `SelectBestEngine()` at time T0
2. **Request B** calls `SelectBestEngine()` at time T0 + 0.5ms
3. **Request C** calls `SelectBestEngine()` at time T0 + 1ms
4. All three requests query the orchestrator's `/streams?container_id=X&status=started` endpoint
5. All three see the same engine (e.g., acestream-15) with `active_streams=0`
6. All three select the same engine as the "best" choice
7. Each request then *asynchronously* sends a `stream_started` event to the orchestrator
8. By the time the orchestrator processes the first event, the other two requests have already made their selection

### Why This Happens

The problem is in the timing:

```
Time    Request A              Request B              Request C              Orchestrator State
----    ---------              ---------              ---------              ------------------
T0      Query orchestrator                                                   engine-15: 0 streams
        -> sees 0 streams
T0+0.5                         Query orchestrator                            engine-15: 0 streams
                               -> sees 0 streams
T0+1                                                  Query orchestrator     engine-15: 0 streams
                                                      -> sees 0 streams
T1      Select engine-15
T2      Start stream (async)
T3                             Select engine-15
T4                             Start stream (async)
T5                                                    Select engine-15
T6      EmitStarted sent →                                                   engine-15: 1 stream
T7                             EmitStarted sent →                            engine-15: 2 streams
T8                                                    Start stream (async)
T9                                                    EmitStarted sent →     engine-15: 3 streams!
```

The key insight is that `EmitStarted()` uses `c.post()` which launches a goroutine, making the orchestrator update asynchronous. Between the time an engine is selected and the time the orchestrator knows about it, other concurrent requests can select the same engine.

## Evidence from Logs

From `log_sample.txt`, we can see engine on port 19014 being selected multiple times in rapid succession:

```
Line 227: 2025-10-12T22:39:21.936312517Z
  Selected best available engine
  container_id=15497f49...
  port=19014 active_streams=0

Line 229: 2025-10-12T22:39:21.936836068Z  (0.5ms later!)
  Selected best available engine
  container_id=15497f49...
  port=19014 active_streams=0

Line 236: 2025-10-12T22:39:22.494087259Z  (558ms later)
  Selected best available engine
  container_id=15497f49...
  port=19014 active_streams=0

[... continues with same pattern 3 more times ...]
```

This shows at least **6 concurrent selections** of the same engine, all seeing `active_streams=0`.

## Solution: Pending Stream Tracking

The fix implements **local pending stream tracking** to prevent the race condition:

### Design

1. **Add a pending streams map**: Track engines that have been selected but haven't yet reported to the orchestrator
   ```go
   pendingStreams   map[string]int // containerID -> count of pending allocations
   pendingStreamsMu sync.Mutex     // protects concurrent access
   ```

2. **Account for pending streams in selection**: When checking capacity, consider both active streams (from orchestrator) and pending streams (local)
   ```go
   totalStreams := activeStreams + pendingCount
   if totalStreams < c.maxStreamsPerEngine {
       // Engine has capacity
   }
   ```

3. **Increment counter on selection**: When an engine is selected, immediately increment its pending count
   ```go
   c.pendingStreamsMu.Lock()
   c.pendingStreams[containerID]++
   c.pendingStreamsMu.Unlock()
   ```

4. **Release after reporting**: Once the `stream_started` event is sent to the orchestrator, decrement the pending count
   ```go
   func (c *orchClient) ReleasePendingStream(engineContainerID string) {
       c.pendingStreamsMu.Lock()
       defer c.pendingStreamsMu.Unlock()
       if c.pendingStreams[engineContainerID] > 0 {
           c.pendingStreams[engineContainerID]--
       }
   }
   ```

### How This Solves the Race Condition

With pending stream tracking:

```
Time    Request A              Request B              Request C              Local Pending Count
----    ---------              ---------              ---------              -------------------
T0      Query orchestrator                                                   engine-15: 0 pending
        -> sees 0 active
        -> 0 pending
        -> total = 0
        Select engine-15
        Increment pending →                                                  engine-15: 1 pending
T0+0.5                         Query orchestrator                            engine-15: 1 pending
                               -> sees 0 active
                               -> 1 pending
                               -> total = 1
                               Select engine-15
                               Increment pending →                           engine-15: 2 pending
T0+1                                                  Query orchestrator     engine-15: 2 pending
                                                      -> sees 0 active
                                                      -> 2 pending
                                                      -> total = 2 (AT LIMIT!)
                                                      Select different engine!
```

Now Request C correctly sees that engine-15 is at capacity (even though the orchestrator doesn't know yet) and selects a different engine.

## Implementation Details

### Changes to `SelectBestEngine()`

1. Now returns `(host, port, containerID, error)` to track which engine was selected
2. Calculates total streams including pending: `totalStreams = activeStreams + pendingCount`
3. Increments pending counter atomically when an engine is selected
4. Logs pending count in debug output for troubleshooting

### Changes to `EmitStarted()`

1. Now accepts `engineContainerID` parameter
2. Calls `ReleasePendingStream()` after posting the event to orchestrator
3. This ensures the pending count is decremented once the allocation is reported

### Changes to `proxy.go`

1. Captures the `containerID` returned from `SelectBestEngine()`
2. Passes it through to all `EmitStarted()` calls
3. Ensures proper cleanup even on error paths

## Testing

All existing tests pass with the new implementation. The changes are backward compatible in behavior - they just prevent the race condition by providing better synchronization.

## Prevention of Future Issues

The pending stream tracking mechanism provides:

1. **Immediate consistency**: Local state is updated synchronously before any async operations
2. **Proper cleanup**: Pending counts are always decremented after reporting
3. **Thread safety**: All map access is protected by mutex
4. **Observable behavior**: Debug logs show both active and pending counts

This pattern could be extended to other resources that need load balancing with async reporting.
