# Emergency Mode for Redundant VPN Failure

## Overview

Emergency mode is a special operational state that activates automatically when one VPN container fails in redundant VPN mode. It ensures continuous service availability by operating on the single healthy VPN until the failed VPN recovers.

## Problem Statement

In redundant VPN mode with two VPN containers (e.g., `gluetun` and `gluetun_2`), when one VPN fails, the system needed to:

1. **Immediately handle** engines on the failed VPN (they become unreachable)
2. **Stop provisioning** to the failed VPN
3. **Operate normally** with the healthy VPN
4. **Wait for recovery** without interfering with the failed VPN
5. **Restore capacity** automatically after recovery

Previously, the system would:
- Leave engines on failed VPN in an unhealthy state
- Continue trying to assign new engines to both VPNs
- Have health_manager and autoscaler confused by unhealthy engines
- Not properly clean up and reprovision after VPN recovery

## Emergency Mode Solution

### What Emergency Mode Does

When **one VPN fails** in redundant mode:

1. ⚠️  **Enters Emergency Mode Immediately**
   - Detects VPN failure through health monitoring
   - Identifies which VPN failed and which is healthy
   
2. 🗑️  **Cleans Up Failed VPN's Engines**
   - Stops all engine containers on the failed VPN
   - Removes them from state management
   - Prevents "ghost" unhealthy engines
   
3. 🎯 **Single-VPN Operation**
   - Only assigns new engines to the healthy VPN
   - Provisioner uses healthy VPN exclusively
   - Operates with reduced capacity (acceptable)
   
