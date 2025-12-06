# Summary of Changes: Remove Acexy Bidirectional Communication

## Overview
The acexy proxy has changed to a stateless model that only sends "stream started" events to the orchestrator. This PR removes all bidirectional communication with acexy and strengthens stale stream detection via stat URL checking.

## Key Changes

### 1. Removed Acexy Sync Service (app/services/acexy.py)
- **Removed**: `AcexyClient` class and all `/ace/streams` endpoint communication
- **Deprecated**: `AcexySyncService` - now a no-op placeholder for backwards compatibility
- **Result**: No more bidirectional polling of acexy's `/ace/streams` endpoint
- **Backwards Compatibility**: Service still exists but does nothing; `/acexy/status` endpoint returns deprecated status

### 2. Removed Acexy Configuration (app/core/config.py)
- **Removed**: `ACEXY_ENABLED` config option
- **Removed**: `ACEXY_URL` config option  
- **Removed**: `ACEXY_SYNC_INTERVAL_S` config option
- **Removed**: All acexy-related config validators

### 3. Strengthened Stat URL Checking (app/services/collector.py)
- **Changed**: `COLLECT_INTERVAL_S` default from 5 seconds to 2 seconds
- **Result**: 2.5x faster stale stream detection
- **Updated**: Documentation to emphasize collector as PRIMARY stale stream detector
- **Impact**: Stale streams now detected in ~2s instead of ~5s

### 4. Updated Configuration Files
- **.env.example**: Removed acexy config, updated COLLECT_INTERVAL_S to 2
- **All values**: Changed from 5s to 2s across the board

### 5. Updated Documentation
- **CONFIG.md**: Removed acexy section, updated COLLECT_INTERVAL_S description
- **API.md**: Marked `/acexy/status` as DEPRECATED, added note about stateless proxy
- **HEALTH_MONITORING.md**: Emphasized stat URL checking as PRIMARY mechanism, noted 5s→2s improvement
- **ARCHITECTURE.md**: Updated collector description to highlight PRIMARY role
- **DEPLOY.md**: Updated COLLECT_INTERVAL_S example to 2s with comment

### 6. Updated Tests
- **test_acexy_integration.py**: Now tests deprecated service status (no longer tests sync functionality)
- **test_stale_stream_detection.py**: Unchanged, still passing (verifies stat URL detection)
- **test_stat_url_checking_frequency.py**: New comprehensive test verifying all changes

## Technical Impact

### Before This Change
- **Stream State Management**: Dual mechanism
  1. Acexy sync service polled `/ace/streams` every 30s to find stale streams
  2. Collector polled stat URLs every 5s to detect stale streams
- **Detection Speed**: 5-30 seconds depending on which mechanism caught it first
- **Bidirectional Communication**: Yes - orchestrator called acexy

### After This Change
- **Stream State Management**: Single mechanism
  1. Collector polls stat URLs every 2s (PRIMARY and ONLY mechanism)
- **Detection Speed**: ~2 seconds
- **Bidirectional Communication**: No - acexy only sends start events
- **Performance**: 2.5x faster detection (2s vs 5s)

## Migration Guide

### For Existing Deployments
1. **No action required** - changes are backwards compatible
2. **Optional**: Remove acexy environment variables from `.env` (they're ignored now)
3. **Optional**: Update `COLLECT_INTERVAL_S=2` in `.env` (will use new default if not set)

### For New Deployments
1. Do not set `ACEXY_ENABLED`, `ACEXY_URL`, or `ACEXY_SYNC_INTERVAL_S` (they don't exist)
2. `COLLECT_INTERVAL_S=2` is the new default (can be customized if needed)
3. Acexy proxy should be configured to only send `stream_started` events

## Testing

All tests passing:
- ✅ `test_stale_stream_detection.py` - Verifies stat URL checking works
- ✅ `test_acexy_integration.py` - Verifies acexy service is deprecated
- ✅ `test_stat_url_checking_frequency.py` - Verifies new behavior and frequency
- ✅ CodeQL security scan - 0 alerts
- ✅ Code review - All feedback addressed

## Benefits

1. **Simpler Architecture**: Single mechanism for stream state management
2. **Faster Detection**: 2.5x faster stale stream detection (2s vs 5s)
3. **Less Network Traffic**: No polling of acexy's `/ace/streams` endpoint
4. **Clearer Responsibility**: Collector is clearly the PRIMARY mechanism
5. **Backwards Compatible**: Existing deployments continue to work
6. **Stateless Acexy**: Acexy proxy can be truly stateless now

## Verification

To verify the changes in your deployment:

```bash
# Check that COLLECT_INTERVAL_S is 2 seconds
curl http://localhost:8000/orchestrator/status | jq '.config'

# Check that acexy is deprecated
curl http://localhost:8000/acexy/status | jq '.'
# Should return: {"enabled": false, "deprecated": true, ...}

# Monitor stale stream detection
curl http://localhost:8000/metrics | grep orch_stale_streams_detected_total
```

## Security

- No security vulnerabilities introduced (CodeQL scan clean)
- No new external dependencies added
- Removed code reduces attack surface
- Stat URL checking still validates responses properly
