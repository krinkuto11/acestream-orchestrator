# Acexy-Inspired Proxy Implementation

## Overview

This document describes the new acexy-inspired proxy implementation that replaces the previous buffer-based approach with a direct streaming multiwriter pattern.

## Problem Statement

The previous proxy implementation had issues with fetching data from AceStream playback URLs due to:
1. Improper HTTP client configuration
2. Buffering that introduced latency
3. Complexity in managing client synchronization

## Solution: Acexy Pattern

The acexy project (context/acexy) demonstrates the correct approach for streaming from AceStream engines:

### Key Principles from Acexy

1. **Direct Streaming**: Stream directly from `playback_url` without intermediate buffering
2. **Parallel Multiwriter**: Write each chunk to ALL connected clients simultaneously
3. **Critical HTTP Configuration**:
   - `Accept-Encoding: identity` header (disables compression)
   - HTTP client with compression disabled
   - Connection pooling limits (max 10 connections/host)
4. **Dynamic Client Management**: Add/remove clients during streaming
5. **Real-time Delivery**: No historical buffer - clients get data as it arrives

## Implementation

### Core Components

#### 1. AcexyStreamManager (`acexy_stream_manager.py`)

The main stream manager that handles:
- Opening HTTP connection to playback_url
- Reading chunks from the stream response  
- Multicasting each chunk to all connected clients in parallel
- Dynamic client subscription (add/remove)
- Health monitoring and retry logic

**Key Features:**
- **Parallel Multiwriter Pattern**: Uses asyncio queues for each client
  ```python
  async def _multicast_chunk(chunk):
      # Write to all clients concurrently
      tasks = [write_to_client(writer, chunk) for writer in clients]
      await asyncio.gather(*tasks, return_exceptions=True)
  ```

- **Retry Logic** (from dispatcharr):
  - Exponential backoff: 2^retry up to 10s max
  - Max 3 retries before giving up
  - Reset retry count on successful reconnection

- **Health Monitoring**:
  - Independent monitoring task running every 5s
  - Detect no data for >30s
  - Auto-recovery detection

#### 2. AcexyStreamGenerator (`acexy_stream_generator.py`)

Reads from a client-specific queue and yields to HTTP response:
- Simple async iteration over queue
- Handles sentinel values (None = stream end)
- Per-client statistics tracking
- Timeout handling

#### 3. StreamSession Integration

Updated to use AcexyStreamManager instead of buffer-based approach:
```python
# Old approach (buffer-based)
buffer = StreamBuffer(...)
stream_manager = StreamManager(..., buffer=buffer)
generator = StreamGenerator(..., buffer=buffer)

# New approach (acexy multiwriter)
stream_manager = AcexyStreamManager(...)
queue = await stream_manager.add_client(client_id)
generator = AcexyStreamGenerator(..., queue=queue)
```

### HTTP Client Configuration

Critical for AceStream compatibility (based on acexy reference):

```python
# HTTP client limits (from acexy)
limits = httpx.Limits(
    max_connections=10,
    max_keepalive_connections=10,
    keepalive_expiry=30,
)

# Headers for streaming (from acexy)
headers = {
    "User-Agent": "VLC/3.0.21 LibVLC/3.0.21",
    "Accept": "*/*",
    "Accept-Encoding": "identity",  # CRITICAL: Disables compression
    "Connection": "keep-alive",
}
```

### Data Flow

```
AceStream Engine (playback_url)
    ↓
HTTP Stream Response
    ↓
AcexyStreamManager._stream_loop()
    ↓
Read chunks with aiter_bytes()
    ↓
_multicast_chunk() [parallel writes]
    ↓         ↓         ↓
Queue 1   Queue 2   Queue 3   ... (one per client)
    ↓         ↓         ↓
Generator Generator Generator
    ↓         ↓         ↓
Client 1  Client 2  Client 3
```

## Differences from Original Acexy

Since acexy is written in Go and this is Python/asyncio, some adaptations were made:

| Acexy (Go) | Our Implementation (Python) |
|------------|----------------------------|
| io.Writer interface | asyncio.Queue |
| goroutines for parallel writes | asyncio.gather() with tasks |
| PMultiWriter.Write() | _multicast_chunk() |
| io.Copy() | async for chunk in stream |
| sync.RWMutex | asyncio.Lock |

