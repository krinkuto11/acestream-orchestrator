# Next Agent Instructions - AceStream Proxy Completion

## Current Status

### ✅ Completed
1. **Infrastructure Setup**
   - Added Redis client dependency (redis==5.0.1)
   - Added gevent for async greenlet support (gevent==24.2.1)
   - Added requests for HTTP streaming (requests==2.31.0)
   - Created `/app/proxy` directory structure
   - Created adapted constants.py and redis_keys.py

2. **Planning**
   - Complete architecture documented in `PROXY_IMPLEMENTATION_PLAN.md`
   - Redis schema defined
   - Integration points identified

### 🔄 In Progress
The foundation has been laid but the full ts_proxy adaptation is incomplete.

## What You Need to Do

### Priority 1: Complete Core Proxy Components (CRITICAL)

Copy and adapt these files from `context/ts_proxy/` to `app/proxy/`:

#### 1. **utils.py** (Simple, do first)
```bash
# Copy from context/ts_proxy/utils.py
# Changes needed:
# - Remove Django imports
# - Keep logging setup
# - Keep helper functions
```

#### 2. **config_helper.py** (Simple)
```bash
# Copy from context/ts_proxy/config_helper.py
# Changes needed:
# - Remove Django settings import
# - Use environment variables directly via os.getenv()
# - Keep all the helper methods
```

#### 3. **http_streamer.py** (Can copy as-is)
```bash
# Copy from context/ts_proxy/http_streamer.py
# This file is already standalone and compatible!
# Just copy it to app/proxy/http_streamer.py
```

#### 4. **stream_buffer.py** (Medium complexity)
```bash
# Copy from context/ts_proxy/stream_buffer.py
# Changes needed:
# - Remove Django imports  
# - Keep all Redis logic
# - Keep gevent integration
# - Adapt channel_id -> content_id naming
# - Use RedisKeys from app.proxy.redis_keys
```

#### 5. **client_manager.py** (Medium complexity)
```bash
# Copy from context/ts_proxy/client_manager.py
# Changes needed:
# - Remove Django imports
# - Keep heartbeat logic
# - Keep Redis client tracking
# - Adapt channel_id -> content_id naming
```

#### 6. **stream_manager.py** (High complexity - MOST IMPORTANT)
```bash
# Copy from context/ts_proxy/stream_manager.py
# Major changes needed:
# - Remove ALL Django model imports
# - Remove URL/stream switching logic (not needed for AceStream)
# - Remove transcode support (AceStream serves HTTP directly)
# - KEEP: HTTPStreamReader integration
# - KEEP: Health monitoring
# - KEEP: Reconnection logic
# - ADD: AceStream engine API integration:
#   * Request stream: GET http://engine:port/ace/getstream?format=json&infohash=<id>
#   * Parse response JSON to get playback_url
#   * Use HTTPStreamReader to read from playback_url
# - Adapt channel_id -> content_id naming
```

#### 7. **stream_generator.py** (Medium complexity)
```bash
# Copy from context/ts_proxy/stream_generator.py
# Changes needed:
# - Remove Django imports
# - Remove channel model references
# - Keep buffering and rate limiting logic
# - Keep initialization waiting logic
# - Adapt to use content_id instead of channel_id
```

#### 8. **server.py** (High complexity)
```bash
# Copy from context/ts_proxy/server.py
# Changes needed:
# - Remove Django imports
# - Keep singleton pattern
# - Keep worker coordination via Redis
# - Keep cleanup thread
# - Keep event listener for PubSub
# - Adapt channel_id -> content_id
# - Integrate with orchestrator's engine selection:
#   * Import from app.services.state import state
#   * Use state.list_engines() for engine selection
#   * Prioritize forwarded engines
#   * Balance load across engines
```

### Priority 2: Delete Old Simplified Implementation

