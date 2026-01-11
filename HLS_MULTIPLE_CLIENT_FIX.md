# HLS Multiple Client Playback Fix

## Problem

When multiple clients attempted to connect to the same HLS stream, playback would fail with 403 Forbidden errors after the second client connected.

### Root Cause

The issue occurred due to how AceStream engine handles HLS sessions:

1. **First client connects** → Calls `/ace/getstream?id=<infohash>`
   - AceStream engine creates session A with playback URL A: `http://gluetun:19000/ace/m/hash1/session1.m3u8`
   - HLS proxy creates a channel and starts fetching segments from URL A

2. **Second client connects** → Calls `/ace/getstream?id=<infohash>` again
   - AceStream engine creates a **new session B** with playback URL B: `http://gluetun:19000/ace/m/hash2/session2.m3u8`
   - HLS proxy detected the channel already exists and returned early
   - Fetch loop continued using the **old playback URL A**

3. **Session A expires** → AceStream engine invalidates the old session
   - Fetch loop tries to get manifest from URL A
   - Returns `403 Forbidden` because session A is expired

### Error Logs

```
orchestrator  | 2026-01-11 10:25:00,707 ERROR app.proxy.hls_proxy: Fetch loop error for channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947: HTTPConnectionPool(host='gluetun', port=19000): Read timed out. (read timeout=10)
orchestrator  | 2026-01-11 10:25:27,725 ERROR app.proxy.hls_proxy: Fetch loop error for channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947: 403 Client Error: Forbidden for url: http://gluetun:19000/ace/m/6fb71f9c463dff28452da9a529ba537d1452a32f/f8645f4a0835d9aa0f6a66bd98f7ff67.m3u8
```

## Solution

Updated the HLS proxy to **refresh the playback URL** when new clients connect to an existing channel.

### Implementation Details

#### 1. Thread-Safe URL Management

Added a lock to `StreamManager` for thread-safe playback URL updates:

```python
class StreamManager:
    def __init__(self, ...):
        self._playback_url_lock = threading.Lock()  # New lock for URL updates
        # ... rest of initialization
```

#### 2. URL Update Method

Implemented `update_playback_url()` to atomically update the playback URL and session info:

```python
def update_playback_url(self, new_playback_url: str, session_info: Dict[str, Any]):
    """Update the playback URL and session info for this stream."""
    # Update all session-related data atomically under the lock to ensure
    # consistency between playback_url and session info.
    with self._playback_url_lock:
        old_url = self.playback_url
        self.playback_url = new_playback_url
        
        # Update session info
        self.playback_session_id = session_info.get('playback_session_id')
        self.stat_url = session_info.get('stat_url')
        self.command_url = session_info.get('command_url')
        self.is_live = session_info.get('is_live', 1)
        
        logger.info(f"Updated playback URL for channel {self.channel_id}")
```

#### 3. Modified Channel Initialization

Updated `HLSProxyServer.initialize_channel()` to detect existing channels and update their URL:

```python
def initialize_channel(self, channel_id: str, playback_url: str, ...):
    """Initialize a new HLS channel or update existing channel's playback URL."""
    with self.lock:
        # If channel already exists, update the playback URL for the new session
        if channel_id in self.stream_managers:
            logger.info(f"HLS channel {channel_id} already exists, updating playback URL")
            manager = self.stream_managers[channel_id]
            manager.update_playback_url(playback_url, session_info)
            return
        
        # ... create new channel if doesn't exist
```

#### 4. Thread-Safe URL Access in Fetcher

Modified `StreamFetcher.fetch_loop()` to safely read the current URL:

```python
def fetch_loop(self):
    while self.manager.running:
        try:
            # Get current playback URL (thread-safe)
            with self.manager._playback_url_lock:
                current_playback_url = self.manager.playback_url
            
            # Fetch manifest using current URL
            response = self.session.get(current_playback_url, timeout=10)
            # ... rest of fetch logic
```

## Testing

Created comprehensive test suite in `tests/test_hls_playback_url_update.py`:

### Test 1: Direct Method Testing
```python
def test_stream_manager_update_method():
    """Test StreamManager.update_playback_url() method directly"""
    # Creates manager with initial URL
    # Updates to new URL with new session info
    # Verifies all session data is updated
```

### Test 2: Thread Safety
```python
def test_thread_safe_url_update():
    """Test that playback URL updates are thread-safe"""
    # Simulates concurrent URL reads (50 reads x 2 threads)
    # Simulates concurrent URL updates (10 updates)
    # Verifies no threading errors occur
    # Verifies final URL is correct
```

### Test 3: Channel Update Flow
```python
def test_playback_url_update():
    """Test that playback URL can be updated for existing channels"""
    # Creates initial channel with URL A
    # Simulates second client connecting (URL B)
    # Verifies playback URL is updated to URL B
    # Verifies no duplicate channels are created
```

### Test Results

```
============================================================
Results: 3 passed, 0 failed
============================================================
```

All existing HLS proxy tests continue to pass.

## Benefits

1. **Multi-client support**: Multiple clients can now connect to the same HLS stream without errors
2. **Seamless URL refresh**: The playback URL is updated automatically when new clients connect
3. **Thread-safe**: All updates are protected by locks to prevent race conditions
4. **No duplicate channels**: Reuses existing channels instead of creating duplicates
5. **Atomic updates**: Playback URL and session info are updated together for consistency

## Code Quality

- ✅ Type annotations fixed (`any` → `Any`)
- ✅ Code review passed
- ✅ Security scan passed (CodeQL: 0 vulnerabilities)
- ✅ All tests pass
- ✅ Well-documented with comments

## Files Changed

1. `app/proxy/hls_proxy.py`:
   - Added `_playback_url_lock` to `StreamManager`
   - Implemented `update_playback_url()` method
   - Updated `initialize_channel()` to handle existing channels
   - Modified `fetch_loop()` for thread-safe URL access

2. `tests/test_hls_playback_url_update.py`:
   - New comprehensive test suite for URL update functionality
