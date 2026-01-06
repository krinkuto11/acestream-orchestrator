# Stream Proxy Multiplexing Fix

## Problem Description

When a player connects to the stream proxy for the first time, it receives no video/audio data (though it doesn't error out). After stopping and reconnecting, the stream works perfectly. This created a poor user experience requiring users to reconnect to get their streams working.

### Root Cause

The issue was that **true multiplexing was not implemented**. Each client that called `session.stream_data()` created a **new HTTP streaming request** to the AceStream engine. This meant:

1. **First connection**: Creates new HTTP stream → AceStream engine buffers data → Client might timeout before data arrives
2. **Second connection**: Creates another HTTP stream → By now AceStream has buffered data → Client receives data immediately

The PROXY_IMPLEMENTATION_SUMMARY.md claimed "Client multiplexing (multiple clients, one engine stream)" but this was not actually implemented.

## Solution: StreamBroadcaster

Implemented a true broadcast multiplexing pattern where:

1. **Single upstream connection**: Only one HTTP request is made to the AceStream engine
2. **Broadcast to all clients**: Data is broadcast to all connected clients via asyncio queues
3. **Ring buffer**: Recent chunks (~6.4MB) are buffered so late-joining clients get immediate data
4. **First-chunk synchronization**: Clients wait for the first chunk to be available before streaming begins

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   StreamSession                          │
│  - One per unique ace_id                                │
│  - Manages session lifecycle                            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ↓
         ┌───────────────────────┐
         │   StreamBroadcaster   │
         │                       │
         │  ┌─────────────────┐  │
         │  │ Upstream Stream │  │  ← Single HTTP connection to AceStream
         │  └────────┬────────┘  │
         │           │           │
         │      ┌────▼────┐      │
         │      │  Buffer │      │  ← Ring buffer (100 chunks)
         │      └────┬────┘      │
         │           │           │
         │    ┌──────▼──────┐    │
         │    │  Broadcast  │    │
         │    └──┬──┬──┬────┘    │
         └───────┼──┼──┼─────────┘
                 │  │  │
         ┌───────▼──▼──▼─────────┐
         │   Client Queues       │
         │  ┌─────┐ ┌─────┐ ┌───┐│
         │  │Que 1│ │Que 2│ │...││  ← One queue per client
         │  └──┬──┘ └──┬──┘ └─┬─┘│
         └─────┼──────┼──────┼───┘
               ↓      ↓      ↓
           Client1 Client2 Client3
```

## Implementation Details

### Key Files Changed

1. **`app/services/proxy/broadcaster.py`** (NEW)
   - StreamBroadcaster class
   - Background task fetches from AceStream once
   - Broadcasts chunks to all client queues
   - Ring buffer for late joiners
   - Handles slow client removal

2. **`app/services/proxy/stream_session.py`** (MODIFIED)
   - Added `broadcaster` instance
   - `initialize()` creates and starts broadcaster
   - `stream_data()` now gets data from broadcaster queue
   - Proper cleanup of broadcaster resources

3. **`tests/test_proxy_broadcaster.py`** (NEW)
   - 4 comprehensive tests
   - Validates multiple client scenarios
   - Tests late-joining behavior
   - Verifies first-chunk synchronization

### Key Features

**Ring Buffer**: 
- Stores last 100 chunks (~6.4MB with 64KB chunks)
- Late-joining clients immediately get buffered data
- Prevents "cold start" delays for subsequent clients

**First Chunk Event**:
- `wait_for_first_chunk()` ensures data is available
- Clients wait up to 30s for initial buffering
- Prevents streaming empty data

**Slow Client Handling**:
- Each client has a queue (max 50 chunks ~3.2MB)
- If queue fills (client too slow), client is removed
- Prevents one slow client from blocking others

**Resource Cleanup**:
- Broadcaster stops when no clients remain
- Proper HTTP client closure
- Async-safe queue management

## Testing

### Unit Tests
All 15 proxy tests pass:
- 4 new broadcaster-specific tests
- 11 existing proxy tests (engine selector, client manager)

### Integration Test Results
```
First client:  Received 3 chunks in 0.151s (after initial buffering)
Second client: Received 3 chunks in 0.000s (immediately from buffer)
```

This confirms:
- ✅ First connection receives data (after buffering)
- ✅ Subsequent connections get instant data from buffer
- ✅ No reconnection needed for streaming to work

## Performance Impact

**Memory**: ~10MB per active stream (6.4MB ring buffer + 3.2MB per client queue)

**CPU**: Minimal - single async loop broadcasts to all clients

**Network**: Optimal - only one connection to AceStream engine per stream

**Latency**: 
- First client: ~100-150ms (initial buffering)
- Subsequent clients: <1ms (from buffer)

## Migration Notes

**No API Changes**: The `/ace/getstream` endpoint remains unchanged

**No Config Changes**: Existing configuration works as-is

**Backward Compatible**: Single-client scenarios work identically

## Future Enhancements

1. **Configurable buffer size**: Allow tuning based on available memory
2. **Buffer metrics**: Expose buffer utilization in `/proxy/status`
3. **Adaptive buffering**: Adjust buffer size based on stream bitrate
4. **HLS support**: Extend broadcaster pattern to HLS segments

## Conclusion

This fix implements **true multiplexing** as originally intended. The issue where "player receives nothing on first connection but works after reconnecting" is now resolved. Both first and subsequent connections receive data properly, with subsequent connections benefiting from the buffered data for instant playback.
