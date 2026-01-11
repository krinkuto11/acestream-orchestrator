# HLS Multiple Client Fix

## Problem

When multiple clients attempted to connect to the same HLS stream, each connection triggered a new AceStream session request. This caused:
1. Unnecessary session creation overhead
2. Old sessions being invalidated
3. 403 Forbidden errors when the proxy tried to fetch from expired session URLs

### Root Cause

Every call to `/ace/getstream?id=<infohash>` made a new request to the AceStream engine, even when a channel already existed:

1. **First client connects** → Calls `/ace/getstream?id=<infohash>`
   - Orchestrator requests session from engine
   - AceStream engine creates session A with playback URL A
   - HLS proxy creates a channel and starts fetching from URL A

2. **Second client connects** → Calls `/ace/getstream?id=<infohash>` **again**
   - Orchestrator requests a **new session** from engine (unnecessary!)
   - AceStream engine creates session B with playback URL B
   - Session A may expire/be invalidated
   - If proxy tries to fetch from URL A → `403 Forbidden`

### Error Logs

```
orchestrator  | 2026-01-11 10:25:00,707 ERROR app.proxy.hls_proxy: Fetch loop error for channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947: HTTPConnectionPool(host='gluetun', port=19000): Read timed out. (read timeout=10)
orchestrator  | 2026-01-11 10:25:27,725 ERROR app.proxy.hls_proxy: Fetch loop error for channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947: 403 Client Error: Forbidden for url: http://gluetun:19000/ace/m/6fb71f9c463dff28452da9a529ba537d1452a32f/f8645f4a0835d9aa0f6a66bd98f7ff67.m3u8
```

## Solution

**Prevent duplicate engine requests by checking if a channel exists before making the request.**

The key insight: Clients already access the proxy's m3u8 (not the engine's), so we don't need to create a new engine session for every client connection.

### Implementation

#### 1. Added `has_channel()` Method

```python
class HLSProxyServer:
    def has_channel(self, channel_id: str) -> bool:
        """Check if a channel already exists."""
        with self.lock:
            return channel_id in self.stream_managers
```

#### 2. Check Before Requesting From Engine

Modified `/ace/getstream` endpoint in `main.py`:

```python
# Get HLS proxy instance
hls_proxy = HLSProxyServer.get_instance()

# Check if channel already exists
if not hls_proxy.has_channel(id):
    # Channel doesn't exist - request from AceStream engine
    response = requests.get(hls_url, params=params, timeout=10)
    # ... process response and initialize channel
else:
    # Channel already exists - reuse existing session
    logger.info(f"HLS channel {id} already exists, reusing existing session")

# Get and return the manifest (for both new and existing channels)
manifest_content = hls_proxy.get_manifest(id)
return StreamingResponse(...)
```

#### 3. Simplified Channel Initialization

```python
def initialize_channel(self, channel_id: str, ...):
    """Initialize a new HLS channel.
    
    This method should only be called for new channels.
    """
    with self.lock:
        # Safety check
        if channel_id in self.stream_managers:
            logger.warning(f"Channel already exists, skipping")
            return
        
        # Create new channel...
```

### Flow Comparison

**Before (wasteful):**
```
Client 1 → /ace/getstream → Engine request → Session A → Proxy channel created
Client 2 → /ace/getstream → Engine request → Session B → URL update attempt
                                            ↑
                                     Creates unnecessary session!
```

**After (efficient):**
```
Client 1 → /ace/getstream → Engine request → Session A → Proxy channel created
Client 2 → /ace/getstream → has_channel() = true → Return proxy manifest
                            ↑
                      No engine request!
```

## Benefits

1. **Single session per stream**: Only one AceStream session is created per unique content ID
2. **No session invalidation**: Old sessions don't expire because we don't create new ones
3. **Better performance**: Eliminates unnecessary engine API calls
4. **Simpler code**: Removed `update_playback_url()` method and associated locking
5. **Cleaner architecture**: Proxy is truly the single point of access to the engine

## Code Quality

- ✅ All tests pass (15 tests across 4 test files)
- ✅ Thread-safety maintained
- ✅ Simpler implementation (less code, fewer race conditions)
- ✅ Well-documented with comments

## Files Changed

1. **`app/main.py`**:
   - Added channel existence check before engine request
   - Restructured HLS handling to avoid duplicate sessions

2. **`app/proxy/hls_proxy.py`**:
   - Added `has_channel()` method
   - Simplified `initialize_channel()` 
   - Removed `update_playback_url()` method (no longer needed)
   - Removed `_playback_url_lock` (no longer needed)

3. **`tests/test_hls_playback_url_update.py`**:
   - Updated tests to verify channel reuse behavior
   - Tests `has_channel()` functionality
   - Verifies channels are not duplicated or modified

## Testing

All HLS tests pass:
- `test_hls_events.py` - 4 tests ✅
- `test_hls_non_blocking.py` - 3 tests ✅  
- `test_hls_playback_url_update.py` - 3 tests ✅ (renamed, updated)
- `test_hls_proxy_implementation.py` - 5 tests ✅

### Test Coverage

1. **Channel existence checking**: Verifies `has_channel()` works correctly
2. **Channel reuse**: Verifies existing channels are not modified
3. **No duplicates**: Verifies only one channel exists per content ID
4. **Thread safety**: Verifies concurrent channel checks work correctly

