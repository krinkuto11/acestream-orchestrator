# VPN Exit Recovery Fix - Summary

## Problem Statement
When a Gluetun VPN container is stopped, the AceStream orchestrator experiences three critical issues:

1. **Docker Socket Timeouts**: 15-second timeouts when communicating with Docker socket
2. **Premature State Cleanup**: All engines incorrectly marked as "orphaned" and removed from state
3. **VPN Engine Imbalance**: After recovery, engines not properly distributed across VPN containers

## Root Causes

### 1. Insufficient Docker Socket Timeout
- Default timeout: 15 seconds
- VPN container stop operations can take longer than 15s
- Caused cascading failures across monitor, health checks, and validator

### 2. Aggressive State Cleanup
- Replica validator couldn't distinguish between:
  - Temporary Docker socket unavailability
  - Actual container removal
- Result: All engines removed as "orphaned" during transient issues

### 3. Missing VPN Assignment Restoration
- VPN container assignment stored in container labels
- Reindex didn't restore `vpn_container` from labels
- Round-robin assignment saw all engines as unassigned
- Result: All new engines assigned to first healthy VPN

## Solutions Implemented

### 1. Docker Socket Timeout Increase
**File**: `app/services/docker_client.py`

```python
def get_client(timeout: int = 30):  # Increased from 15s
```

- Doubled timeout from 15s to 30s
- Made timeout configurable via parameter
- Applied to all critical VPN operations

### 2. Docker Socket Retry Logic
**File**: `app/services/replica_validator.py`

```python
max_retries = 3
retry_delay = 1  # 1s, 2s, 4s with exponential backoff
```

- Added retry logic with exponential backoff
- Detect Docker unavailability with `docker_available` flag
- Skip state cleanup when Docker temporarily unreachable
- Cache state estimates for consistency

### 3. VPN Assignment Restoration
**File**: `app/services/reindex.py`

```python
# Extract VPN container from labels
vpn_container = lbl.get("acestream.vpn_container")

# Restore VPN assignment in state
if vpn_container:
    state.set_engine_vpn(key, vpn_container)
```

- Read `acestream.vpn_container` label during reindex
- Restore VPN assignment in state
- Use specific VPN container for host in redundant mode
- Properly track per-VPN forwarded engines

### 4. Monitor Error Handling
**File**: `app/services/monitor.py`

```python
try:
    current_containers = list_managed()
except Exception as e:
    logger.warning(f"Docker socket temporarily unavailable...")
    return  # Skip this iteration, retry next time
```

- Graceful handling of Docker socket errors
- Skip sync iteration when Docker unavailable
- Prevent cascade failures

### 5. Consistent Timeout Usage
**File**: `app/services/gluetun.py`

- Applied 30s timeout to:
  - VPN health checks
  - Force restart operations
  - VPN status queries

## Testing

### Code Validation
All validations passed:
- ✅ VPN container label extraction
- ✅ VPN assignment in EngineState
- ✅ VPN assignment restoration
- ✅ Per-VPN forwarded engine check
- ✅ Docker unavailability warning
- ✅ Retry logic message
- ✅ Docker availability tracking
- ✅ Retry logic for Docker operations
- ✅ Skip cleanup on Docker unavailable
- ✅ Default timeout increased to 30s
- ✅ Configurable timeout parameter

### Test File
Created `tests/test_vpn_exit_recovery.py` with comprehensive validation

## Expected Behavior Changes

### Before Fix
```
VPN Container Stop
    ↓
Docker Socket Timeout (15s)
    ↓
All Engines Marked "Orphaned"
    ↓
State Cleared (all engines removed)
    ↓
VPN Recovers
    ↓
New Engines Provisioned
    ↓
All Assigned to VPN1 (VPN2 has 0)
    ↓
Result: 6/4 Imbalance
```

### After Fix
```
VPN Container Stop
    ↓
Docker Socket Timeout (30s) + 3 Retries
    ↓
Docker Unavailable Detected
    ↓
State Preserved (skip cleanup)
    ↓
Use Cached State
    ↓
VPN Recovers
    ↓
Reindex Restores VPN Assignments from Labels
    ↓
Round-Robin Sees Actual Counts: VPN1=5, VPN2=5
    ↓
New Engines Distributed Evenly
    ↓
Result: 5/5 Balance Maintained
```

## Files Modified

1. `app/services/docker_client.py` - Timeout increase
2. `app/services/replica_validator.py` - Retry logic and state preservation
3. `app/services/reindex.py` - VPN assignment restoration
4. `app/services/monitor.py` - Error handling
5. `app/services/gluetun.py` - Consistent timeout usage
6. `tests/test_vpn_exit_recovery.py` - Validation tests (new)

## Impact

### Positive
- ✅ No "orphaned engine" removals during transient Docker issues
- ✅ State preserved during VPN container lifecycle events
- ✅ Proper VPN assignment restoration after recovery
- ✅ Balanced engine distribution across VPNs
- ✅ More resilient to Docker socket timeouts

### Neutral
- Slightly longer timeout (15s → 30s) for actual failures
- Minor increase in retry attempts (0 → 3) adds ~7s max delay

### No Breaking Changes
- All changes are backwards compatible
- No API changes
- No configuration changes required
- Existing functionality unchanged

## Deployment Notes

### Requirements
- No new dependencies
- No configuration changes needed
- No database migrations

### Rollback
- Simple git revert if needed
- No state cleanup required

### Monitoring
Watch for these log messages indicating the fix is working:
- "Docker socket temporarily unavailable - skipping state synchronization"
- "Restored VPN assignment for engine"
- "Using cached forwarded port"

## Future Improvements (Out of Scope)

1. Make retry configuration (max_retries, delays) configurable via environment variables
2. Add Prometheus metrics for Docker socket failures and retries
3. Extract shared VPN mode checking logic into utility functions
4. Add integration tests with actual VPN container stop/start scenarios