4. ⏸️  **Pauses Non-Essential Operations**
   - Health manager skips operations (no point trying to fix failed VPN's engines)
   - Autoscaler pauses (happy with reduced capacity)
   - System accepts reduced capacity until recovery
   
5. ✅ **Automatic Recovery**
   - Detects when failed VPN becomes healthy
   - Exits emergency mode
   - Provisions engines to restore full capacity
   - Resumes normal redundant operations

### State Transitions

```
┌─────────────────────────────────────────────────────┐
│           Normal Redundant Mode Operation           │
│  Both VPNs healthy, engines load-balanced across    │
│  both VPNs, all services operating normally         │
└──────────────────┬──────────────────────────────────┘
                   │
                   │ VPN Failure Detected
                   ▼
┌─────────────────────────────────────────────────────┐
│              ⚠️  EMERGENCY MODE ⚠️                   │
│  • Failed VPN's engines stopped and removed         │
│  • Only healthy VPN used for new engines            │
│  • Health manager paused                            │
│  • Autoscaler paused (accepts reduced capacity)     │
│  • System operates on single VPN                    │
└──────────────────┬──────────────────────────────────┘
                   │
                   │ VPN Recovery Detected
                   ▼
┌─────────────────────────────────────────────────────┐
│              Emergency Mode Exit                     │
│  • Provision engines to restore capacity            │
│  • Resume health manager operations                 │
│  • Resume autoscaler operations                     │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│           Normal Redundant Mode Operation           │
│  Back to normal - both VPNs operational             │
└─────────────────────────────────────────────────────┘
```

## Implementation Details

### State Management

Emergency mode state is managed in `app/services/state.py`:

```python
class State:
    def __init__(self):
        # Emergency mode state
        self._emergency_mode = False
        self._failed_vpn_container = None
        self._healthy_vpn_container = None
        self._emergency_mode_entered_at = None
    
    def enter_emergency_mode(self, failed_vpn: str, healthy_vpn: str) -> bool:
        """Enter emergency mode - removes failed VPN's engines."""
        
    def exit_emergency_mode(self) -> bool:
        """Exit emergency mode - triggers engine provisioning."""
        
    def is_emergency_mode(self) -> bool:
        """Check if in emergency mode."""
        
    def get_emergency_mode_info(self) -> Dict:
        """Get emergency mode status and details."""
```

### VPN Health Monitoring

Emergency mode is triggered in `app/services/gluetun.py`:

```python
async def _handle_health_transition(self, container_name: str, old_status: bool, new_status: bool):
    """Handle VPN health transitions."""
    if old_status and not new_status:
        # VPN became unhealthy
        if cfg.VPN_MODE == 'redundant':
            await self._handle_vpn_failure(container_name)
    
    elif not old_status and new_status:
        # VPN recovered
        if cfg.VPN_MODE == 'redundant':
            await self._handle_vpn_recovery(container_name)
```

### Service Integration

All services respect emergency mode:

**Health Manager** (`app/services/health_manager.py`):
```python
async def _check_and_manage_health(self):
    if state.is_emergency_mode():
        logger.debug("Health manager paused: in emergency mode")
        return
    # ... continue with health management
```

**Autoscaler** (`app/services/autoscaler.py`):
```python
def ensure_minimum(initial_startup: bool = False):
    if not initial_startup and state.is_emergency_mode():
        logger.debug("Autoscaler paused: in emergency mode")
        return
    # ... continue with autoscaling
```

**Provisioner** (`app/services/provisioner.py`):
```python
def start_acestream(req: AceProvisionRequest):
    if state.is_emergency_mode():
        emergency_info = state.get_emergency_mode_info()
        vpn_container = emergency_info['healthy_vpn']
        logger.info(f"Emergency mode: assigning to healthy VPN '{vpn_container}'")
    # ... continue with provisioning
```

## Monitoring Emergency Mode

### API Endpoint

Emergency mode status is included in the VPN status endpoint:

```bash
curl http://localhost:8000/vpn/status
```

Response includes `emergency_mode` field:
```json
{
  "mode": "redundant",
  "enabled": true,
  "status": "running",
  "health": "healthy",
  "vpn1": { ... },
  "vpn2": { ... },
  "emergency_mode": {
    "active": true,
    "failed_vpn": "gluetun_2",
    "healthy_vpn": "gluetun",
    "duration_seconds": 125.3,
    "entered_at": "2025-11-07T22:03:33.157000+00:00"
  }
}
```

### Log Messages

Emergency mode transitions are clearly logged:

**Entry:**
```
2025-11-07 22:03:33 WARNING ⚠️  ENTERING EMERGENCY MODE ⚠️
2025-11-07 22:03:33 WARNING Failed VPN: gluetun_2
2025-11-07 22:03:33 WARNING Healthy VPN: gluetun
2025-11-07 22:03:33 WARNING System will operate with reduced capacity on single VPN until recovery
2025-11-07 22:03:33 WARNING Removing 5 engines from failed VPN 'gluetun_2'
2025-11-07 22:03:33 WARNING Emergency mode active: operating with 5 engines on 'gluetun'
```

**Exit:**
```
2025-11-07 22:05:55 INFO ✅ EXITING EMERGENCY MODE ✅
2025-11-07 22:05:55 INFO VPN 'gluetun_2' has recovered after 125.3s
2025-11-07 22:05:55 INFO System will restore full capacity and resume normal operations
```

## Configuration

Emergency mode is **automatic** and requires no configuration. It activates when:

1. You're running in redundant VPN mode (`VPN_MODE=redundant`)
2. Both VPN containers are configured (`GLUETUN_CONTAINER_NAME` and `GLUETUN_CONTAINER_NAME_2`)
3. One VPN becomes unhealthy while the other remains healthy

Relevant configuration:
```bash
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_CONTAINER_NAME_2=gluetun_2
MIN_REPLICAS=10
```

During emergency mode:
- System operates with engines on single VPN
- Capacity may be reduced (e.g., 5 engines instead of 10)
- System is **happy with reduced capacity** until recovery
- No provisioning attempts to failed VPN

## Testing

Tests are located in `tests/test_emergency_mode.py`:

```bash
# Run emergency mode tests
python -m pytest tests/test_emergency_mode.py -v

# Test classes:
# - TestEmergencyModeState: State management
# - TestEmergencyModeIntegration: Service integration
# - TestEmergencyModeProvisioning: Provisioner behavior
```

## Recovery Behavior

After VPN recovery, the system:

1. **Detects Recovery** - VPN health check returns healthy
2. **Exits Emergency Mode** - Clears emergency state
3. **Waits for Stabilization** - 120 second stabilization period per VPN
4. **Provisions Engines** - Creates engines to restore MIN_REPLICAS
5. **Resumes Normal Operations** - Full redundant mode restored

### Per-VPN Stabilization Period

When a VPN recovers from a failure, it enters a **120-second stabilization period** to allow:
- VPN connection to fully establish
- Port forwarding to be configured
- Network routes to stabilize

**Key behavior:**
- **Per-VPN blocking**: Only blocks provisioning to the **specific VPN** that is stabilizing
- **Other VPNs unaffected**: Engines can still be provisioned on other healthy, stable VPNs
- **Improved uptime**: In redundant mode, if VPN1 is stabilizing, VPN2 can immediately receive new engines

**Example scenario:**
```
Time 0:00 - VPN1 fails and recovers → enters 120s stabilization
Time 0:30 - VPN2 fails and recovers → enters 120s stabilization
Time 0:45 - System can provision engines:
  ✗ VPN1: Still stabilizing (75s remaining)
  ✗ VPN2: Still stabilizing (105s remaining)
  Result: Health manager waits for target VPN to finish stabilizing

Time 2:00 - VPN1 stabilization ends
Time 2:15 - System can provision engines:
  ✓ VPN1: Stable and ready
  ✗ VPN2: Still stabilizing (15s remaining)
  Result: If VPN1 is selected as target, provisioning proceeds
          If VPN2 is selected, waits for stabilization to end
```

This per-VPN approach ensures maximum availability - engines can be provisioned on healthy VPNs even while other VPNs are stabilizing after recovery.

Example recovery:
```
Before recovery: 5 engines on gluetun (healthy)
After recovery:  10 engines total (5 on each VPN)
```

## Benefits

1. **Automatic Handling** - No manual intervention required
2. **Service Continuity** - Operates on single VPN seamlessly
3. **Clean State** - Failed VPN's engines properly cleaned up
4. **No Confusion** - Services know to pause operations
5. **Automatic Recovery** - Full capacity restored when VPN recovers
6. **Clear Visibility** - API and logs show emergency mode status

## Comparison: Before vs After

### Before Emergency Mode

```
❌ VPN fails
❌ Engines on failed VPN stay in unhealthy state
❌ Health manager tries to fix "unhealthy" engines (can't reach them)
❌ Autoscaler tries to provision to both VPNs (one is down)
❌ System confused about capacity (some engines unreachable)
❌ Manual intervention needed to clean up
```

### After Emergency Mode

```
✅ VPN fails
✅ Emergency mode activated immediately
✅ Failed VPN's engines stopped and removed
✅ Only healthy VPN used for operations
✅ Health manager paused (nothing to fix)
✅ Autoscaler paused (happy with reduced capacity)
✅ Clean automatic recovery when VPN returns
```

## Limitations

1. **Only for Redundant Mode** - Single VPN mode doesn't use emergency mode
2. **Requires Both VPNs** - Need both VPN containers configured
3. **One Failure at a Time** - If both VPNs fail, emergency mode can't help
4. **Reduced Capacity** - System operates with fewer engines during emergency

## Related Documentation

- [Gluetun Integration](GLUETUN_INTEGRATION.md) - VPN setup and configuration
- [Gluetun Failure & Recovery](GLUETUN_FAILURE_RECOVERY.md) - VPN failure scenarios
- [Health Monitoring](HEALTH_MONITORING.md) - Health check system
- [Architecture & Operations](ARCHITECTURE.md) - System design

## Example Scenario

### Normal Operation
```
VPN1 (gluetun):    [Engine1] [Engine2] [Engine3] [Engine4] [Engine5]
VPN2 (gluetun_2):  [Engine6] [Engine7] [Engine8] [Engine9] [Engine10]
Status: Both VPNs healthy, 10 engines total
```

### VPN2 Fails
```
VPN1 (gluetun):    [Engine1] [Engine2] [Engine3] [Engine4] [Engine5]
VPN2 (gluetun_2):  ❌ FAILED ❌
Status: Emergency mode active, 5 engines on VPN1
Actions: 
  - Engines 6-10 stopped and removed
  - New engines only assigned to VPN1
  - Health manager paused
  - Autoscaler paused
```

### VPN2 Recovers
```
VPN1 (gluetun):    [Engine1] [Engine2] [Engine3] [Engine4] [Engine5]
VPN2 (gluetun_2):  ✅ RECOVERED ✅
Status: Emergency mode deactivated, provisioning to restore capacity
Actions:
  - Exit emergency mode
  - Provision 5 new engines
  - Assign to both VPNs via round-robin
  - Resume normal operations
```

### Restored Operation
```
VPN1 (gluetun):    [Engine1] [Engine2] [Engine3] [Engine4] [Engine5]
VPN2 (gluetun_2):  [Engine11] [Engine12] [Engine13] [Engine14] [Engine15]
Status: Both VPNs healthy, 10 engines total, normal redundant operation
```
