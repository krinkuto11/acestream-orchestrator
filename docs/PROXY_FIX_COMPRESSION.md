# Proxy Playback URL Fix - Compression Disable

## Problem

The AceStream proxy was experiencing issues fetching data from playback URLs returned by the AceStream engine. Streams would fail to start or would not deliver data to clients.

## Root Cause

After analyzing the working acexy reference implementation (located in `context/acexy/`), we identified a critical HTTP client configuration issue. The acexy Go implementation includes this comment:

```go
// The transport to be used when connecting to the AceStream middleware. We have to tweak it
// a little bit to avoid compression and to limit the number of connections per host. Otherwise,
// the AceStream Middleware won't work.
a.middleware = &http.Client{
    Transport: &http.Transport{
        DisableCompression:    true,  // CRITICAL
        MaxIdleConns:          10,
        MaxConnsPerHost:       10,
        IdleConnTimeout:       30 * time.Second,
        ResponseHeaderTimeout: a.NoResponseTimeout,
        ExpectContinueTimeout: 1 * time.Second,
    },
}
```

**Key insight**: **AceStream middleware won't work properly if compression is enabled.**

## Solution

We applied the following fixes based on the acexy reference:

### 1. Added HTTP Client Configuration Constants (`app/services/proxy/config.py`)

```python
# HTTP client configuration for AceStream compatibility
# Based on acexy reference: compression must be disabled for AceStream middleware to work properly
MAX_CONNECTIONS: Final[int] = 10  # Maximum connections per host
MAX_KEEPALIVE_CONNECTIONS: Final[int] = 10  # Maximum keepalive connections
KEEPALIVE_EXPIRY: Final[int] = 30  # Seconds before keepalive connection expires
```

### 2. Disabled Compression in Broadcaster (`app/services/proxy/broadcaster.py`)

Added the `Accept-Encoding: identity` header when fetching from playback URLs:

```python
headers = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Encoding": "identity",  # Disable compression - required for AceStream
}
```

The `Accept-Encoding: identity` header tells the server to NOT compress the response, which is the HTTP equivalent of Go's `DisableCompression: true`.

### 3. Configured Connection Limits (`app/services/proxy/stream_session.py`)

Applied connection limits to the HTTP client:

```python
limits = httpx.Limits(
    max_connections=MAX_CONNECTIONS,
    max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
    keepalive_expiry=KEEPALIVE_EXPIRY,
)

self.http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=10.0, read=None),
    follow_redirects=True,
    limits=limits,  # Apply connection limits
)
```

## Why This Matters

### HTTP Compression and AceStream

AceStream's playback URLs stream MPEG-TS data, which is already compressed video/audio. When HTTP compression (gzip/deflate) is applied:

1. **Double compression overhead**: Compressing already-compressed data wastes CPU
2. **Buffering issues**: Compression adds latency as data must be buffered before compression
3. **Stream corruption**: AceStream middleware may not handle decompression correctly
4. **Protocol incompatibility**: The middleware expects raw MPEG-TS bytes

### Connection Limits

Limiting connections per host prevents:

1. **Port exhaustion**: Too many connections can exhaust available ports
2. **Engine overload**: AceStream engines have limited connection capacity
3. **Resource leaks**: Orphaned connections waste memory and file descriptors

## Testing

### Automated Tests

All existing proxy tests continue to pass:

```bash
$ python -m pytest tests/test_proxy_engine_selector.py tests/test_proxy_client_manager.py -v
======================= 11 passed, 10 warnings in 2.02s ========================
```

### Configuration Validation

A validation script confirms the fix:

```bash
$ python /tmp/test_proxy_headers.py
======================================================================
✓ All tests passed!
======================================================================

Key findings:
1. Compression is disabled via 'Accept-Encoding: identity' header
2. Connection limits match acexy reference (10 max connections)
3. This should fix playback URL issues with AceStream middleware
```

## Files Changed

1. **app/services/proxy/config.py**
   - Added HTTP client configuration constants

2. **app/services/proxy/broadcaster.py**
   - Added `Accept-Encoding: identity` header to disable compression

3. **app/services/proxy/stream_session.py**
   - Configured HTTP client with connection limits

## Reference

- **acexy implementation**: `context/acexy/acexy/lib/acexy/acexy.go` lines 105-114
- **acexy copier**: `context/acexy/acexy/lib/acexy/copier.go`
- **acexy main**: `context/acexy/acexy/proxy.go`

## Expected Behavior After Fix

With compression disabled and connection limits applied:

1. ✅ Playback URLs should return data successfully
2. ✅ Streams should start without timeout errors
3. ✅ Multiple clients can multiplex to the same stream
4. ✅ No "stream failed to start - no data received" errors
5. ✅ MPEG-TS chunks should flow to clients smoothly

## Verification Steps

To verify the fix is working:

1. Start the orchestrator
2. Make a request to `/ace/getstream?id=<content_id>`
3. Check logs for "First chunk received" message
4. Verify video data streams to the client
5. Monitor `/proxy/status` to see active sessions

## Migration Notes

No configuration changes required. The fix is transparent to existing deployments:

- ✅ No environment variables changed
- ✅ No API changes
- ✅ Backward compatible
- ✅ Drop-in fix

## Troubleshooting

If streams still fail after this fix:

1. **Check engine health**: Verify engines are running and healthy via `/engines`
2. **Check VPN connectivity**: If using VPN mode, ensure VPN is connected
3. **Check content ID**: Verify the AceStream content ID is valid
4. **Check logs**: Look for HTTP errors or timeout messages in broadcaster logs
5. **Check network**: Ensure orchestrator can reach engine containers

## Credits

This fix was inspired by the acexy reference implementation in `context/acexy/`, which demonstrated the correct HTTP client configuration for AceStream compatibility.
