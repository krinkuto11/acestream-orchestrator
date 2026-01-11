# HLS Proxy Optimization - Query Reduction Fix

## Problem Statement

The HLS proxy was making excessive queries to engines and generating significant log noise. From the user's logs:

```
orchestrator  | 2026-01-11 12:03:55,530 INFO app.main: Selected engine 92c7cab8bbf3 for HLS stream 00c9bc9c5d7d87680a5a6bed349edfa775a89947 (forwarded=True, current_load=0)
orchestrator  | 2026-01-11 12:03:55,530 INFO app.main: Client aefc96d7-485c-4480-9992-ea253b26166a connecting to HLS stream 00c9bc9c5d7d87680a5a6bed349edfa775a89947 from 100.124.31.118
orchestrator  | 2026-01-11 12:03:55,530 INFO app.main: HLS channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947 already exists, reusing existing session
orchestrator  | 2026-01-11 12:04:01,563 INFO app.main: Selected engine 92c7cab8bbf3 for HLS stream 00c9bc9c5d7d87680a5a6bed349edfa775a89947 (forwarded=True, current_load=0)
orchestrator  | 2026-01-11 12:04:01,563 INFO app.main: Client fd842ff3-19e5-43d1-b06b-f18a1a6e238a connecting to HLS stream 00c9bc9c5d7d87680a5a6bed349edfa775a89947 from 100.124.31.118
orchestrator  | 2026-01-11 12:04:01,563 INFO app.main: HLS channel 00c9bc9c5d7d87680a5a6bed349edfa775a89947 already exists, reusing existing session
```

Every ~6 seconds, the same pattern repeated:
1. Engine selection logic ran
2. "Selected engine" logged
3. "Client connecting" logged
4. "HLS channel already exists, reusing existing session" logged

This was happening on **every manifest request**, even though the HLS channel already existed.

## Root Cause

The `/ace/getstream` endpoint was structured incorrectly for HLS mode:

```python
# OLD FLOW (BEFORE FIX)
1. Run engine selection (always)
2. Log "Selected engine..." (always)
3. Log "Client connecting..." (always)
4. Check if HLS channel exists
5. If exists: Skip channel creation, log "already exists"
6. If not exists: Create channel
7. Return manifest
```

This meant that **every manifest/segment request** (which happens every few seconds in HLS) would:
- Run the full engine selection algorithm
- Query active streams from the database
- Sort and filter engines
- Generate INFO-level log messages
- Only then discover the channel already exists and skip creation

## Comparison with TS Proxy

The TS proxy doesn't have this problem because:
- TS uses persistent connections (one connection per client)
- `/ace/getstream` is called **once per client**
- Engine selection happens only once when the connection is established

HLS, by design, uses multiple HTTP requests:
- Initial manifest request
- Periodic manifest refresh (every ~6 seconds)
- Multiple segment requests
- Each request goes through `/ace/getstream`

## Solution

Reorganize the HLS handling to check for channel existence **before** engine selection:

```python
# NEW FLOW (AFTER FIX)
1. Check if HLS channel exists
2. If exists:
   a. Log debug message (not INFO)
   b. Skip engine selection entirely
   c. Return manifest immediately
3. If not exists:
   a. Run engine selection
   b. Log "Selected engine for NEW stream..."
   c. Create channel
   d. Return manifest
```

## Implementation

### Key Changes in `/ace/getstream`

1. **Early channel check for HLS**:
   ```python
   if hls_proxy.has_channel(id):
       logger.debug(f"HLS channel {id} already exists, serving manifest...")
       # Skip engine selection, just serve manifest
       return StreamingResponse(...)
   ```

2. **Engine selection only for new channels**:
   ```python
   # Only reach here if channel doesn't exist
   engines = state.list_engines()
   # ... engine selection logic ...
   selected_engine = engines_sorted[0]
   logger.info(f"Selected engine for NEW HLS stream {id}...")
   ```

3. **Updated log messages** to distinguish:
   - New stream: "Selected engine for **new** HLS stream..."
   - New stream: "Client **initializing new** HLS stream..."
   - Existing channel: DEBUG level "HLS channel already exists, serving manifest..."

### Files Changed

- `app/main.py`: Reorganized `/ace/getstream` endpoint HLS handling (177 lines changed)

## Impact

### Log Reduction

**Before**: For a typical 30-second HLS session (5 manifest requests)
```
Request #1: 5 INFO messages (channel creation)
Request #2: 3 INFO messages (channel reuse)
Request #3: 3 INFO messages (channel reuse)
Request #4: 3 INFO messages (channel reuse)
Request #5: 3 INFO messages (channel reuse)
Total: 17 INFO messages
```

**After**: For the same 30-second session
```
Request #1: 5 INFO messages (channel creation)
Request #2: 1 DEBUG message (channel reuse)
Request #3: 1 DEBUG message (channel reuse)
Request #4: 1 DEBUG message (channel reuse)
Request #5: 1 DEBUG message (channel reuse)
Total: 5 INFO messages, 4 DEBUG messages
```

**Improvement**: ~71% reduction in INFO log noise (17 → 5)

### Performance

**Before**:
- Engine selection: 5 times (once per request)
- Database queries: 5 times
- List sorting: 5 times

**After**:
- Engine selection: 1 time (first request only)
- Database queries: 1 time
- List sorting: 1 time

**Improvement**: 80% reduction in computation overhead

## Testing

All existing HLS tests pass:
- `test_hls_playback_url_update.py`: Validates channel reuse via `has_channel()`
- Tests confirm channels are not recreated when they already exist
- Thread safety of `has_channel()` verified

## Backward Compatibility

✅ **Fully backward compatible**:
- No API changes
- No configuration changes
- No breaking changes to HLS proxy behavior
- Only changes are internal optimization and log levels

## Migration

✅ **No migration required**:
- Changes are transparent to users
- Existing HLS streams continue to work
- No restart needed (hot-deployed with orchestrator update)

## Expected User Experience

Users will observe:
1. **Cleaner logs**: Dramatically fewer "Selected engine" messages
2. **Better performance**: Faster manifest responses for existing channels
3. **Same functionality**: Streams work exactly as before
4. **Alignment with TS proxy**: HLS now follows the same "select once" pattern

## Verification

To verify the fix is working, check logs when a client plays an HLS stream:

**Expected pattern**:
```
[INFO] Selected engine abc123 for new HLS stream XYZ ...
[INFO] Client initializing new HLS stream XYZ ...
[INFO] Requesting HLS stream from engine ...
[INFO] HLS playback URL: ...
[DEBUG] HLS channel XYZ already exists, serving manifest ...
[DEBUG] HLS channel XYZ already exists, serving manifest ...
[DEBUG] HLS channel XYZ already exists, serving manifest ...
```

**Bad pattern (old behavior)**:
```
[INFO] Selected engine abc123 for HLS stream XYZ ...
[INFO] Client connecting to HLS stream XYZ ...
[INFO] HLS channel XYZ already exists, reusing existing session
[INFO] Selected engine abc123 for HLS stream XYZ ...
[INFO] Client connecting to HLS stream XYZ ...
[INFO] HLS channel XYZ already exists, reusing existing session
```

## References

- Issue: "ts-proxy works way more reliably, take a look at number of queries"
- Related documentation: `docs/HLS_PROXY.md`
- Test coverage: `tests/test_hls_playback_url_update.py`
