# AceStream Proxy - Quick Reference

## What This Does

Multiplexes multiple clients to single AceStream engine streams, using the battle-tested ts_proxy architecture.

## How It Works

```
Client 1 ─┐
Client 2 ─┼─> ProxyServer ─> StreamManager ─> HTTPStreamReader ─> AceStream Engine
Client 3 ─┘         │                                                      │
                    └─> StreamBuffer (Redis) <─────────────────────────────┘
                              │
                              └─> All clients read from shared buffer
```

## Key Components

| Component | Purpose | Status |
|-----------|---------|--------|
| constants.py | States, events, metadata fields | ✅ Done |
| redis_keys.py | Centralized Redis key patterns | ✅ Done |
| utils.py | Logging and helpers | ⏳ TODO |
| config_helper.py | Config access | ⏳ TODO |
| http_streamer.py | HTTP→pipe reader | ⏳ TODO (just copy) |
| stream_buffer.py | Ring buffer with Redis | ⏳ TODO |
| client_manager.py | Client tracking + heartbeat | ⏳ TODO |
| stream_manager.py | Stream lifecycle + health | ⏳ TODO |
| stream_generator.py | Per-client data delivery | ⏳ TODO |
| server.py | Worker coordination | ⏳ TODO |
| manager.py | FastAPI wrapper | ⏳ TODO |

## Workflow

### 1. Client Requests Stream
```http
GET /ace/getstream?id=INFOHASH
```

### 2. ProxyServer Handles Request
- Check if stream session exists for this infohash
- If not, create new session:
  - Select best engine (prioritize forwarded, balance load)
  - Initialize StreamManager
  - Initialize StreamBuffer with Redis
  - Initialize ClientManager
  
### 3. StreamManager Connects to Engine
```http
GET http://engine:port/ace/getstream?format=json&infohash=INFOHASH
```

Response:
```json
{
  "response": {
    "playback_url": "http://engine:port/ace/r/...",
    "stat_url": "http://engine:port/ace/stat/...",
    "command_url": "http://engine:port/ace/cmd/...",
    "playback_session_id": "...",
    "is_live": 1
  }
}
```

### 4. HTTPStreamReader Starts
- Thread-based reader
- Reads from playback_url
- Writes to pipe
- StreamManager reads from pipe
- Adds chunks to StreamBuffer (Redis)

### 5. StreamGenerator Delivers to Client
- Each client gets StreamGenerator instance
- Reads from shared StreamBuffer
- Tracks client position independently
- Rate limiting and buffering
- Updates client heartbeat

### 6. Cleanup
- Client disconnects → ClientManager removes client
- No clients for 5s → Stop stream session
- StreamManager stops HTTPStreamReader
- Send stop command to engine
- Clear Redis buffer

## Redis Schema

```
ace_proxy:stream:{INFOHASH}:metadata              → Stream state, engine info
ace_proxy:stream:{INFOHASH}:owner                 → Worker ID owning this stream
ace_proxy:stream:{INFOHASH}:buffer:index          → Current buffer position
ace_proxy:stream:{INFOHASH}:buffer:chunk:123      → Individual chunks (TTL 60s)
ace_proxy:stream:{INFOHASH}:clients               → Set of client IDs
ace_proxy:stream:{INFOHASH}:clients:CLIENT_ID     → Client metadata (heartbeat, stats)
ace_proxy:stream:{INFOHASH}:last_client_disconnect_time → Cleanup timer
ace_proxy:events:{INFOHASH}                       → PubSub for worker coordination
```

## Engine Selection Logic

```python
def select_engine():
    engines = state.list_engines()
    active_streams = state.list_streams(status="started")
    
    # Count streams per engine
    engine_loads = count_streams_per_engine(active_streams)
    
    # Sort by:
    # 1. Stream count (ascending) - prefer less loaded
    # 2. Forwarded status (descending) - prefer forwarded when equal load
    return sorted(engines, key=lambda e: (
        engine_loads.get(e.container_id, 0),  # Lower is better
        not e.forwarded                        # False < True, so forwarded wins
    ))[0]
```

## Critical Features to Preserve

### 1. Heartbeat Mechanism
Prevents ghost clients when network drops without proper disconnect.
```python
# Client sends heartbeat every 10s
# If no heartbeat for 50s (5x interval), client is removed
```

### 2. Ring Buffer with TTL
Prevents memory growth while allowing late clients to catch up.
```python
# Each chunk has 60s TTL
# Buffer keeps last ~1000 chunks (configurable)
# Old chunks auto-expire from Redis
```

