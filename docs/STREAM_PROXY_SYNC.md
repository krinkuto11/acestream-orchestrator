# Stream-Proxy Synchronization Architecture

## Problem Statement

Prior to this fix, there was a critical synchronization gap between the `/streams` endpoint and the proxy servers (TS and HLS). When streams ended, they would disappear from the UI immediately, but the proxy sessions would continue serving clients until idle timeout or manual cleanup. This created a confusing user experience where streams appeared to be gone but were still active.

## Root Cause

The issue occurred because:

1. **State Management**: The `state.on_stream_ended()` method immediately removes streams from the in-memory `state.streams` dictionary when they end
2. **API Response**: The `/streams` endpoint queries `state.list_streams_with_stats()` which only returns streams in the `state.streams` dict
3. **Proxy Independence**: The `ProxyServer` and `HLSProxyServer` maintained their own `stream_managers` dictionaries independently
4. **No Coordination**: There was NO explicit call from state to proxy when streams ended

### Synchronization Gap Timeline

```
Time    State                   Proxy               UI Effect
----    -----                   -----               ---------
T0      Stream active           Stream active       Visible
T1      on_stream_ended()       Still active        GONE!
T2      Stream removed          Still active        GONE!
T3      -                       Still active        GONE!
...     (gap continues)         Still active        GONE!
Tn      -                       Idle timeout        GONE
```

## Solution

### 1. Public Cleanup Methods

Added public methods to both proxy servers for synchronous cleanup:

**ProxyServer.stop_stream_by_key(content_id)**
- Stops TS proxy session for a given content ID
- Safe to call even if session doesn't exist
- Logs cleanup action for debugging

**HLSProxyServer.stop_stream_by_key(channel_id)**
- Stops HLS proxy channel for a given channel ID
- Safe to call even if channel doesn't exist
- Logs cleanup action for debugging

### 2. Coordinated Cleanup

Modified `state.on_stream_ended()` to call both proxy cleanup methods:

```python
def on_stream_ended(self, evt: StreamEndedEvent):
    # ... existing stream removal code ...
    
    # CRITICAL: Synchronize proxy cleanup
    if st and st.key:
        try:
            # Clean up TS proxy
            from ..proxy.server import ProxyServer
            proxy_server = ProxyServer.get_instance()
            proxy_server.stop_stream_by_key(st.key)
        except Exception as e:
            logger.warning(f"Failed to synchronize TS proxy cleanup: {e}")
        
        try:
            # Clean up HLS proxy
            from ..proxy.hls_proxy import HLSProxyServer
            hls_proxy = HLSProxyServer.get_instance()
            hls_proxy.stop_stream_by_key(st.key)
        except Exception as e:
            logger.warning(f"Failed to synchronize HLS proxy cleanup: {e}")
```

### 3. Resilient Error Handling

- Proxy cleanup failures do NOT prevent stream ending
- Failures are logged as warnings, not errors
- Proxy servers have their own idle cleanup as fallback

### 4. Debug Endpoint

Added `/debug/sync-check` endpoint to detect desynchronization:

```bash
curl http://localhost:8000/debug/sync-check
```

Returns:
- Streams in state
- Active TS proxy sessions
- Active HLS proxy sessions
- Orphaned sessions (in proxy but not in state)
- Missing sessions (in state but not in proxy)
- `has_issues` flag for quick health check

## Fixed Timeline

```
Time    State                   Proxy               UI Effect
----    -----                   -----               ---------
T0      Stream active           Stream active       Visible
T1      on_stream_ended() -->   stop_stream()       GONE!
T2      Stream removed          Session stopped     GONE!
        ✓ Synchronized
```

## Testing

### Unit Tests

1. **test_proxy_cleanup_integration.py**
   - Verifies proxy cleanup methods are called
   - Tests resilience when cleanup fails
   - Validates cleanup only for valid streams

2. **test_streams_proxy_sync.py**
   - Tests atomic stream removal
   - Tests concurrent access safety
   - Tests stats consistency

3. **test_streams_proxy_integration.py**
   - Tests rapid start/stop cycles
   - Tests mapping consistency

### Manual Testing

1. Start a stream
2. Call `/streams?status=started` - should see the stream
3. Stop the stream
4. Call `/streams?status=started` - should NOT see the stream
5. Call `/debug/sync-check` - should show no discrepancies

## Edge Cases Handled

1. **Proxy cleanup fails**: Stream still removed from state, logged as warning
2. **No active proxy session**: `stop_stream_by_key()` handles gracefully
3. **Concurrent stream end calls**: Locking in state ensures atomic removal
4. **Rapid start/stop cycles**: Each operation fully synchronized

## Performance Impact

- **Minimal**: Cleanup methods are O(1) operations
- **Non-blocking**: Proxy cleanup is synchronous but fast
- **No additional threads**: Uses existing structures

## Backward Compatibility

- Existing proxy idle cleanup still works as fallback
- Existing API endpoints unchanged
- No database schema changes required

## Future Enhancements

1. **Proactive Monitoring**: Add periodic sync-check to detect orphaned sessions
2. **Auto-Recovery**: Automatically clean up orphaned sessions detected by sync-check
3. **Metrics**: Track synchronization issues in Prometheus metrics
4. **UI Dashboard**: Show sync status in admin panel

## Files Changed

- `app/services/state.py`: Added proxy cleanup calls in `on_stream_ended()`
- `app/proxy/server.py`: Added `stop_stream_by_key()` public method
- `app/proxy/hls_proxy.py`: Added `stop_stream_by_key()` public method
- `app/main.py`: Added `/debug/sync-check` endpoint
- `tests/test_proxy_cleanup_integration.py`: New comprehensive tests

## Related Issues

This fix addresses the issue where:
> "In some edge cases, streams disappear from the UI but still remain active"

The synchronization is now robust and handles all edge cases gracefully.
