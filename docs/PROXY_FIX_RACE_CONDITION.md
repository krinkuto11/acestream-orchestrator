# Proxy Stream Initialization Race Condition Fix

## Problem Statement

The AceStream proxy was experiencing timeout errors after 30 seconds when attempting to stream content. The error message was:

```
ERROR: Timeout waiting for stream data for {stream_id}
ERROR: Stream failed to start - no data received
RuntimeError: Stream failed to start - no data received
```

This occurred even when:
- The AceStream engine was healthy and reachable
- The getstream request succeeded
- The playback URL was valid
- No network or connection errors occurred

## Root Cause

A race condition existed in the stream initialization sequence:

### Initialization Flow (Before Fix)

1. **Client requests stream** → `proxy_manager.get_or_create_session(ace_id)`
2. **Session initialization starts** → `StreamSession.initialize()`
3. **StreamManager created** → `StreamManager(stream_id, playback_url, ...)`
4. **StreamManager started** → `await stream_manager.start()`
   - Creates async task: `asyncio.create_task(self._stream_loop())`
   - **Returns immediately** (doesn't wait for connection)
5. **Session marked as active** → `self.is_active = True`
6. **Client tries to stream** → `session.stream_data(client_id)`
7. **Waits for data** → `_has_data()` checks for chunks/buffer

### The Race Condition

The problem occurred between steps 4 and 7:

- `start()` creates an async task but **returns immediately**
- The async task may not execute for 10-100ms (event loop scheduling)
- During that time, the client calls `stream_data()` and starts waiting
- `_has_data()` checks `stream_manager.chunks_received` and `buffer.index`
- **But the StreamManager hasn't even connected yet!**
- Client waits 30 seconds for data that will never arrive
- Timeout error occurs

### Evidence from Logs

```
18:26:53,280 INFO: Stream initialized successfully
18:26:53,282 INFO: Started StreamManager for {stream_id}    ← Task created
18:26:53,312 INFO: Starting stream fetch for {stream_id}    ← Task actually runs (30ms later!)
18:27:23,328 ERROR: Timeout waiting for stream data         ← 30s timeout
```

The 30ms delay between "Started StreamManager" and "Starting stream fetch" shows the race condition window. In production with network latency, this could be much longer.

## Solution

Add connection synchronization using `asyncio.Event` to ensure the StreamManager has established a connection before allowing clients to stream.

### Changes Made

#### 1. StreamManager: Add Connection Event

```python
class StreamManager:
    def __init__(self, ...):
        # ... existing code ...
        
        # Connection event - signals when connection is established or failed
        self.connection_event = asyncio.Event()
```

#### 2. StreamManager: Signal Connection Status

```python
async def start(self):
    # ... existing code ...
    self.connection_event.clear()  # Reset event for new start
    self.stream_task = asyncio.create_task(self._stream_loop())

async def _stream_loop(self):
    try:
        async with self.http_client.stream(...) as response:
            response.raise_for_status()
            
            self.is_connected = True
            self.connection_event.set()  # Signal success
            # ... continue streaming ...
            
    except Exception as e:
        self.error = e
        self.connection_event.set()  # Signal failure (prevent hanging)
```

#### 3. StreamManager: Add Wait Method

```python
async def wait_for_connection(self, timeout: float = 30.0) -> bool:
    """Wait for the stream manager to establish connection.
    
    Returns:
        True if connected successfully, False if timeout or error
    """
    try:
        await asyncio.wait_for(self.connection_event.wait(), timeout=timeout)
        
        # Event was set - check if success or failure
        if self.error:
            return False
        if not self.is_connected:
            return False
            
        return True
        
    except asyncio.TimeoutError:
        return False
```

#### 4. StreamSession: Wait for Connection

```python
async def initialize(self) -> bool:
    # ... create buffer, StreamManager, etc. ...
    
    # Start the stream manager
    await self.stream_manager.start()
    
    # Wait for connection to be established before marking as active
    if not await self.stream_manager.wait_for_connection(timeout=30.0):
        self.error = "Failed to establish connection to AceStream engine"
        await self.stream_manager.stop()
        return False
    
    self.is_active = True
    return True
```

### New Initialization Flow (After Fix)

1. **Client requests stream** → `proxy_manager.get_or_create_session(ace_id)`
2. **Session initialization starts** → `StreamSession.initialize()`
3. **StreamManager created** → `StreamManager(stream_id, playback_url, ...)`
4. **StreamManager started** → `await stream_manager.start()`
   - Creates async task
   - Returns immediately (same as before)
5. **Wait for connection** → `await stream_manager.wait_for_connection()`
   - **Blocks until connection established** ✓
   - Or returns False on timeout/error ✓
6. **Session marked as active** → `self.is_active = True`
7. **Client tries to stream** → `session.stream_data(client_id)`
8. **Streams immediately** → Connection already established ✓

## Benefits

### 1. Eliminates Race Condition
- Client can only stream after connection is confirmed
- No more timeouts waiting for data that hasn't started

### 2. Better Error Handling
- Connection failures detected during initialization
- Clear error message if connection cannot be established
- Failed StreamManager is cleaned up properly

### 3. Predictable Behavior
- Consistent 30-second timeout for connection (not data arrival)
- Timeout occurs at the right place (during initialization)
- Stream only marked as active after connection verified

### 4. No Performance Impact
- `start()` still non-blocking (task creation is fast)
- Only waits when necessary (during initialization)
- Clients can still multiplex (read independently)

## Testing

### Unit Tests

Added comprehensive test coverage in `tests/test_proxy_connection_race.py`:

```python
# Test basic connection waiting
test_stream_manager_waits_for_connection()

# Test timeout handling
test_stream_manager_connection_timeout()

# Test error handling
test_stream_manager_connection_error()

# Test race condition prevention
test_race_condition_prevented()

# Test event signaling on error
test_connection_event_set_on_error()
```

All tests pass:
```
tests/test_proxy_connection_race.py::test_stream_manager_waits_for_connection PASSED
tests/test_proxy_connection_race.py::test_stream_manager_connection_timeout PASSED
tests/test_proxy_connection_race.py::test_stream_manager_connection_error PASSED
tests/test_proxy_connection_race.py::test_race_condition_prevented PASSED
tests/test_proxy_connection_race.py::test_connection_event_set_on_error PASSED
```

### Regression Tests

All existing proxy tests still pass (24 total):
```bash
$ python -m pytest tests/test_proxy_*.py -v
======================= 24 passed =======================
```

### Manual Verification

See `/tmp/verify_proxy_fix.py` for demonstration script that shows:
- `start()` is non-blocking
- `wait_for_connection()` properly waits
- Timeout handling works correctly
- Race condition is prevented

## Deployment

### No Configuration Changes Required

This is a code-only fix with no configuration changes:
- ✅ No environment variables changed
- ✅ No API changes
- ✅ Backward compatible
- ✅ Drop-in fix

### Migration Path

1. Update code to latest version
2. Restart orchestrator service
3. Existing sessions will be recreated on next client connection
4. No manual intervention required

## Expected Behavior After Fix

### Successful Stream

```
INFO: Selecting engine for stream {ace_id}
INFO: Initializing session for {ace_id} on engine {container_id}
INFO: Fetching stream from {getstream_url}
INFO: Stream initialized successfully
INFO: Started StreamManager for {stream_id}
INFO: Starting stream fetch for {stream_id}          ← Task runs
INFO: Stream response received, status: 200          ← Connection established
INFO: StreamManager connected successfully           ← Event signaled
INFO: Session created for {ace_id}                   ← Now marked active
INFO: Client connected to stream                     ← Client can now stream
```

### Connection Failure

```
INFO: Initializing session for {ace_id} on engine {container_id}
INFO: Started StreamManager for {stream_id}
INFO: Starting stream fetch for {stream_id}
ERROR: HTTP error streaming: ConnectError
ERROR: StreamManager connection failed              ← Detected early
ERROR: StreamManager failed to connect              ← Cleanup
ERROR: Failed to initialize session                 ← Return to client
```

### Connection Timeout

```
INFO: Initializing session for {ace_id} on engine {container_id}
INFO: Started StreamManager for {stream_id}
INFO: Starting stream fetch for {stream_id}
ERROR: Timeout waiting for StreamManager connection after 30s
ERROR: StreamManager failed to connect
ERROR: Failed to initialize session
```

## Troubleshooting

### If streams still fail after this fix

#### Issue: Different timeout error

**Possible Cause**: Network is slow but connection eventually succeeds.

**Solution**: Increase timeout in `stream_session.py` line 221:
```python
if not await self.stream_manager.wait_for_connection(timeout=60.0):  # Increase to 60s
```

#### Issue: Stream stops after connecting

**Possible Cause**: Different issue (not connection race condition).

**Check**:
- HTTP timeouts in `stream_manager.py` (should be `timeout=None, pool=None`)
- AceStream engine health
- Network connectivity between orchestrator and engine

#### Issue: Frequent connection timeouts

**Possible Cause**: Engine is slow to respond or overloaded.

**Check**:
- Engine resource usage (CPU, memory)
- Number of concurrent streams per engine
- Network latency to engine

## Related Documentation

- `docs/PROXY_FIX_TIMEOUT.md` - HTTP timeout configuration fix
- `docs/PROXY_FIX_COMPRESSION.md` - Compression disable fix
- `docs/STREAM_MULTIPLEXING_REIMPLEMENTATION.md` - Buffer-based architecture
- `docs/PROXY_IMPLEMENTATION_SUMMARY.md` - Overall proxy architecture

## Technical Details

### Why asyncio.Event?

`asyncio.Event` is the standard Python async synchronization primitive:
- Lightweight (no resource overhead)
- Safe for concurrent access
- Built-in timeout support via `asyncio.wait_for()`
- Clear semantics (set/clear/wait)

### Why Set Event on Error?

Critical for preventing hangs:
```python
try:
    # Connection attempt
    async with self.http_client.stream(...):
        self.is_connected = True
        self.connection_event.set()  # Success
except Exception as e:
    self.error = e
    self.connection_event.set()  # MUST set even on error!
```

If event is not set on error, `wait_for_connection()` would wait the full timeout period even if the connection failed immediately.

### Why Check Error After Event?

The event signals "connection attempt complete" (success or failure):
```python
await self.connection_event.wait()  # Wait for attempt to complete

# Then check the result
if self.error:
    return False  # Failed
if not self.is_connected:
    return False  # Failed
return True  # Success
```

This ensures we distinguish between:
- Timeout (event never set)
- Connection failure (event set, but error occurred)
- Connection success (event set, no error)

## Conclusion

This fix resolves the race condition that caused 30-second timeout errors when the StreamManager's async task was slow to execute. By adding connection synchronization, we ensure clients only stream after the connection is verified, eliminating false timeout errors.

**Status**: ✅ Fixed and tested
**Compatibility**: ✅ Backward compatible
**Migration**: ✅ No changes required
