# Port Release Fix

## Problem Statement

When running the orchestrator with Gluetun enabled, users were experiencing an error:

```
orchestrator  | 2025-10-12 21:38:16,998 INFO app.services.autoscaler: Starting 1 AceStream containers to maintain MIN_REPLICAS=10 free engines (currently: total=10, used=1, free=9)
orchestrator  | 2025-10-12 21:38:16,998 WARNING app.services.circuit_breaker: Recorded failed general provisioning
orchestrator  | 2025-10-12 21:38:16,998 ERROR app.services.autoscaler: Failed to start AceStream container 1/1: Maximum active replicas limit reached (20)
```

This error occurred even when there were only 10 engine containers running, well below the `MAX_ACTIVE_REPLICAS=20` limit.

## Root Cause

The issue was caused by port allocator state becoming out of sync with actual Docker containers.

### The Bug

In `app/services/autoscaler.py`, the `scale_to()` function was stopping containers directly:

```python
# OLD CODE (BUGGY)
if can_stop_engine(c.id, bypass_grace_period=False):
    try:
        c.stop(timeout=5)
        c.remove()
        stopped_count += 1
        logger.info(f"Stopped and removed container {c.id[:12]} ({stopped_count}/{excess})")
    except Exception as e:
        logger.error(f"Failed to stop container {c.id[:12]}: {e}")
```

This approach had a critical flaw: it stopped and removed containers without releasing their allocated ports.

When using Gluetun, the port allocator tracks which ports are in use in `app/services/ports.py`:
- `alloc_gluetun_port()` allocates a port and adds it to `_used_gluetun_ports`
- `free_gluetun_port()` removes a port from `_used_gluetun_ports`
- The allocator raises an error when `len(_used_gluetun_ports) >= MAX_ACTIVE_REPLICAS`

### The Scenario

1. System scales up to 20 containers (ports 19000-19019 allocated)
2. Demand decreases, system scales down to 10 containers
3. **BUG**: `scale_to()` calls `c.stop()` directly, ports 19010-19019 are NOT released
4. Port allocator still thinks 20 ports are in use
5. Later, system tries to start 1 more container (to maintain MIN_REPLICAS)
6. **ERROR**: Port allocation fails with "Maximum active replicas limit reached (20)"

Even though only 10 containers are running, the port allocator believes all 20 ports are still allocated.

## The Fix

Changed `scale_to()` to use the proper `stop_container()` function from `provisioner.py`:

```python
# NEW CODE (FIXED)
if can_stop_engine(c.id, bypass_grace_period=False):
    try:
        stop_container(c.id)
        stopped_count += 1
        logger.info(f"Stopped and removed container {c.id[:12]} ({stopped_count}/{excess})")
    except Exception as e:
        logger.error(f"Failed to stop container {c.id[:12]}: {e}")
```

The `stop_container()` function in `provisioner.py` properly releases ports through `_release_ports_from_labels()`:

```python
def stop_container(container_id: str):
    cli = get_client()
    cont = cli.containers.get(container_id)
    labels = cont.labels or {}
    cont.stop(timeout=10)
    try:
        _release_ports_from_labels(labels)  # <-- Releases ports here
    finally:
        cont.remove()
```

## Changes Made

1. **File**: `app/services/autoscaler.py`
   - Added `stop_container` to imports
   - Changed `scale_to()` to call `stop_container(c.id)` instead of `c.stop()` and `c.remove()`

2. **Tests**: Created two comprehensive tests
   - `tests/test_port_release_fix.py`: Verifies ports are released during scale-down
   - `tests/test_problem_scenario.py`: Reproduces and validates the fix for the exact problem scenario

## Testing

All tests pass, including:
- `test_port_release_fix.py`: Confirms `stop_container()` is called during scale-down
- `test_problem_scenario.py`: Demonstrates the bug and validates the fix
- `test_autoscaler_max_active_simple.py`: Existing MAX_ACTIVE_REPLICAS tests
- `test_gluetun_enhancements.py`: Gluetun integration tests
- `test_circuit_breaker_simple.py`: Circuit breaker functionality

## Impact

This fix ensures that:
1. Port allocator state stays synchronized with actual Docker containers
2. Ports are properly released when containers are stopped during scale-down
3. The "Maximum active replicas limit reached" error only occurs when truly at the limit
4. The system can scale up and down repeatedly without accumulating orphaned port allocations

## Related Components

- `app/services/autoscaler.py`: Contains the fix in `scale_to()` function
- `app/services/provisioner.py`: Contains `stop_container()` and `_release_ports_from_labels()`
- `app/services/ports.py`: Contains port allocation tracking (`PortAllocator` class)
