# Emergency Mode Implementation Summary

## Overview
This implementation adds an automatic emergency mode to handle VPN failures gracefully in redundant VPN configurations.

## Problem Analysis
Analyzed the `vpn_exit.log` file which demonstrated incomplete VPN recovery:

**Key Issues Identified:**
1. Line 72-83: VPN 'gluetun_2' becomes unhealthy with 5 engines
2. Lines 84, 94, 104: Health manager logs "Not taking action - waiting for VPN recovery"
3. Lines 109-128: After VPN restart, system assigns NEW engines to BOTH VPNs (including the just-recovered one)
4. Lines 139-169: System tries to replace "unhealthy" engines that are unreachable
5. Lines 170-196: Eventually all engines are cleaned up as empty (wrong approach)

**Root Cause:**
The system lacked coordination when one VPN failed - services didn't know to:
- Stop managing failed VPN's engines
- Only operate on healthy VPN
- Clean up failed VPN state immediately
- Wait for proper recovery before restoring operations

## Solution: Emergency Mode

### What Was Implemented

#### 1. State Management (`app/services/state.py`)
Added emergency mode state tracking:
```python
- _emergency_mode: bool
- _failed_vpn_container: str
- _healthy_vpn_container: str
- _emergency_mode_entered_at: datetime
```

**Methods Added:**
- `enter_emergency_mode(failed_vpn, healthy_vpn)` - Activates emergency mode, removes failed VPN engines
- `exit_emergency_mode()` - Deactivates emergency mode
- `is_emergency_mode()` - Check if in emergency mode
- `get_emergency_mode_info()` - Get status details
- `should_skip_vpn_operations(vpn)` - Check if VPN operations should be skipped

#### 2. VPN Health Monitoring (`app/services/gluetun.py`)
Enhanced health transition handling:

**Added:**
- `_handle_vpn_failure(failed_vpn)` - Enters emergency mode when VPN fails
- `_handle_vpn_recovery(recovered_vpn)` - Exits emergency mode and provisions engines
- Modified `_handle_health_transition()` - Detects failures and recoveries

**Updated:**
- `get_vpn_status()` - Now includes emergency_mode info in API response

#### 3. Health Manager Integration (`app/services/health_manager.py`)
**Modified:**
- `_check_and_manage_health()` - Skips operations when in emergency mode

#### 4. Autoscaler Integration (`app/services/autoscaler.py`)
**Modified:**
- `ensure_minimum()` - Pauses autoscaling when in emergency mode (except initial startup)

#### 5. Provisioner Integration (`app/services/provisioner.py`)
**Modified:**
- `start_acestream()` - In emergency mode, only assigns engines to healthy VPN

### How It Works

**Normal Operation:**
```
VPN1 (gluetun):    ✅ [Engine1] [Engine2] [Engine3] [Engine4] [Engine5]
VPN2 (gluetun_2):  ✅ [Engine6] [Engine7] [Engine8] [Engine9] [Engine10]
Emergency Mode: ❌ Inactive
```

**VPN Failure Detected:**
```
1. VPN health monitor detects gluetun_2 became unhealthy
2. Calls _handle_vpn_failure('gluetun_2')
3. Enters emergency mode via state.enter_emergency_mode()
4. Immediately stops and removes Engine6-10
5. Logs: "⚠️ ENTERING EMERGENCY MODE ⚠️"
```

**Emergency Mode Active:**
```
VPN1 (gluetun):    ✅ [Engine1] [Engine2] [Engine3] [Engine4] [Engine5]
VPN2 (gluetun_2):  ❌ FAILED
Emergency Mode: ✅ Active
Actions:
- Health manager: Paused (logs "Health manager paused: in emergency mode")
- Autoscaler: Paused (logs "Autoscaler paused: in emergency mode")
- Provisioner: Only assigns to VPN1
- System: Happy with 5 engines instead of 10
```

