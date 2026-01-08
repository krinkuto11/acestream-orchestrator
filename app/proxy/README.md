# AceStream Proxy

Battle-tested stream multiplexing proxy adapted from ts_proxy.

## Status

**Foundation Complete** ✅  
**Implementation In Progress** ⏳

## Quick Start (For Next Agent)

1. Read `../IMPLEMENTATION_SUMMARY.md`
2. Read `../NEXT_AGENT_INSTRUCTIONS.md`  
3. Copy files from `../../context/ts_proxy/` to this directory
4. Adapt as documented
5. Test with provided scenarios

## Files

### Complete ✅
- `constants.py` - States, events, metadata fields
- `redis_keys.py` - Centralized Redis key management

### TODO ⏳
- `utils.py` - Copy from context/ts_proxy, remove Django
- `config_helper.py` - Copy from context/ts_proxy, use env vars
- `http_streamer.py` - Copy as-is from context/ts_proxy
- `stream_buffer.py` - Copy from context/ts_proxy, remove Django
- `client_manager.py` - Copy from context/ts_proxy, remove Django
- `stream_generator.py` - Copy from context/ts_proxy, remove Django
- `stream_manager.py` - Copy from context/ts_proxy, add AceStream API
- `server.py` - Copy from context/ts_proxy, integrate orchestrator
- `manager.py` - Create thin wrapper for FastAPI

## Architecture

```
Client Request → ProxyServer → StreamManager → AceStream Engine
                      ↓              ↓
                StreamBuffer ← HTTPStreamReader
                      ↓
              StreamGenerator → Client Response
```

## Documentation

- **NEXT_AGENT_INSTRUCTIONS.md** - Complete step-by-step guide
- **PROXY_QUICK_REFERENCE.md** - Architecture and examples
- **PROXY_IMPLEMENTATION_PLAN.md** - Detailed design

## Testing

See `../PROXY_QUICK_REFERENCE.md` for:
- Testing scenarios
- Example commands
- Expected results
- Debugging tips
