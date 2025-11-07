# VPN Port Change Handling

## Overview

When using Gluetun VPN with port forwarding, the VPN may restart internally and receive a new forwarded port. This document describes how the orchestrator automatically handles this scenario to prevent service disruption.

## Problem Scenario

From the logs, we can see that Gluetun may restart internally without engines becoming unhealthy:

```
gluetun  | 2025-11-06T10:48:49Z INFO [firewall] setting allowed input port 65290...
gluetun  | 2025-11-06T19:42:58Z WARN [vpn] restarting VPN because it failed to pass the healthcheck
gluetun  | 2025-11-06T19:42:58Z INFO [firewall] removing allowed port 65290...
gluetun  | 2025-11-06T19:43:04Z INFO [port forwarding] port forwarded is 40648
gluetun  | 2025-11-06T19:43:04Z INFO [firewall] setting allowed input port 40648...
```

The forwarded port changed from **65290** to **40648**. The existing forwarded engine was still using port 65290, making it unable to accept P2P connections.

## Solution

The orchestrator now automatically detects port changes and replaces the forwarded engine:

### Detection

1. **Port Tracking**: Each `VpnContainerMonitor` tracks the last known stable forwarded port
2. **Monitoring Loop**: Every health check cycle, the system checks if the forwarded port has changed
3. **Change Detection**: When a port change is detected, the system logs it and triggers replacement

### Replacement Process

When a port change is detected:

1. **Identify Engine**: Find the forwarded engine for the affected VPN
2. **Remove from State**: Immediately remove the engine from state (hides it from `/engines` endpoint)
3. **Stop Container**: Stop and remove the old forwarded engine container
4. **Automatic Provisioning**: The autoscaler detects the missing engine and provisions a new one with the new port

### Benefits

- **Zero Proxy Errors**: Old engine is hidden from `/engines` before proxy can route to it
- **Automatic Recovery**: New forwarded engine is provisioned automatically
- **Minimal Disruption**: Only active streams on the old engine are affected
- **Operational Visibility**: Clear logging of port changes for debugging

## Implementation Details

### VpnContainerMonitor

The `VpnContainerMonitor` class tracks port changes:

```python
class VpnContainerMonitor:
    def __init__(self, container_name: str):
        # ... existing fields ...
        self._last_stable_forwarded_port: Optional[int] = None
    
    async def check_port_change(self) -> Optional[tuple[int, int]]:
        """Check if the forwarded port has changed."""
        # Only check when VPN is healthy
        if not self._last_health_status:
            return None
        
        current_port = await self._fetch_and_cache_port()
        
        if current_port and self._last_stable_forwarded_port:
            if current_port != self._last_stable_forwarded_port:
                old_port = self._last_stable_forwarded_port
                self._last_stable_forwarded_port = current_port
                return (old_port, current_port)
        
        return None
```

### GluetunMonitor

The main monitoring loop checks for port changes:

```python
async def _monitor_gluetun(self):
    while not self._stop.is_set():
        for container_name, monitor in self._vpn_monitors.items():
            # ... health checks ...
            
            if current_health:
                port_change = await monitor.check_port_change()
                if port_change:
                    old_port, new_port = port_change
                    await self._handle_port_change(container_name, old_port, new_port)
```

### Port Change Handler

```python
async def _handle_port_change(self, container_name: str, old_port: int, new_port: int):
    """Handle VPN forwarded port change by replacing the forwarded engine."""
    
    # Find the forwarded engine
    forwarded_engine = state.get_forwarded_engine_for_vpn(container_name)
    
    if forwarded_engine:
        # Remove from state (hides from /engines endpoint)
        state.remove_engine(forwarded_engine.container_id)
        
        # Stop the container
        stop_container(forwarded_engine.container_id)
        
        logger.info(f"Forwarded engine replaced - autoscaler will provision new engine with port {new_port}")
```

## Monitoring and Debugging

### Log Messages

When a port change occurs, you'll see these log messages:

```
WARNING [gluetun] VPN 'gluetun' forwarded port changed from 65290 to 40648
WARNING [gluetun] VPN 'gluetun' port changed from 65290 to 40648 - replacing forwarded engine
INFO [gluetun] Stopping forwarded engine acestream_fwd_123 due to port change
INFO [gluetun] Successfully stopped forwarded engine acestream_fwd_123
INFO [gluetun] Forwarded engine replacement triggered - autoscaler will provision new engine with port 40648
```

### Debug Mode

Enable debug mode to see detailed port change detection:

```bash
DEBUG_MODE=true
DEBUG_LOG_DIR=./debug_logs
```

This will create detailed logs in the debug directory showing:
- Port cache updates
- Port change detections
- Engine replacement operations

## Configuration

No additional configuration is needed. The feature works automatically with existing VPN settings:

```bash
# Existing VPN configuration
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_HEALTH_CHECK_INTERVAL_S=5
GLUETUN_PORT_CACHE_TTL_S=60

# For redundant VPN mode
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME_2=gluetun2
```

## Redundant VPN Mode

In redundant VPN mode, port changes are handled independently for each VPN:

- Each VPN has its own forwarded engine
- Port changes only affect the specific VPN's forwarded engine
- Other VPN's forwarded engine continues operating normally

Example:
```
VPN1 (gluetun): Port changes from 65290 to 40648
  → Only VPN1's forwarded engine is replaced
  
VPN2 (gluetun2): Continues using port 51234
  → VPN2's forwarded engine unaffected
```

## Testing

Run the included tests to verify the functionality:

```bash
# Unit tests
python tests/test_vpn_port_change_detection.py

# Demo scenario
python tests/demo_vpn_port_change_scenario.py
```

## Related Documentation

- [Gluetun Integration](GLUETUN_INTEGRATION.md) - VPN integration overview
- [Gluetun Failure & Recovery](GLUETUN_FAILURE_RECOVERY.md) - Other VPN failure scenarios
- [Health Monitoring](HEALTH_MONITORING.md) - Health check system