Remove these files you created earlier (they'll be replaced by adapted ts_proxy):
```bash
rm app/services/proxy_manager.py
rm app/services/proxy_session.py
rm app/services/proxy_client_manager.py
rm app/services/proxy_buffer.py
```

### Priority 3: Create New ProxyManager Wrapper

Create `app/proxy/manager.py` as a thin wrapper:
```python
"""
ProxyManager - Entry point for AceStream proxy.
Wraps the battle-tested ts_proxy ProxyServer.
"""

from .server import ProxyServer

class ProxyManager:
    """Singleton wrapper around ProxyServer for FastAPI integration."""
    
    @classmethod
    def get_instance(cls):
        return ProxyServer.get_instance()
```

Then update `app/main.py`:
```python
# Change this import:
from .services.proxy_manager import ProxyManager
# To:
from .proxy.manager import ProxyManager
```

### Priority 4: Integration and Testing

1. **Test Redis Connection**
```python
# In app/proxy/, create test_redis.py:
import redis
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
r.ping()  # Should return True
```

2. **Test Basic Proxy**
```bash
# Start orchestrator
docker-compose up -d

# Request a stream (replace with real infohash)
curl "http://localhost:8000/ace/getstream?id=INFOHASH_HERE"
```

3. **Test Multi-Client**
Open 2-3 browser tabs to same stream URL, verify they share single engine session.

### Priority 5: Configuration

Add to `.env.example`:
```bash
# Proxy Configuration
PROXY_CHUNK_SIZE=1048576              # 1MB chunks
PROXY_BUFFER_TTL=60                   # Chunk TTL in seconds  
PROXY_CLIENT_TTL=60                   # Client record TTL
PROXY_HEARTBEAT_INTERVAL=10           # Client heartbeat interval
PROXY_CLEANUP_INTERVAL=60             # Server cleanup interval
PROXY_INIT_TIMEOUT=30                 # Stream initialization timeout
PROXY_GRACE_PERIOD=5                  # Shutdown grace period

# Redis (already configured, but document for proxy)
REDIS_HOST=localhost
REDIS_PORT=6379
```

## Key Adaptation Patterns

### Pattern 1: Replace Django Models
```python
# OLD (ts_proxy):
from apps.channels.models import Channel
channel = Channel.objects.get(uuid=channel_id)

# NEW (ace_proxy):
# No database access needed! Use orchestrator state:
from app.services.state import state
engines = state.list_engines()
```

### Pattern 2: Replace Channel/Stream Selection
```python
# OLD (ts_proxy):
stream = get_stream_object(channel_id)
url = stream.url

# NEW (ace_proxy):
# Select engine from orchestrator
engine = select_best_engine()  # Your logic
# Request stream from AceStream API
url = request_acestream(engine, content_id)
```

### Pattern 3: Keep Redis Patterns
```python
# KEEP ALL REDIS LOGIC AS-IS!
# Just change key prefixes via RedisKeys class
metadata_key = RedisKeys.stream_metadata(content_id)
redis_client.hset(metadata_key, mapping=data)
```

### Pattern 4: Keep Gevent Patterns
```python
# KEEP gevent AS-IS!
import gevent
gevent.sleep(1)  # Instead of time.sleep()
gevent.spawn(func)  # For background tasks
```

## Testing Checklist

- [ ] Redis connection works
- [ ] Single client can request and play stream
- [ ] Multiple clients share same engine/buffer
- [ ] Client disconnect triggers cleanup after grace period
- [ ] Engine selection prioritizes forwarded engines
- [ ] Engine selection balances load
- [ ] Buffer maintains proper ring buffer with TTL
- [ ] Heartbeat prevents ghost clients
- [ ] Worker coordination works (if testing multi-worker)

## Common Pitfalls

1. **Don't simplify the buffer logic** - It's complex for good reasons (TTL, ring buffer, TS alignment)
2. **Don't remove gevent** - It's essential for the threading model
3. **Keep the heartbeat mechanism** - It prevents ghost client issues
4. **Don't skip the cleanup thread** - It prevents memory leaks
5. **Test with real AceStream engines** - Mock testing won't catch edge cases

## Files Reference

All source files are in: `context/ts_proxy/`
All target files go in: `app/proxy/`

Good luck! The architecture is sound, you just need to adapt the Django bits to work standalone.