**VPN Recovery Detected:**
```
1. VPN health monitor detects gluetun_2 became healthy
2. Calls _handle_vpn_recovery('gluetun_2')
3. Exits emergency mode via state.exit_emergency_mode()
4. Waits 5 seconds for VPN stabilization
5. Provisions engines to restore MIN_REPLICAS (10 total)
6. Logs: "✅ EXITING EMERGENCY MODE ✅"
```

**Back to Normal:**
```
VPN1 (gluetun):    ✅ [Engine1] [Engine2] [Engine3] [Engine4] [Engine5]
VPN2 (gluetun_2):  ✅ [Engine11] [Engine12] [Engine13] [Engine14] [Engine15]
Emergency Mode: ❌ Inactive
```

## Technical Details

### Entry Conditions
Emergency mode activates when:
1. VPN_MODE == 'redundant'
2. Both GLUETUN_CONTAINER_NAME and GLUETUN_CONTAINER_NAME_2 are configured
3. One VPN becomes unhealthy while the other remains healthy

### Exit Conditions
Emergency mode deactivates when:
1. The failed VPN becomes healthy again
2. System waits 5 seconds for stabilization
3. Provisions engines to restore MIN_REPLICAS
4. Resumes normal health_manager and autoscaler operations

### State Transitions
```
NORMAL → [VPN Failure] → EMERGENCY → [VPN Recovery] → NORMAL
```

## Testing

### Test Suite (`tests/test_emergency_mode.py`)
**Test Classes:**
1. `TestEmergencyModeState` - State management tests
   - Initial state
   - Enter/exit emergency mode
   - VPN operation skip checks

2. `TestEmergencyModeIntegration` - Service integration tests
   - Health manager respects emergency mode
   - Autoscaler respects emergency mode

3. `TestEmergencyModeProvisioning` - Provisioner tests
   - Only assigns to healthy VPN in emergency mode

### Running Tests
```bash
python -m pytest tests/test_emergency_mode.py -v
```

## API Integration

### Endpoint: `/vpn/status`
Now includes emergency mode information:

**Request:**
```bash
curl http://localhost:8000/vpn/status
```

**Response:**
```json
{
  "mode": "redundant",
  "enabled": true,
  "status": "running",
  "health": "healthy",
  "vpn1": {
    "enabled": true,
    "status": "running",
    "health": "healthy",
    "container_name": "gluetun",
    "forwarded_port": 62981
  },
  "vpn2": {
    "enabled": true,
    "status": "running",
    "health": "healthy",
    "container_name": "gluetun_2",
    "forwarded_port": 53344
  },
  "emergency_mode": {
    "active": true,
    "failed_vpn": "gluetun_2",
    "healthy_vpn": "gluetun",
    "duration_seconds": 125.3,
    "entered_at": "2025-11-07T22:03:33.157000+00:00"
  }
}
```

## Log Messages

### Emergency Mode Entry
```
2025-11-07 22:03:33 WARNING ⚠️  ENTERING EMERGENCY MODE ⚠️
2025-11-07 22:03:33 WARNING Failed VPN: gluetun_2
2025-11-07 22:03:33 WARNING Healthy VPN: gluetun
2025-11-07 22:03:33 WARNING System will operate with reduced capacity on single VPN until recovery
2025-11-07 22:03:33 WARNING Removing 5 engines from failed VPN 'gluetun_2'
2025-11-07 22:03:33 INFO Stopped engine 72126a24d5b1 from failed VPN
2025-11-07 22:03:33 INFO Stopped engine bfecd003fccf from failed VPN
2025-11-07 22:03:33 INFO Stopped engine 1eb9e5af2919 from failed VPN
2025-11-07 22:03:33 INFO Stopped engine 6100b2d270af from failed VPN
2025-11-07 22:03:33 INFO Stopped engine a21aa2f4c84c from failed VPN
2025-11-07 22:03:33 WARNING Emergency mode active: operating with 5 engines on 'gluetun'
```

