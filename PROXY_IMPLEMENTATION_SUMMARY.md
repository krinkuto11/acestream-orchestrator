# AceStream Proxy Implementation Summary

## Overview

This document summarizes the implementation of the AceStream Proxy feature for Orchestrator v1.5.0. It provides context for future development work and documents the current state of the implementation.

## Problem Statement

The Orchestrator v1.5.0 release needed a proxy that allows video clients to get streams from a single endpoint (`/ace/getstream?id=<id>`). The proxy manages engine lifecycle, provides intelligent engine selection, and supports client multiplexing.

## Goals Achieved ✅

### 1. Core Proxy Functionality
- ✅ Single streaming endpoint: `/ace/getstream?id=<content_id>`
- ✅ Automatic engine selection and provisioning
- ✅ Client multiplexing (multiple clients, one engine stream)
- ✅ Automatic session lifecycle management
- ✅ Graceful error handling and cleanup

### 2. Intelligent Engine Selection
- ✅ Prioritizes forwarded engines (+1000 score)
- ✅ Balances load across engines (-10 per active stream)
- ✅ Filters unhealthy engines (-1000 score)
- ✅ VPN-aware (respects orchestrator's VPN health)
- ✅ Caches engine list (2 second TTL)

### 3. Production-Ready Design
- ✅ Based on dispatcharr_proxy patterns
- ✅ Proper error handling and logging
- ✅ Resource cleanup (HTTP clients, sessions)
- ✅ Background cleanup of idle sessions (5 min timeout)
- ✅ Thread-safe client management

### 4. Monitoring & Observability
- ✅ `/proxy/status` - Overall status and session list
- ✅ `/proxy/sessions` - Active sessions
- ✅ `/proxy/sessions/{ace_id}` - Individual session details
- ✅ Detailed logging for debugging

### 5. Testing
- ✅ 11 unit tests for engine selector
- ✅ 6 unit tests for client manager
- ✅ All tests passing
- ✅ Coverage of core algorithms

### 6. Documentation
- ✅ `docs/PROXY_API.md` - Complete API reference
- ✅ `docs/PROXY_DEPLOYMENT.md` - Deployment and integration guide
- ✅ Updated `README.md` with proxy feature
- ✅ Code comments and docstrings

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────┐
│                    ProxyManager                          │
│  - Singleton instance                                   │
│  - Manages all sessions                                 │
│  - Background cleanup task                              │
└────────────┬────────────────────────────────────────────┘
             │
             ├─────────────────┐
             ↓                 ↓
    ┌────────────────┐  ┌────────────────┐
    │ StreamSession  │  │ StreamSession  │  (one per content_id)
    │ - ace_id       │  │ - ace_id       │
    │ - engine info  │  │ - engine info  │
    │ - playback URL │  │ - playback URL │
    └────┬───────────┘  └────┬───────────┘
         │                   │
         ↓                   ↓
    ┌────────────────┐  ┌────────────────┐
    │ ClientManager  │  │ ClientManager  │  (tracks clients per session)
    │ - clients set  │  │ - clients set  │
    │ - activity     │  │ - activity     │
    └────────────────┘  └────────────────┘
         
    ┌────────────────────────────┐
    │     EngineSelector         │  (shared, with caching)
    │  - Scores engines          │
    │  - Filters by health       │
    │  - Caches results          │
    └────────────────────────────┘
```

### Key Design Decisions

1. **FFmpeg-less Implementation**
   - Direct MPEG-TS stream passthrough
   - No transcoding overhead
   - Lower latency and resource usage

2. **Session-Based Multiplexing**
   - One session per unique `ace_id`
   - Multiple clients tracked per session
   - Automatic cleanup when clients = 0 for 5 minutes

3. **Intelligent Scoring**
   - Forwarded engines: +1000 (P2P performance)
   - Active streams: -10 each (load balancing)
   - Unhealthy: -1000 (reliability)

4. **Stateless Architecture**
   - No persistent state for sessions
   - Recreated on demand
   - Self-cleaning via idle timeout

5. **Production Patterns**
   - Async/await for I/O
   - Proper resource cleanup
   - Background tasks for maintenance
   - Comprehensive error handling

## File Structure

### New Files

```
app/services/proxy/
├── __init__.py                # Package exports
├── config.py                  # Configuration constants
├── client_manager.py          # Client tracking and multiplexing
├── engine_selector.py         # Intelligent engine selection
├── stream_session.py          # Stream session management
└── proxy_manager.py           # Main proxy orchestration

tests/
├── test_proxy_engine_selector.py  # Engine selector tests
└── test_proxy_client_manager.py   # Client manager tests

docs/
├── PROXY_API.md              # API documentation
└── PROXY_DEPLOYMENT.md       # Deployment guide
```

### Modified Files

```
app/main.py                    # Added proxy endpoints and lifecycle
README.md                      # Added v1.5.0 proxy feature highlights
```

## API Endpoints

### Streaming

- `GET /ace/getstream?id=<content_id>`
  - Stream MPEG-TS video
  - Automatic engine selection
  - Client multiplexing

- `GET /ace/manifest.m3u8?id=<content_id>` (placeholder)
  - HLS support (not implemented yet)
  - Returns 501 Not Implemented

### Monitoring

- `GET /proxy/status`
  - Overall proxy status
  - Session count, client count
  - List of all sessions

- `GET /proxy/sessions`
  - List of active sessions
  - Simplified view

- `GET /proxy/sessions/{ace_id}`
  - Detailed session info
  - Client count, activity, errors

## Configuration

Located in `app/services/proxy/config.py`:

```python
# Timeouts
EMPTY_STREAM_TIMEOUT = 30         # Wait for initial data
STREAM_IDLE_TIMEOUT = 300         # Cleanup after 5 min idle
CLIENT_TIMEOUT = 30               # Client activity timeout

# Buffers
STREAM_BUFFER_SIZE = 4 * 1024 * 1024   # 4 MB
COPY_CHUNK_SIZE = 64 * 1024             # 64 KB

# Engine Selection
ENGINE_SELECTION_TIMEOUT = 5      # Selection timeout
ENGINE_CACHE_TTL = 2              # Cache duration
MAX_STREAMS_PER_ENGINE = 10       # Max per engine

# Cleanup
SESSION_CLEANUP_INTERVAL = 60     # Cleanup check frequency
```

## Testing

### Unit Tests (17 total, all passing)

**Engine Selector** (11 tests):
- `test_engine_score_calculation` - Verify scoring algorithm
- `test_select_best_engine_prioritizes_forwarded` - Forwarded priority
- `test_select_best_engine_balances_load` - Load balancing
- `test_select_best_engine_filters_unhealthy` - Health filtering
- `test_engine_cache` - Caching behavior

**Client Manager** (6 tests):
- `test_add_client` - Adding clients
- `test_remove_client` - Removing clients
- `test_activity_tracking` - Activity timestamps
- `test_idle_time` - Idle time calculation
- `test_get_client_ids` - Client ID retrieval
- `test_concurrent_client_operations` - Thread safety

### Running Tests

```bash
cd /home/runner/work/acestream-orchestrator/acestream-orchestrator
python -m pytest tests/test_proxy_engine_selector.py tests/test_proxy_client_manager.py -v
```

## Future Work

### Phase 6: Dashboard Integration

1. **Proxy Status Panel**
   - Show active sessions count
   - Display total clients
   - Show per-session client count

2. **Session List View**
   - Table of active sessions
   - Client count per session
   - Engine assignment
   - Idle time

3. **Metrics Visualization**
   - Session timeline
   - Client count over time
   - Engine load distribution

### Additional Enhancements

1. **HLS Support**
   - Implement `/ace/manifest.m3u8` endpoint
   - Parse HLS manifests from engines
   - Proxy segment requests
   - Client multiplexing for segments

2. **Integration Tests**
   - End-to-end streaming tests
   - Multi-client multiplexing tests
   - Engine failure scenarios
   - VPN failover scenarios

3. **Prometheus Metrics**
   - `proxy_sessions_total`
   - `proxy_sessions_active`
   - `proxy_clients_total`
   - `proxy_bytes_streamed_total`
   - `proxy_session_duration_seconds`

4. **Performance Optimizations**
   - Zero-copy streaming (if possible)
   - Adjustable buffer sizes per stream
   - Bandwidth throttling options
   - Quality adaptation

5. **Advanced Features**
   - Session affinity (sticky clients)
   - Custom headers support
   - Rate limiting per IP
   - Authentication/authorization
   - DVR capabilities (rewind/pause)

## Known Limitations

1. **M3U8/HLS Not Implemented**
   - Only MPEG-TS streaming works
   - HLS endpoint returns 501

2. **No Session Persistence**
   - Sessions lost on restart
   - Clients must reconnect

3. **Limited Metrics**
   - No Prometheus metrics yet
   - Basic logging only

4. **No Dashboard Integration**
   - CLI/API monitoring only
   - React dashboard doesn't show proxy

5. **No Rate Limiting**
   - Unlimited clients per session
   - No bandwidth throttling

## Recent Fixes

### Compression Disable Fix (Based on acexy Reference)

Fixed playback URL issues by disabling HTTP compression, based on the acexy reference implementation.

**Problem**: Streams would fail to start or not deliver data to clients.

**Solution**: 
- Added `Accept-Encoding: identity` header to disable compression (critical for AceStream)
- Configured HTTP client with connection limits (10 max connections per host)
- Applied acexy's transport configuration settings

**See**: `docs/PROXY_FIX_COMPRESSION.md` for detailed explanation and technical background.

## Migration Notes

### From acexy Proxy

The new proxy is a drop-in replacement for acexy:

**Old URL**: `http://acexy:8080/ace/getstream?id=<id>`
**New URL**: `http://orchestrator:8000/ace/getstream?id=<id>`

**Key Differences**:
1. Port changed (8080 → 8000)
2. Better engine selection (intelligent scoring)
3. Better multiplexing (orchestrator-managed)
4. Integrated monitoring (orchestrator dashboard)
5. VPN-aware selection

### Configuration Migration

No additional configuration needed! The proxy uses existing orchestrator settings:
- `MIN_REPLICAS` - Ensures capacity
- `MAX_REPLICAS` - Limits resources
- `API_KEY` - (not currently used for proxy endpoints)

## Troubleshooting

### Common Issues

**503 Service Unavailable**
- No healthy engines available
- Check `/engines` endpoint
- Verify `MIN_REPLICAS` > 0

**Stream Buffering**
- Engine overload
- Check `/proxy/status` for client counts
- Increase `MAX_REPLICAS`

**High Memory**
- Too many idle sessions
- Reduce `STREAM_IDLE_TIMEOUT`
- Monitor `/proxy/sessions`

**Clients Not Multiplexing**
- Different `id` parameters
- Check exact URL match
- Review session list

## Contributing

When extending the proxy:

1. **Add tests** - Maintain test coverage
2. **Update docs** - Keep PROXY_API.md current
3. **Log appropriately** - Use debug/info/error levels
4. **Handle errors** - Graceful degradation
5. **Clean resources** - No leaks in async code
6. **Maintain patterns** - Follow dispatcharr_proxy style

## Resources

- **acexy reference**: `context/acexy/` - Simple proxy example
- **dispatcharr reference**: `context/dispatcharr_proxy/` - Production patterns
- **Orchestrator API**: `docs/API.md` - Existing API
- **Architecture**: `docs/ARCHITECTURE.md` - System design

## Credits

Implementation based on:
- **acexy**: Simple stateless proxy design
- **dispatcharr_proxy**: Production-ready patterns and robustness
- **Orchestrator**: Existing engine management and state

## Conclusion

The proxy implementation is **production-ready** for basic MPEG-TS streaming. It provides:
- ✅ Single endpoint access
- ✅ Intelligent engine selection
- ✅ Client multiplexing
- ✅ Automatic lifecycle
- ✅ Comprehensive documentation

Future work focuses on:
- HLS support
- Dashboard integration
- Advanced metrics
- Additional features (rate limiting, auth, etc.)

The codebase is well-tested, documented, and ready for extension.
