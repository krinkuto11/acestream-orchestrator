# Solution Summary: Race Condition Fix

## Problem
The log_sample.txt showed that engine on port 19014 was getting assigned more streams than the configured maximum of 2. Analysis revealed it received at least 6 concurrent stream assignment requests, all seeing `active_streams=0`.

## Root Cause
A race condition occurred when multiple concurrent HTTP requests called `SelectBestEngine()` simultaneously:

1. All requests queried the orchestrator and saw the same engine with 0 active streams
2. All selected the same engine as "best"
3. The `stream_started` events were sent asynchronously via goroutines
4. Before the orchestrator could update its state, additional requests had already made their selection

## Solution
Implemented **local pending stream tracking** to provide immediate consistency:

### Key Changes

1. **Added tracking fields to `orchClient`**:
   ```go
   pendingStreams   map[string]int // containerID -> pending count
   pendingStreamsMu sync.Mutex     // thread safety
   ```

2. **Modified SelectBestEngine()**:
   - Now considers both active streams (from orchestrator) and pending streams (local)
   - Increments pending counter atomically when engine is selected
   - Returns containerID for tracking
   ```go
   totalStreams := activeStreams + pendingCount
   if totalStreams < maxStreamsPerEngine {
       // Engine has capacity
   }
   ```

3. **Added ReleasePendingStream()**:
   - Decrements pending count after event is sent to orchestrator
   - Called automatically after `EmitStarted()`
   - Cleans up map entries when count reaches 0

4. **Updated proxy.go**:
   - Passes containerID from SelectBestEngine to EmitStarted
   - Ensures proper cleanup on all code paths

## Testing
All tests pass including new race condition prevention tests:
- `TestPendingStreamTracking`: Verifies only max streams are allocated
- `TestPendingStreamRelease`: Verifies cleanup works correctly

## Results
With this fix:
- Only max_streams_per_engine concurrent selections can occur for any engine
- Race condition is eliminated through local synchronization
- Orchestrator state updates remain asynchronous (no performance impact)
- Thread-safe implementation with proper mutex protection

## Files Modified
- `acexy/orchestrator_events.go` - Added tracking, modified SelectBestEngine
- `acexy/proxy.go` - Updated to pass containerID through
- `acexy/orchestrator_health_test.go` - Updated for new signature
- `acexy/orchestrator_race_test.go` - New tests for race condition prevention
- `RACE_CONDITION_ANALYSIS.md` - Detailed technical analysis
