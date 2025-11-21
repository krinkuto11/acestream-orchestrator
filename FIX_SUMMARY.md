# VPN Port Change Recovery Fix - Summary

## Problem Statement

The degradation.log showed an engine distribution imbalance (4-2) after VPN recovery, with gluetun having 4 engines and gluetun_2 having only 2 engines. Additionally, gluetun had no forwarded engine.

## Root Cause Analysis

By analyzing the degradation.log, we discovered the following sequence:

1. **Port Change Event** (10:49:21): gluetun_2's forwarded port changed from 36783 to 61697
2. **Forwarded Engine Removed**: Old forwarded engine (223ba7f4fcb6) was stopped and removed
3. **Health Manager Triggered**: Detected deficit (3 healthy engines, need 6) and provisioned 3 replacement engines
4. **Initial Recovery Success**: Engines were provisioned correctly with proper distribution (VPN1: 4 engines, VPN2: 4 engines)
5. **Premature Cleanup** (10:50:13-27): ~20 seconds after port recovery, 3 engines were removed by autoscaler/monitor
   - Engine 56b618316101 removed
   - Engine 3e94940e3a5a removed
   - Engine 2ff3b2bda469 removed
6. **Final Imbalance**: System ended with 4 engines on gluetun and 2 on gluetun_2

### Why This Happened

The issue was in `app/services/gluetun.py` in the `_handle_port_change()` method:

- When a VPN forwarded port changes, the old forwarded engine is removed
- The autoscaler provisions replacement engines
- **BUT** the recovery stabilization period was NOT established

The recovery stabilization period (120 seconds) is only set during VPN health transitions (unhealthy → healthy) at line 482. During a port change, the VPN stays "healthy" throughout, so no recovery time is set.

Without a recovery stabilization period:
- The monitor's `_cleanup_empty_engines()` proceeds normally
- Engines that became temporarily unhealthy during the port change are removed
- This creates an engine distribution imbalance

## Solution

Modified `_handle_port_change()` in `app/services/gluetun.py` to set `_last_recovery_time` on the VPN monitor after handling a port change. This triggers the same 120-second stabilization period that's used after VPN recovery.

### Code Changes

```python
# Set recovery stabilization period to prevent premature cleanup during recovery
# This prevents the monitor from cleaning up engines that may be temporarily
# unhealthy during the port change and subsequent reprovisioning
monitor = self._vpn_monitors.get(container_name)
if monitor:
    monitor._last_recovery_time = now
    logger.info(f"Recovery stabilization period set for VPN '{container_name}' after port change "
               f"({monitor._recovery_stabilization_period_s}s)")
```

### Impact

After this fix:
1. When a VPN port changes, a 120-second stabilization period is established
2. During this period, the monitor skips empty engine cleanup
3. This prevents premature removal of engines that are temporarily unhealthy
4. Engine distribution remains balanced after recovery
5. No more 4-2 imbalances after VPN port changes

## Testing

### New Tests Created

Created `tests/test_port_change_stabilization.py` with 3 test cases:

1. **test_port_change_sets_recovery_stabilization**: Validates that port change sets recovery stabilization period on the VPN monitor
2. **test_monitor_respects_recovery_stabilization**: Validates that monitor respects the stabilization period timing (immediate, 60s, 130s)
3. **test_redundant_mode_port_change_stabilization**: Validates that redundant VPN mode handles stabilization correctly per VPN

All tests pass successfully.

### Existing Tests

Ran full test suite (excluding integration tests):
- 261 tests selected
- 253 tests passed
- 8 tests failed (pre-existing, unrelated to this change)
- No regressions introduced

### Security Analysis

- **Code Review**: 3 minor suggestions for test improvements (not blocking)
- **CodeQL Security Scan**: 0 vulnerabilities found

## Verification

The fix can be verified by:

1. Checking logs for "Recovery stabilization period set for VPN" message after port changes
2. Confirming monitor skips cleanup with "Skipping empty engine cleanup - VPN recently recovered" message
3. Verifying engine distribution remains balanced after VPN port changes
4. Running the new test suite: `python tests/test_port_change_stabilization.py`

## Related Files

- **Modified**: `app/services/gluetun.py` - Added recovery stabilization period after port change
- **New**: `tests/test_port_change_stabilization.py` - Comprehensive tests for the fix
- **Reference**: `app/services/monitor.py` - Contains cleanup logic that respects stabilization period
- **Reference**: `app/services/health_manager.py` - Contains provisioning logic that respects stabilization period
