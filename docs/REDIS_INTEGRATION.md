# Redis Integration for Stream Multiplexing

## Overview

Redis is integrated into the AceStream Orchestrator Docker container to provide persistent buffering for stream multiplexing. This enables multiple clients to efficiently stream from the same AceStream source with independent playback positions.

## What's Included

### 1. Redis Server
The Dockerfile includes `redis-server` which runs automatically when the container starts:
- Runs on `127.0.0.1:6379` (localhost only, not exposed externally)
- Starts as a daemon before the FastAPI application
- Configuration can be customized via environment variables

### 2. Python Redis Client
The `redis` Python package (v5.0.1) is included in `requirements.txt` and provides:
- Connection management via `app.core.utils.RedisClient`
- Automatic fallback to in-memory mode if Redis is unavailable
- Configurable connection parameters

### 3. Stream Buffer
The `StreamBuffer` class uses Redis for:
- Storing stream chunks with 5-minute TTL
- TS packet alignment (188-byte boundaries)
- Independent client read positions
- Automatic cleanup of expired data

## Configuration

### Environment Variables

Add these to your `.env` file to customize Redis connection (optional):

```bash
# Redis Configuration (for stream multiplexing buffer)
REDIS_HOST=127.0.0.1    # Redis server host
REDIS_PORT=6379         # Redis server port
REDIS_DB=0              # Redis database number
```

### Default Behavior

If not configured, the orchestrator will:
1. Try to connect to Redis at `127.0.0.1:6379`
2. Fall back to in-memory buffering if Redis is unavailable
3. Log connection status on startup

## Docker Container

The container startup sequence:
1. Start Redis server as daemon (`redis-server --daemonize yes`)
2. Wait for Redis to be ready (`redis-cli ping`)
3. Start FastAPI application

This is handled automatically by the `/app/start.sh` script in the container.

## External Redis

To use an external Redis instance instead of the bundled one:

1. Set environment variables in `.env`:
   ```bash
   REDIS_HOST=your-redis-host
   REDIS_PORT=6379
   REDIS_DB=0
   ```

2. Optionally remove Redis installation from Dockerfile to save space

## Benefits

### With Redis (Default)
- ✅ Persistent buffer across worker restarts
- ✅ Efficient memory usage (chunks stored in Redis)
- ✅ Better scalability with many concurrent clients
- ✅ Buffer survives application crashes

### Without Redis (Fallback)
- ⚠️ In-memory buffer only (lost on restart)
- ⚠️ Higher memory usage per stream
- ✅ Still functional (automatic fallback)
- ✅ No external dependencies

## Monitoring

Check Redis status:
```bash
# Inside container
redis-cli ping
# Should return: PONG

# Check memory usage
redis-cli info memory

# List all keys
redis-cli --scan
```

## Troubleshooting

### Redis won't start
- Check container logs: `docker logs <container_name>`
- Ensure port 6379 is not already in use
- Verify Redis is installed: `redis-server --version`

### Connection failures
- Application will log warnings and fall back to in-memory mode
- Check `REDIS_HOST` and `REDIS_PORT` environment variables
- Verify Redis is running: `redis-cli ping`

### High memory usage
- Chunks expire after 5 minutes (configurable in code)
- Use `redis-cli info memory` to check Redis memory
- Consider reducing `MEMORY_BUFFER_SIZE` in config

## Technical Details

### Buffer Storage Pattern

```
Redis Keys:
  ace_proxy:buffer:{stream_id}:index        → Current write position
  ace_proxy:buffer:{stream_id}:chunk:{N}    → Chunk data at index N

TTL: 300 seconds (5 minutes)
Chunk Size: ~320KB (64KB * 5)
```

### Connection Management

The `RedisClient` class provides:
- Singleton pattern (one connection per process)
- Automatic reconnection on failure
- Connection pooling
- Graceful degradation to in-memory mode

### Code Example

```python
from app.core.utils import RedisClient

# Get Redis client (returns None if unavailable)
redis_client = RedisClient.get_client()

if redis_client:
    # Redis available - use persistent buffer
    redis_client.set("key", "value")
else:
    # Redis unavailable - use in-memory fallback
    print("Using in-memory buffer")
```

## See Also

- `app/core/utils.py` - RedisClient implementation
- `app/services/proxy/stream_buffer.py` - Buffer with Redis backend
- `PROXY_IMPLEMENTATION_SUMMARY.md` - Stream multiplexing overview
- `STREAM_MULTIPLEXING_REIMPLEMENTATION.md` - Architecture details