### During Emergency Mode
```
2025-11-07 22:03:43 DEBUG Health manager paused: in emergency mode (failed VPN: gluetun_2)
2025-11-07 22:03:53 DEBUG Autoscaler paused: in emergency mode (failed VPN: gluetun_2)
2025-11-07 22:04:03 INFO Emergency mode active: assigning engine to healthy VPN 'gluetun'
```

### Emergency Mode Exit
```
2025-11-07 22:05:55 INFO ✅ EXITING EMERGENCY MODE ✅
2025-11-07 22:05:55 INFO VPN 'gluetun_2' has recovered after 125.3s
2025-11-07 22:05:55 INFO System will restore full capacity and resume normal operations
2025-11-07 22:06:00 INFO VPN 'gluetun_2' recovered - provisioning 5 engines to restore capacity (5/10)
2025-11-07 22:06:01 INFO Provisioning recovery engine 1/5
2025-11-07 22:06:01 INFO Successfully provisioned recovery engine ba2771e8e6c8
2025-11-07 22:06:02 INFO Provisioning recovery engine 2/5
2025-11-07 22:06:02 INFO Successfully provisioned recovery engine fc68522982cb
...
2025-11-07 22:06:10 INFO VPN recovery provisioning complete - successfully provisioned 5/5 engines (failed: 0)
```

## Configuration

**No configuration required!** Emergency mode is automatic when:
```bash
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_CONTAINER_NAME_2=gluetun_2
```

## Benefits

1. **Automatic** - No manual intervention needed
2. **Fast** - Immediate response to VPN failure
3. **Clean** - Properly removes failed VPN's engines
4. **Resilient** - System continues operating on single VPN
5. **Self-Healing** - Automatic recovery when VPN returns
6. **Visible** - Clear logging and API status
7. **Safe** - No race conditions or state corruption

## Limitations

1. **Redundant Mode Only** - Single VPN mode doesn't need emergency mode
2. **One Failure at a Time** - Can't help if both VPNs fail
3. **Reduced Capacity** - Operates with fewer engines during emergency
4. **Requires Both VPNs** - Need both VPN containers configured

## Security

**CodeQL Scan:** ✅ Passed (0 alerts)
- No security vulnerabilities introduced
- No data exposure risks
- Thread-safe state management

## Documentation

**Created:**
- `docs/EMERGENCY_MODE.md` - Complete guide with diagrams and examples
- `tests/test_emergency_mode.py` - Comprehensive test suite
- This summary document

**Updated:**
- `README.md` - Added emergency mode to features and docs sections

## Files Changed

**Core Implementation (5 files):**
1. `app/services/state.py` - Emergency mode state management
2. `app/services/gluetun.py` - VPN failure/recovery handling
3. `app/services/health_manager.py` - Emergency mode integration
4. `app/services/autoscaler.py` - Emergency mode integration
5. `app/services/provisioner.py` - VPN assignment logic

**Testing & Docs (3 files):**
1. `tests/test_emergency_mode.py` - Test suite
2. `docs/EMERGENCY_MODE.md` - Documentation
3. `README.md` - Feature listing

**Total Changes:**
- ~400 lines of code added
- ~30 lines modified
- 0 lines removed
- 100% backwards compatible

## Future Enhancements

Potential improvements (not in scope):
1. Configurable emergency mode timeout
2. Metrics/alerts for emergency mode events
3. Dashboard visualization of emergency mode
4. Support for >2 VPN containers
5. Emergency mode for other failure types

## Conclusion

This implementation solves the VPN recovery problem identified in `vpn_exit.log` by:
1. ✅ Immediately cleaning up failed VPN's engines
2. ✅ Operating cleanly on single VPN
3. ✅ Coordinating all services (health, autoscaler, provisioner)
4. ✅ Automatically recovering when VPN returns
5. ✅ Providing clear visibility via API and logs

The solution is automatic, safe, and requires no configuration changes.
