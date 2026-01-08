# AceStream Proxy Implementation Plan

## Overview
Integrate the battle-tested ts_proxy architecture into the AceStream Orchestrator to enable
robust stream multiplexing with all the reliability features of the original ts_proxy.

## Architecture

### Core Components (Adapted from ts_proxy)

1. **ProxyServer** (server.py)
   - Singleton manager for all proxy sessions
   - Worker coordination via Redis
   - Event listener for cross-worker communication
   - Cleanup thread for idle sessions

2. **StreamManager** (stream_manager.py)
   - Manages connection to AceStream engine
   - HTTP stream reading via HTTPStreamReader
   - Health monitoring and automatic recovery
   - Stream switching logic (not applicable for AceStream, but keep structure)

3. **StreamBuffer** (stream_buffer.py)
   - Ring buffer with Redis backing
   - Optimized chunk storage (~1MB chunks)
   - TS packet alignment (188 bytes)
   - TTL-based expiration

4. **ClientManager** (client_manager.py)
   - Tracks clients per stream
   - Heartbeat mechanism to prevent ghost clients
   - Redis-based client registry for multi-worker setups
   - Automatic cleanup of stale clients

5. **StreamGenerator** (stream_generator.py)
   - Generates stream data for individual clients
   - Handles client-specific buffering and rate limiting
   - Graceful initialization waiting
   - Error handling and recovery

6. **HTTPStreamReader** (http_streamer.py) - Already exists in context/ts_proxy
   - Thread-based HTTP reader
   - Pipes data for unified processing
   - Works with both HTTP and transcode sources

### Supporting Modules

7. **RedisKeys** (redis_keys.py)
   - Centralized Redis key management
   - Prevents key conflicts
   - Consistent naming patterns

8. **Constants** (constants.py)
   - Channel states
   - Event types
   - Configuration constants

9. **ConfigHelper** (config_helper.py)
   - Centralized configuration access
   - Environment variable integration

10. **Utils** (utils.py)
    - Logging setup
    - Helper functions

## Integration with Orchestrator

### Key Differences from ts_proxy

1. **No Django** - Replace Django ORM with:
   - Direct orchestrator state access
   - Engine selection from `state.list_engines()`
   - No database models needed

2. **No M3U/Channel Management** - Replace with:
   - AceStream content IDs (infohash)
   - Direct engine API calls

3. **Simplified Stream Selection** - Replace URL switching with:
   - Engine selection logic
   - No alternate streams (single engine serves stream)

4. **Integration Points**:
   - Use existing Redis from orchestrator (already installed in Dockerfile)
   - Integrate with orchestrator's engine state management
   - Reuse existing health monitoring patterns

## Implementation Steps

### Phase 1: Core Infrastructure (Current)
- [x] Add Redis Python client to requirements
- [ ] Copy and adapt constants.py
- [ ] Copy and adapt redis_keys.py
- [ ] Copy and adapt config_helper.py
- [ ] Copy and adapt utils.py

### Phase 2: Buffer and Client Management
- [ ] Adapt stream_buffer.py (remove Django, keep Redis logic)
- [ ] Adapt client_manager.py (remove Django, keep heartbeat logic)
- [ ] Adapt http_streamer.py (already compatible, just copy)

### Phase 3: Stream Management
- [ ] Adapt stream_manager.py:
  - Remove Django models
  - Remove URL switching
  - Add AceStream engine API integration
  - Keep health monitoring
  - Keep reconnection logic

### Phase 4: Server and Generator
- [ ] Adapt server.py:
  - Remove Django integration
  - Keep worker coordination
  - Keep cleanup logic
  - Keep event listener

- [ ] Adapt stream_generator.py:
  - Remove Django models
  - Keep buffering logic
  - Keep rate limiting
  - Keep initialization waiting

### Phase 5: FastAPI Integration
- [ ] Update main.py endpoints to use ProxyServer
- [ ] Add startup/shutdown hooks
- [ ] Test multi-client scenarios

## Redis Schema

```
Keys used by proxy:

ace_proxy:stream:{content_id}:buffer:index           - Current buffer index
ace_proxy:stream:{content_id}:buffer:chunk:{index}   - Individual chunks
ace_proxy:stream:{content_id}:clients                - Set of client IDs
ace_proxy:stream:{content_id}:clients:{client_id}    - Client metadata
ace_proxy:stream:{content_id}:metadata               - Stream metadata
ace_proxy:stream:{content_id}:owner                  - Worker owning this stream
ace_proxy:stream:{content_id}:worker:{worker_id}     - Worker info
ace_proxy:stream:{content_id}:activity               - Last activity timestamp
ace_proxy:stream:{content_id}:events                 - PubSub channel for events
```

## Configuration

Environment variables:
```
# Proxy-specific settings
PROXY_CHUNK_SIZE=1048576              # 1MB chunks
PROXY_BUFFER_TTL=60                   # Chunk TTL in seconds
PROXY_CLIENT_TTL=60                   # Client record TTL
PROXY_HEARTBEAT_INTERVAL=10           # Client heartbeat interval
PROXY_CLEANUP_INTERVAL=60             # Server cleanup interval
PROXY_INIT_TIMEOUT=30                 # Stream initialization timeout
PROXY_GRACE_PERIOD=5                  # Shutdown grace period
```

## Testing Plan

1. **Single Client Test**
   - Request stream via /ace/getstream?id=<infohash>
   - Verify engine selection
   - Verify stream playback

2. **Multi-Client Test**
   - Multiple clients request same infohash
   - Verify they share same engine/session
   - Verify buffer multiplexing

3. **Client Disconnect Test**
   - Client disconnects
   - Verify cleanup after grace period

4. **Engine Failure Test**
   - Stop engine during playback
   - Verify error handling

5. **Load Balancing Test**
   - Multiple different streams
   - Verify engine selection distributes load
   - Verify forwarded engines preferred

## Next Agent Tasks

If you run out of time, the next agent should:

1. **Complete Phase 1** - Copy remaining infrastructure files
2. **Complete Phase 2** - Adapt buffer and client management
3. **Test Integration** - Verify Redis connectivity and basic operations
4. **Document** - Update README with proxy usage instructions

The foundation has been laid with:
- Redis dependency added
- Directory structure created
- Core proxy service stubs created (will be replaced with adapted ts_proxy)
- FastAPI integration points identified in main.py
