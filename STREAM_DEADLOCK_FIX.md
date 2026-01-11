# Stream Deadlock Fix - Technical Summary

## Problem Description

Streams were not playing due to a deadlock in the orchestrator. The logs showed repeated timeout errors:

```
WARNING app.proxy.hls_proxy: Failed to send HLS stream started event to orchestrator: 
HTTPConnectionPool(host='localhost', port=8000): Read timed out. (read timeout=5)

ERROR app.proxy.hls_proxy: Fetch loop error for channel ...: 
HTTPConnectionPool(host='gluetun', port=19000): Read timed out. (read timeout=10)

ERROR app.main: Timeout getting HLS manifest: Timeout waiting for initial buffer
```

## Root Cause

The orchestrator runs uvicorn in **single-worker mode** (only one request can be handled at a time). This created a deadlock scenario:

1. Client requests `/hls/manifest.m3u8`
2. FastAPI/uvicorn starts processing this request
3. The HLS proxy initializes and spawns a background thread
4. The background thread tries to POST to `http://localhost:8000/events/stream_started`
5. **Deadlock**: uvicorn is busy handling the original request and cannot accept the new POST request
6. After 5 seconds, the POST request times out
7. Stream initialization fails

## Solution

Replaced HTTP-based event communication with direct function calls:

### Before (HTTP-based, causes deadlock)
```python
# In background thread
response = requests.post(
    f"http://localhost:8000/events/stream_started",
    json=event_data,
    timeout=5  # ← Times out because server is busy
)
```

### After (Direct function call, no deadlock)
```python
# In background thread
from ..services.internal_events import handle_stream_started

event = StreamStartedEvent(...)
result = handle_stream_started(event)  # ← Direct call, no HTTP
```

## Implementation Details

### New Module: `app/services/internal_events.py`

Created two functions that encapsulate the event handling logic:

- `handle_stream_started(evt: StreamStartedEvent) -> StreamState`
- `handle_stream_ended(evt: StreamEndedEvent) -> Optional[StreamState]`

These functions:
- Call `state.on_stream_started()` and `state.on_stream_ended()` directly
- Call `event_logger.log_event()` for audit trail
- Do NOT require API key authentication (internal use only)
- Are NOT exposed via HTTP endpoints

### Modified Files

1. **`app/proxy/hls_proxy.py`**
   - Updated `_send_stream_started_event()` to use internal handler
   - Updated `_send_stream_ended_event()` to use internal handler
   - Removed HTTP POST requests to localhost

2. **`app/proxy/stream_manager.py`** (TS proxy)
   - Updated `_send_stream_started_event()` to use internal handler
   - Updated `_send_stream_ended_event()` to use internal handler
   - Removed HTTP POST requests to localhost

## Security Considerations

**Q: Why don't the internal handlers require API key authentication?**

A: By design. The internal handlers are:
- Only accessible within the orchestrator process
- Only called by trusted proxy threads (part of the same application)
- NOT exposed via HTTP endpoints

External access to stream events still requires API key authentication via the HTTP endpoints in `main.py`:
- `POST /events/stream_started` (requires API key)
- `POST /events/stream_ended` (requires API key)

This follows the principle of "trust internal components, authenticate external access."

## Testing

Created `tests/test_internal_events_fix.py` with 6 comprehensive tests:

1. ✅ Internal event handlers can be imported
2. ✅ Schema classes are correct (StreamKey, EngineAddress, SessionInfo)
3. ✅ HLS proxy uses internal handlers (no HTTP)
4. ✅ TS proxy uses internal handlers (no HTTP)
5. ✅ No HTTP dependencies in event handlers
6. ✅ Events are properly structured

All tests pass. CodeQL security scan: 0 alerts.

## Expected Behavior After Fix

### Before
```
orchestrator  | WARNING app.proxy.hls_proxy: Failed to send HLS stream started event to orchestrator: 
               HTTPConnectionPool(host='localhost', port=8000): Read timed out. (read timeout=5)
orchestrator  | ERROR app.main: Timeout getting HLS manifest: Timeout waiting for initial buffer
```

### After
```
orchestrator  | INFO app.proxy.hls_proxy: Sent HLS stream started event to orchestrator: stream_id=abc123...
orchestrator  | INFO app.proxy.hls_proxy: Initial buffer ready with X segments (Y.Zs of content)
orchestrator  | INFO app.main: HLS stream started successfully
```

## Why This Works

The fix eliminates the deadlock by removing the HTTP roundtrip:

| Aspect | Before (HTTP) | After (Direct Call) |
|--------|---------------|---------------------|
| Communication | HTTP POST to localhost:8000 | Direct function call |
| Timeout risk | 5 seconds, can fail | No timeout possible |
| Worker blocking | Yes (single worker) | No |
| Dependencies | requests library | None (internal) |
| Latency | Network + HTTP overhead | Function call only |

## Future Considerations

If you need to scale to multiple uvicorn workers in the future:
- The internal event handlers will continue to work (each worker has its own state)
- Consider implementing a shared state backend (Redis, database) for cross-worker communication
- The HTTP endpoints can remain as-is for external integrations

## Related Files

- `app/services/internal_events.py` - New internal event handlers
- `app/proxy/hls_proxy.py` - HLS proxy (modified)
- `app/proxy/stream_manager.py` - TS proxy (modified)
- `app/main.py` - HTTP endpoints (unchanged, still require API key)
- `app/services/state.py` - State management (unchanged)
- `tests/test_internal_events_fix.py` - Validation tests

## Commits

1. `b54563d` - Fix stream deadlock by using internal event handlers
2. `cf24511` - Fix schema class names in internal event handlers
3. `54d3ed4` - Add security note clarifying authentication design
4. `52355c5` - Add validation tests for internal event handlers fix
