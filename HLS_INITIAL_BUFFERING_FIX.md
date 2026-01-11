# HLS Initial Buffering Timeout Fix

## Problem

When playing HLS streams, clients were experiencing playback failures with the following error pattern:

```
orchestrator  | 2026-01-11 11:35:20,860 INFO app.proxy.hls_proxy: New client connected: 100.124.31.118
orchestrator  | 2026-01-11 11:35:46,898 INFO app.proxy.hls_proxy: Initial buffer ready with 1 segments (5.0s of content)
orchestrator  | 2026-01-11 11:35:47,861 WARNING app.proxy.hls_proxy: Client 100.124.31.118 inactive for 27.0s, removing
orchestrator  | 2026-01-11 11:35:47,861 INFO app.proxy.hls_proxy: Channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947: All clients disconnected for 21.0s
orchestrator  | 2026-01-11 11:35:47,861 INFO app.proxy.hls_proxy: Stopping HLS channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947
```

The stream would stop just as the initial buffer became ready, preventing playback.

## Root Cause

The issue was a **race condition between initial buffering and cleanup monitoring**:

1. **Client connects** at `11:35:20.860` → client activity is recorded
2. **Manifest request blocks** waiting for initial buffer to be ready
3. **Cleanup monitoring starts** with a 2-second delay
4. **Initial buffering takes time** (20-30 seconds due to network timeouts/delays)
5. **During buffering**, the client makes no new requests (it's waiting for the manifest response)
6. **Cleanup monitoring checks** every 5 seconds and sees no activity
7. **After 27 seconds**, client is marked inactive (timeout is 3x target_duration = 30s)
8. **Channel is stopped** at `11:35:47.861` - just 1 second after buffer became ready

The problem: **The cleanup monitoring thread didn't know that the first manifest request was still being processed.** It only saw that no new requests had come in for 27 seconds.

## Solution

Skip cleanup monitoring during the initial buffering phase:

```python
def cleanup_loop():
    # Use a small initial delay to allow first client to connect
    time.sleep(2)
    
    # Monitor client activity
    while self.cleanup_running and self.running:
        try:
            # Skip cleanup during initial buffering to avoid premature timeout
            # Initial buffering can take significant time due to network delays
            if self.initial_buffering:
                logger.debug(f"Channel {self.channel_id}: Skipping cleanup during initial buffering")
                time.sleep(5)
                continue
            
            # Calculate timeout based on target duration
            timeout = self.target_duration * 3.0
            
            if self.client_manager and self.client_manager.cleanup_inactive(timeout):
                logger.info(f"Channel {self.channel_id}: All clients disconnected for {timeout:.1f}s")
                proxy_server.stop_channel(self.channel_id)
                break
        except Exception as e:
            logger.error(f"Cleanup error for channel {self.channel_id}: {e}")
        
        time.sleep(5)
```

### How It Works

1. **Cleanup monitoring starts** as usual when a channel is initialized
2. **During initial buffering** (`initial_buffering=True`):
   - Cleanup checks are skipped
   - No clients are marked as inactive
   - Channel stays alive regardless of timeout
3. **Once buffering completes** (`initial_buffering=False`):
   - Normal cleanup monitoring resumes
   - Inactive clients are detected and removed
   - Channel stops when all clients disconnect

The `initial_buffering` flag is set to `False` by the `StreamFetcher` once initial segments are successfully downloaded:

```python
# In StreamFetcher._fetch_initial_segments()
if successful_downloads > 0:
    self.manager.initial_buffering = False
    self.manager.buffer_ready.set()
```

## Files Changed

- `app/proxy/hls_proxy.py` - Added check to skip cleanup during initial buffering
- `tests/test_hls_initial_buffering_cleanup.py` - New tests validating the fix

## Testing

Created comprehensive tests that verify:

1. **Cleanup is skipped during initial buffering**
   - Channel stays alive even with no client activity
   - `stop_channel` is not called prematurely

2. **Cleanup resumes after buffering completes**
   - Once `initial_buffering=False`, normal monitoring starts
   - Inactive clients are correctly detected and removed
   - Channel stops after timeout

All 17 HLS-related tests pass, including:
- 5 existing HLS proxy implementation tests
- 4 existing HLS events tests
- 3 existing HLS non-blocking tests
- 3 existing HLS playback URL update tests
- 2 new initial buffering cleanup tests

## Expected Behavior After Fix

With this fix, the typical HLS stream flow is:

```
11:35:20 - Client connects, manifest request received
11:35:20 - Channel initialized, cleanup monitoring starts
11:35:20 - Initial buffering begins, fetch loop starts
11:35:22 - Cleanup monitoring first check → SKIPPED (initial_buffering=True)
11:35:27 - Cleanup monitoring check → SKIPPED (initial_buffering=True)
11:35:32 - Cleanup monitoring check → SKIPPED (initial_buffering=True)
11:35:37 - Cleanup monitoring check → SKIPPED (initial_buffering=True)
11:35:42 - Cleanup monitoring check → SKIPPED (initial_buffering=True)
11:35:46 - Initial buffer ready (initial_buffering=False)
11:35:46 - Manifest returned to client
11:35:47 - Cleanup monitoring check → ACTIVE (checks for inactive clients)
11:35:48 - Client requests first segment
...stream continues normally...
```

## Benefits

1. **Fixes playback failures** - Streams no longer stop during initial buffering
2. **Minimal change** - Only adds a simple condition check
3. **No side effects** - Cleanup monitoring still works normally after buffering
4. **Well tested** - Comprehensive tests ensure correct behavior
5. **Clean solution** - Uses existing `initial_buffering` flag, no new state needed

## Alternative Solutions Considered

1. **Delay cleanup monitoring start** - Would require hardcoding a delay, not flexible
2. **Record activity periodically during buffering** - More complex, requires timer
3. **Increase timeout** - Would delay cleanup after buffering completes
4. **Current solution** - Simple, clean, uses existing state flag ✓
