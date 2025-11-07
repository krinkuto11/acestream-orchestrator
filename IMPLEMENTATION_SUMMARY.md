# VPN Port Forwarding Race Condition Fix

## Summary
Fixed a race condition where engines were provisioned immediately after VPN emergency mode exit, before the VPN forwarded port was established. This resulted in all engines being provisioned without port forwarding.

## Problem Description
From the vpn_exit.log file, the issue manifested as:
1. **00:23:06.436** - VPN exits emergency mode
2. **00:23:07.280** - Health manager provisions 5 engines (less than 1 second later)
3. **00:23:07.280-00:23:08.695** - All 5 engines provisioned without forwarded port
4. **00:23:19.348** - Forwarded port finally available (~13 seconds too late)

## Root Cause
Two independent systems working simultaneously:
- **VPN Recovery Handler** (`gluetun.py`): Exits emergency mode, waits for port, then provisions engines
- **Health Manager** (`health_manager.py`): Runs every 20s, sees we have fewer engines than MIN_REPLICAS, provisions immediately

The health manager didn't know to wait for the VPN recovery handler to establish port forwarding first.

## Solution
Added a check in the health manager's `_should_wait_for_vpn_recovery()` method to detect when a VPN is in its recovery stabilization period (120 seconds after recovery). During this grace period, the health manager waits, allowing the VPN recovery handler to:
1. Wait for forwarded port establishment (up to 30s)
2. Provision engines with correct port forwarding
3. Complete recovery process

## Implementation Details

### Changes to `app/services/health_manager.py`
Added VPN recovery stabilization period check:

```python
# Check if any VPN is in recovery stabilization period
# This ensures we wait for forwarded port to be established before provisioning
vpn1_monitor = gluetun_monitor.get_vpn_monitor(cfg.GLUETUN_CONTAINER_NAME)
vpn2_monitor = gluetun_monitor.get_vpn_monitor(cfg.GLUETUN_CONTAINER_NAME_2)

if vpn1_monitor and vpn1_monitor.is_in_recovery_stabilization_period():
    logger.info(f"VPN '{cfg.GLUETUN_CONTAINER_NAME}' is in recovery stabilization period. "
               f"Not taking action - waiting for port forwarding to stabilize.")
    return True
```

### Existing Infrastructure Used
The fix leverages existing VPN monitor functionality:
- `_last_recovery_time`: Timestamp when VPN recovered (already tracked)
- `_recovery_stabilization_period_s`: Grace period duration (120s, already configured)
- `is_in_recovery_stabilization_period()`: Method to check if in grace period (already implemented)

No changes to VPN recovery handler were needed!

## Testing

### Automated Tests (`test_vpn_recovery_stabilization.py`)
- ✅ Health manager waits when VPN is in stabilization period
- ✅ VPN monitor correctly tracks recovery period
- ✅ Integration test validates complete scenario

### Manual Verification (`manual_verify_vpn_fix.py`)
- ✅ Demonstrates timing comparison before/after fix
- ✅ Shows expected behavior step-by-step

### Security
- ✅ CodeQL analysis: No security issues found

## Expected Behavior After Fix

```
00:23:06.436 - Emergency mode exits, VPN recovery time recorded
00:23:07.280 - Health manager: "VPN in stabilization period - waiting"
00:23:11.436 - VPN recovery handler starts waiting for port
00:23:19.348 - Port forwarding established
00:23:19.500 - VPN recovery handler provisions engines WITH port forwarding ✅
00:25:06.436 - Stabilization period ends, health manager resumes
```

## Impact
- **Minimal code change**: Only 22 lines modified in one file
- **Surgical fix**: Uses existing infrastructure, no architectural changes
- **Backward compatible**: Doesn't affect single VPN mode or normal operations
- **Well-tested**: 3 automated tests + manual verification

## Files Changed
1. `app/services/health_manager.py` - Added stabilization period check
2. `tests/test_vpn_recovery_stabilization.py` - Comprehensive test suite
3. `tests/manual_verify_vpn_fix.py` - Manual verification script
