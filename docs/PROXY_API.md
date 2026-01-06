# Proxy API Documentation

## Overview

The AceStream Proxy provides a unified HTTP endpoint for video streaming clients. It handles engine selection, multiplexing, and lifecycle management automatically.

## Key Features

- **Single Endpoint**: `/ace/getstream?id=<content_id>` for all streams
- **Intelligent Engine Selection**: Prioritizes forwarded engines, balances load
- **Client Multiplexing**: Multiple clients can share the same stream without duplicate engine requests
- **Automatic Lifecycle**: Streams are automatically started and cleaned up
- **Production Ready**: Based on best practices from dispatcharr_proxy

## Endpoints

### GET /ace/getstream

**Description**: Stream AceStream content via MPEG-TS

**Query Parameters**:
- `id` (required): AceStream content ID (infohash or content_id)

**Response**: 
- Content-Type: `video/mp2t`
- Body: Streaming MPEG-TS video data

**Example**:
```bash
# Stream a video
curl "http://localhost:8000/ace/getstream?id=<your_infohash>"

# Use with video players
vlc "http://localhost:8000/ace/getstream?id=<your_infohash>"
mpv "http://localhost:8000/ace/getstream?id=<your_infohash>"
```

**Behavior**:
1. Checks if stream is already active
   - If yes, adds client to existing stream (multiplexing)
   - If no, selects best engine and starts new stream
2. Streams data to client
3. Automatically removes client when connection closes
4. Cleans up stream after 5 minutes of no clients (configurable)

**Error Responses**:
- `503 Service Unavailable`: No engines available or stream initialization failed
- `500 Internal Server Error`: Unexpected error

### GET /ace/manifest.m3u8

**Description**: HLS manifest endpoint (not yet implemented)

**Query Parameters**:
- `id` (required): AceStream content ID

**Response**: 
- `501 Not Implemented`: HLS support is planned for future release

### GET /proxy/status

**Description**: Get proxy status and statistics

**Response**:
```json
{
  "running": true,
  "total_sessions": 5,
  "active_sessions": 4,
  "total_clients": 12,
  "sessions": [
    {
      "stream_id": "abc123...",
      "ace_id": "abc123...",
      "engine_host": "127.0.0.1",
      "engine_port": 19001,
      "container_id": "engine_container_id",
      "playback_session_id": "uuid",
      "is_live": false,
      "is_active": true,
      "client_count": 3,
      "created_at": "2026-01-06T19:30:00Z",
      "started_at": "2026-01-06T19:30:01Z",
      "ended_at": null,
      "error": null,
      "idle_seconds": 5.2
    }
  ]
}
```

### GET /proxy/sessions

**Description**: Get list of active proxy sessions

**Response**:
```json
{
  "sessions": [
    {
      "stream_id": "abc123...",
      "client_count": 3,
      "is_active": true,
      ...
    }
  ]
}
```

### GET /proxy/sessions/{ace_id}

**Description**: Get detailed info for a specific session

**Path Parameters**:
- `ace_id`: AceStream content ID

**Response**:
```json
{
  "stream_id": "abc123...",
  "ace_id": "abc123...",
  "engine_host": "127.0.0.1",
  "engine_port": 19001,
  "container_id": "engine_container_id",
  "client_count": 3,
  "is_active": true,
  ...
}
```

**Error Responses**:
- `404 Not Found`: Session not found

## Engine Selection Algorithm

The proxy uses an intelligent engine selection algorithm:

1. **Filter**: Only healthy engines
2. **Score**: Calculate score for each engine
   - Forwarded engines: +1000 points
   - Active streams: -10 points per stream
   - Unhealthy engines: -1000 points
3. **Select**: Choose engine with highest score
4. **Cache**: Cache engine list for 2 seconds to reduce load

**Example Scenarios**:
- 2 forwarded engines (0 streams each) + 1 regular engine (0 streams)
  → Selects one of the forwarded engines
- 1 forwarded engine (5 streams) + 1 forwarded engine (2 streams)
  → Selects the one with 2 streams (load balancing)
- 1 forwarded engine (unhealthy) + 1 regular engine (healthy)
  → Selects regular engine (health takes priority)

## Client Multiplexing

Multiple clients can connect to the same stream without creating duplicate engine requests:

1. Client A requests stream `abc123`
   - Proxy creates new session
   - Fetches stream from engine
   - Streams to Client A
   