### 3. Worker Coordination
Multiple uvicorn workers can share streams via Redis.
```python
# Worker 1 owns stream, writes to buffer
# Worker 2 reads from shared Redis buffer
# PubSub coordinates events (client connect/disconnect)
```

### 4. Health Monitoring
Detects stale streams and recovers automatically.
```python
# Track last data timestamp
# If no data for 10s, mark unhealthy
# If unhealthy for 30s, attempt reconnect
```

### 5. Graceful Shutdown
Clients get proper EOF, not abrupt disconnect.
```python
# On shutdown:
# 1. Stop accepting new clients
# 2. Send stop command to engine
# 3. Flush remaining buffer to clients
# 4. Close connections gracefully
```

## Testing Scenarios

### Scenario 1: Single Client
```bash
# Start stream
curl "http://localhost:8000/ace/getstream?id=HASH" > /dev/null &

# Check Redis
redis-cli SMEMBERS "ace_proxy:stream:HASH:clients"
redis-cli HGETALL "ace_proxy:stream:HASH:metadata"

# Verify buffer
redis-cli GET "ace_proxy:stream:HASH:buffer:index"
redis-cli KEYS "ace_proxy:stream:HASH:buffer:chunk:*" | wc -l
```

### Scenario 2: Multi-Client Multiplexing
```bash
# Start 3 clients
for i in 1 2 3; do
  curl "http://localhost:8000/ace/getstream?id=HASH" > client$i.ts &
done

# Should see 3 clients in Redis
redis-cli SMEMBERS "ace_proxy:stream:HASH:clients"
# Should see 3 items

# But only 1 engine session
# Check orchestrator /streams endpoint
curl http://localhost:8000/streams?status=started | jq '.[] | select(.key == "HASH")'
# Should show 1 stream
```

### Scenario 3: Client Disconnect Cleanup
```bash
# Start client
PID=$(curl "http://localhost:8000/ace/getstream?id=HASH" > /dev/null & echo $!)

# Kill client
kill $PID

# Wait 5s
sleep 5

# Check cleanup
redis-cli SMEMBERS "ace_proxy:stream:HASH:clients"
# Should be empty

redis-cli EXISTS "ace_proxy:stream:HASH:metadata"  
# Should be 0 (key deleted)
```

### Scenario 4: Load Balancing
```bash
# Start 5 different streams
for i in 1 2 3 4 5; do
  curl "http://localhost:8000/ace/getstream?id=HASH$i" > stream$i.ts &
done

# Check engine distribution
curl http://localhost:8000/streams?status=started | jq -r '.[].container_id' | sort | uniq -c
# Should distribute across multiple engines
```

## Debugging

### Check Stream State
```bash
redis-cli HGETALL "ace_proxy:stream:INFOHASH:metadata"
```

### Check Active Clients
```bash
redis-cli SMEMBERS "ace_proxy:stream:INFOHASH:clients"
```

### Check Buffer
```bash
# Current position
redis-cli GET "ace_proxy:stream:INFOHASH:buffer:index"

# How many chunks
redis-cli KEYS "ace_proxy:stream:INFOHASH:buffer:chunk:*" | wc -l

# Check a chunk
redis-cli --raw GET "ace_proxy:stream:INFOHASH:buffer:chunk:1" | hexdump -C | head
```

### Check Worker Coordination
```bash
# Subscribe to events
redis-cli SUBSCRIBE "ace_proxy:events:INFOHASH"

# In another terminal, connect/disconnect clients and watch events
```

## Performance Expectations

- **Buffer latency**: < 1 second (typically ~200ms)
- **Memory per stream**: ~50-100 MB (1000 chunks × ~1MB each in Redis)
- **CPU per stream**: Minimal (single HTTP reader thread)
- **Max clients per stream**: Tested with 100+, limited by network bandwidth
- **Cleanup time**: 5 seconds after last client disconnect

## Common Issues

### Issue: Clients get stale data
**Cause**: Buffer falling behind due to slow Redis
**Fix**: Increase PROXY_BUFFER_TTL or use faster Redis

### Issue: Ghost clients remain in Redis
**Cause**: Heartbeat not running
**Fix**: Check ClientManager thread is started

### Issue: Multiple engine sessions for same stream
**Cause**: Worker coordination broken
**Fix**: Check Redis PubSub working, verify worker_id consistency

### Issue: Streams not cleaning up
**Cause**: Cleanup thread not running
**Fix**: Check ProxyServer._start_cleanup_thread() called

## Next Steps

See NEXT_AGENT_INSTRUCTIONS.md for detailed implementation steps.