## Robustness Features (from Dispatcharr)

While acexy provides the core streaming pattern, we added robustness features from dispatcharr:

1. **Retry Logic**: Automatic reconnection with exponential backoff
2. **Health Monitoring**: Independent task checking stream health
3. **Connection Stability Tracking**: Reset retries on stable connections
4. **Graceful Error Handling**: Don't hang clients on errors

## Testing

### Manual Testing

Use the existing manual test:

```bash
python tests/manual/test_proxy_playback.py \
    --engine-host localhost \
    --engine-port 6878 \
    --content-id <acestream_id>
```

This verifies:
- ✓ HTTP client configuration
- ✓ Compression disabled
- ✓ Playback URL streaming
- ✓ Data reception
- ✓ Clean shutdown

### Integration Testing

Test scenarios:
1. **Single client**: Verify stream works with one client
2. **Multiple clients**: Test parallel multiwriter with 3+ concurrent clients
3. **Client disconnect/reconnect**: Verify graceful handling
4. **Network interruption**: Test retry logic
5. **Slow client**: Verify queue backpressure protects other clients

## Configuration

All configuration is in `app/services/proxy/config.py`:

```python
# Critical for AceStream compatibility
USER_AGENT = "VLC/3.0.21 LibVLC/3.0.21"
MAX_CONNECTIONS = 10
MAX_KEEPALIVE_CONNECTIONS = 10
KEEPALIVE_EXPIRY = 30

# Chunk sizes
COPY_CHUNK_SIZE = 64 * 1024  # 64KB chunks

# Timeouts
EMPTY_STREAM_TIMEOUT = 30  # Seconds before stream considered empty
```

## Migration Guide

### For Developers

The API remains mostly the same, but internal implementation changed:

**Before:**
- StreamSession created StreamBuffer and StreamManager
- StreamManager wrote to buffer
- StreamGenerator read from buffer at different positions

**After:**
- StreamSession creates AcexyStreamManager
- AcexyStreamManager multicasts to client queues
- AcexyStreamGenerator reads from its queue

### For Users

No changes required - the proxy API endpoints remain the same.

## Performance Characteristics

### Acexy Pattern Benefits

1. **Lower Latency**: No intermediate buffering, clients get data immediately
2. **Simpler Code**: Direct pipe from source to clients
3. **Better Reliability**: Proven pattern from acexy project
4. **Resource Efficient**: No Redis storage overhead for real-time streams

### Trade-offs

1. **No Historical Buffer**: Clients can't join mid-stream and seek back
   - This is acceptable for live streaming use case
2. **Queue Memory**: Each client has a queue (max 100 chunks)
   - With 64KB chunks: ~6.4MB per client max
3. **Slow Client Protection**: Queue fills → chunks dropped for that client
   - This is intentional to prevent slow clients affecting others

## Troubleshooting

### Common Issues

**Problem**: "No data received from playback_url"
- **Check**: HTTP headers include `Accept-Encoding: identity`
- **Check**: HTTP client has compression disabled
- **Check**: Engine is accessible and content ID is valid

**Problem**: "Clients receiving corrupted data"
- **Check**: Chunk sizes are appropriate (64KB default)
- **Check**: No intermediate proxy recompressing data

**Problem**: "Stream disconnects frequently"
- **Check**: Retry logic is working (check logs for retry attempts)
- **Check**: Health monitoring is running
- **Check**: Network stability between orchestrator and engine

## References

- **Acexy Project**: `/context/acexy/` - Original Go implementation
- **Dispatcharr Proxy**: `/context/dispatcharr_proxy/` - Robustness patterns
- **Implementation Files**:
  - `app/services/proxy/acexy_stream_manager.py`
  - `app/services/proxy/acexy_stream_generator.py`
  - `app/services/proxy/stream_session.py`

## Future Enhancements

Potential improvements:
1. **Optional FFmpeg Mode**: Keep FFmpeg passthrough option for metadata extraction
2. **Stream Switching**: Support multiple URLs per content (failover)
3. **Advanced Health Checks**: Deeper analysis of stream quality
4. **Metrics Collection**: Prometheus metrics for monitoring
5. **Adaptive Buffering**: Dynamic queue sizes based on client behavior