2. Client B requests same stream `abc123`
   - Proxy finds existing session
   - Adds Client B to session
   - Streams same data to both clients

3. Client A disconnects
   - Proxy removes Client A
   - Stream continues for Client B

4. Client B disconnects
   - Proxy removes Client B
   - Session idle timer starts (5 minutes)
   - After 5 minutes, stream is stopped and cleaned up

## Stream Lifecycle

```
[Client Request] → [Session Created] → [Engine Selected] → [Stream Initialized]
                                                                    ↓
[Stream Active] ← [Client Added] ← [Playback URL Fetched] ← [Engine Contacted]
       ↓
[Streaming Data to Clients]
       ↓
[Client Disconnects] → [Client Removed] → [Check Client Count]
                                                    ↓
                                           [0 clients] → [Idle Timer: 5 min]
                                                    ↓
                                           [Timeout] → [Stop Stream] → [Cleanup]
```

## Configuration

Proxy configuration is in `app/services/proxy/config.py`:

- `EMPTY_STREAM_TIMEOUT`: 30 seconds - Max time to wait for initial data
- `STREAM_IDLE_TIMEOUT`: 300 seconds (5 min) - Time before cleaning up idle streams
- `CLIENT_HEARTBEAT_INTERVAL`: 10 seconds - Client activity tracking
- `CLIENT_TIMEOUT`: 30 seconds - Max client inactivity
- `STREAM_BUFFER_SIZE`: 4 MB - Buffer for smooth streaming
- `COPY_CHUNK_SIZE`: 64 KB - Chunk size for copying data
- `ENGINE_SELECTION_TIMEOUT`: 5 seconds - Max time for engine selection
- `ENGINE_CACHE_TTL`: 2 seconds - Engine list cache duration
- `MAX_STREAMS_PER_ENGINE`: 10 - Max concurrent streams per engine
- `SESSION_CLEANUP_INTERVAL`: 60 seconds - Cleanup check frequency

## Monitoring

Monitor proxy health via:

1. **Status Endpoint**: `GET /proxy/status`
   - See active sessions, client counts, errors
   
2. **Prometheus Metrics**: `GET /metrics`
   - Standard orchestrator metrics
   - TODO: Add proxy-specific metrics

3. **Logs**: Check application logs for:
   - Session creation/cleanup
   - Engine selection decisions
   - Stream errors
   - Client connections/disconnections

## Integration with Orchestrator

The proxy integrates seamlessly with the orchestrator:

1. **Engine Selection**: Uses orchestrator's engine state
2. **Health Monitoring**: Respects engine health status
3. **VPN Awareness**: Filters engines by VPN health in redundant mode
4. **Load Balancing**: Considers active streams from all sources

## Best Practices

1. **Client-Side**:
   - Use persistent connections
   - Handle 503 errors with retry logic
   - Don't poll for new streams - the proxy handles multiplexing

2. **Server-Side**:
   - Monitor `/proxy/status` for session buildup
   - Watch for streams with high client counts
   - Ensure sufficient engine capacity

3. **Deployment**:
   - Use the proxy endpoint as your primary video URL
   - Configure load balancers to support long-lived connections
   - Set appropriate timeouts (> 5 minutes)

## Troubleshooting

**Problem**: `503 Service Unavailable`
- **Cause**: No healthy engines available
- **Solution**: Check `/engines` endpoint, verify engines are running and healthy

**Problem**: Stream stuttering or buffering
- **Cause**: Network issues, engine overload, or buffer underrun
- **Solution**: Check engine load via `/proxy/status`, consider adding more engines

**Problem**: High memory usage
- **Cause**: Too many idle sessions
- **Solution**: Reduce `STREAM_IDLE_TIMEOUT` to clean up faster

**Problem**: Clients not multiplexing
- **Cause**: Different content IDs or session creation race
- **Solution**: Verify clients use exact same `id` parameter

## Future Enhancements

Planned features for future releases:

1. **HLS Support**: `/ace/manifest.m3u8` endpoint with segment proxying
2. **Per-Stream Metrics**: Detailed bandwidth and client metrics
3. **Session Affinity**: Sticky sessions for clients
4. **Custom Headers**: Support for authentication headers
5. **Rate Limiting**: Per-client or per-IP rate limits
6. **DVR Support**: Rewind/pause for live streams
