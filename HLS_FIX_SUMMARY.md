# HLS Stream Proxy Fix - Summary

## Problem
When `stream_mode='HLS'`, the orchestrator was using the MPEG-TS proxy (`app/proxy/`) to handle HLS streams. This caused immediate EOF errors because:

1. The TS proxy expects continuous MPEG-TS binary packets
2. HLS streams provide M3U8 manifests (text) and separate TS segment files
3. The TS proxy tried to stream the M3U8 manifest as binary data, causing it to fail

**Error log:**
```
orchestrator  | 2026-01-10 12:18:44,637 INFO ace_proxy.http_streamer: HTTP reader connecting to http://gluetun:19000/ace/m/52b680be2991d1d7d558a9cbcae1652855d2998f/605f17de1936cb7f7c9fcb705549d159.m3u8
orchestrator  | 2026-01-10 12:18:46,656 INFO ace_proxy.http_streamer: HTTP reader connected successfully, streaming data...
orchestrator  | 2026-01-10 12:18:46,657 INFO ace_proxy.http_streamer: HTTP stream ended
orchestrator  | 2026-01-10 12:18:46,657 INFO ace_proxy.stream_manager: Stream ended (EOF)
```

The stream ended immediately after connecting because the M3U8 manifest is small and was read completely, but the TS proxy expected a continuous stream.

## Solution

### 1. Created FastAPI-based HLS Proxy (`app/proxy/hls_proxy.py`)
Rewritten from `context/hls_proxy` to be FastAPI-native instead of Django-based:

- **`HLSProxyServer`**: Singleton server managing multiple HLS channels
- **`StreamManager`**: Manages individual channel state and metadata
- **`StreamFetcher`**: Background thread fetching segments from AceStream engine
- **`StreamBuffer`**: Thread-safe buffer for storing segments

Key features:
- Fetches M3U8 manifest from AceStream engine periodically
- Downloads and buffers TS segments
- Generates proxy manifest with rewritten URLs pointing back to orchestrator
- Serves segments from buffer to clients

### 2. Modified `app/main.py:ace_getstream` Endpoint
Added conditional routing based on `stream_mode`:

**HLS Mode:**
```python
if stream_mode == 'HLS':
    # Request manifest from AceStream engine
    # Get playback_url (M3U8 URL)
    # Initialize HLS proxy channel
    hls_proxy.initialize_channel(channel_id=id, playback_url=playback_url)
    # Return rewritten manifest
    return manifest with URLs pointing to /ace/hls/{id}/segment/{seq}.ts
```

**TS Mode (unchanged):**
```python
else:
    # Use existing ts_proxy architecture
    proxy.start_stream(...)
    generator = create_stream_generator(...)
    return StreamingResponse(generator.generate(), media_type="video/mp2t")
```

### 3. Added Segment Endpoint (`/ace/hls/{content_id}/segment/{segment_path}`)
Serves buffered HLS segments:

```python
@app.get("/ace/hls/{content_id}/segment/{segment_path:path}")
async def ace_hls_segment(...):
    hls_proxy = HLSProxyServer.get_instance()
    segment_data = hls_proxy.get_segment(content_id, segment_path)
    return Response(content=segment_data, media_type="video/MP2T")
```

### 4. Added Dependency
Added `m3u8==4.0.0` to `requirements.txt` for parsing HLS manifests.

## How It Works

### HLS Stream Flow:

1. **Client requests stream:** `GET /ace/getstream?id=<content_id>`

2. **Orchestrator requests manifest from AceStream engine:**
   - `GET http://engine:port/ace/manifest.m3u8?id=<content_id>&format=json&pid=<uuid>`
   - Response: `{"response": {"playback_url": "http://engine:port/ace/m/session/hash.m3u8", ...}}`

3. **Initialize HLS proxy channel:**
   - `hls_proxy.initialize_channel(channel_id=content_id, playback_url=playback_url)`
   - Starts background thread to fetch segments

4. **Background fetcher downloads segments:**
   - Fetches M3U8 manifest from `playback_url`
   - Parses segments using `m3u8` library
   - Downloads initial segments for buffering
   - Continues fetching new segments as they become available

5. **Generate proxy manifest:**
   - Rewrite segment URLs to point to orchestrator: `/ace/hls/{content_id}/segment/{seq}.ts`
   - Return manifest to client

6. **Client requests segments:**
   - `GET /ace/hls/{content_id}/segment/123.ts`
   - Orchestrator serves from buffer
   - Client plays video

## Testing

Created `tests/test_hls_proxy_implementation.py`:
- ✓ HLS proxy singleton creation
- ✓ HLS configuration values
- ✓ Stream buffer functionality
- ✓ Stream manager creation
- ✓ HLS routing logic in main.py

All tests pass (5/5).

## Comparison: TS vs HLS Mode

| Aspect | TS Mode | HLS Mode |
|--------|---------|----------|
| **Proxy** | `app/proxy/` (ts_proxy) | `app/proxy/hls_proxy.py` |
| **Stream Type** | Continuous MPEG-TS | Segmented HLS |
| **Buffering** | Redis-based shared buffer | In-memory segment buffer |
| **Multiplexing** | Multiple clients share stream | Each client gets manifest |
| **URL Rewriting** | No | Yes (segment URLs) |
| **Media Type** | `video/mp2t` | `application/vnd.apple.mpegurl` |

## Benefits

1. **Correct HLS Handling:** Uses proper HLS proxy architecture instead of treating HLS as continuous stream
2. **Segment Buffering:** Buffers segments for smooth playback
3. **No Regression:** TS mode unchanged, continues using battle-tested ts_proxy
4. **Clean Architecture:** Separate proxy for each streaming protocol
5. **Based on Existing Code:** Rewritten from `context/hls_proxy` which was already validated

## Files Changed

- `app/proxy/hls_proxy.py` (new) - FastAPI-based HLS proxy
- `app/main.py` - Added HLS routing and segment endpoint
- `requirements.txt` - Added m3u8 dependency
- `tests/test_hls_proxy_implementation.py` (new) - Validation tests
