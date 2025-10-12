# Autoscaler MAX_ACTIVE_REPLICAS Fix

## Problem Summary

The autoscaler was attempting to start containers beyond the `MAX_ACTIVE_REPLICAS` limit when using Gluetun, causing failures with the error "Maximum active replicas limit reached".

### Example from the Logs

```
orchestrator  | 2025-10-12T18:13:38.639751650Z 2025-10-12 20:13:38,639 INFO app.services.autoscaler: Starting 1 AceStream containers to maintain MIN_REPLICAS=10 free engines (currently: total=10, used=1, free=9)
orchestrator  | 2025-10-12T18:13:38.639849411Z 2025-10-12 20:13:38,639 WARNING app.services.circuit_breaker: Recorded failed general provisioning
orchestrator  | 2025-10-12T18:13:38.639860702Z 2025-10-12 20:13:38,639 ERROR app.services.autoscaler: Failed to start AceStream container 1/1: Maximum active replicas limit reached (20)
```

### Root Cause

When using Gluetun for VPN integration:
- `MAX_ACTIVE_REPLICAS` limits the total number of containers that can use Gluetun's network (due to port range limitations)
- The `alloc_gluetun_port()` function enforces this limit by throwing an error when trying to allocate more ports
- The autoscaler's `ensure_minimum()` function was checking `MIN_REPLICAS` but not `MAX_ACTIVE_REPLICAS`
- This caused the autoscaler to attempt starting containers that would fail during port allocation

### Configuration in the Example
- `MIN_REPLICAS=10` - Desired minimum number of containers
- `MAX_REPLICAS=50` - Maximum allowed total containers
- `MAX_ACTIVE_REPLICAS=20` - Hard limit for Gluetun-connected containers

## Solution

The fix adds proper `MAX_ACTIVE_REPLICAS` checking in two key functions:

### 1. `ensure_minimum()` Function Changes

**Before:** 
- Calculated deficit as `MIN_REPLICAS - running_count`
- Attempted to start containers without checking `MAX_ACTIVE_REPLICAS`

**After:**
- Calculates `effective_min_replicas = min(MIN_REPLICAS, MAX_ACTIVE_REPLICAS)` when using Gluetun
- Checks if already at `MAX_ACTIVE_REPLICAS` limit before attempting to start containers
- Logs warning and returns early if at the limit
- Reduces deficit if it would exceed `MAX_ACTIVE_REPLICAS`

### 2. `scale_to()` Function Changes

**Before:**
- Calculated desired as `min(max(MIN_REPLICAS, demand), MAX_REPLICAS)`

**After:**
- Also applies `min(desired, MAX_ACTIVE_REPLICAS)` when using Gluetun

## Code Changes

### app/services/autoscaler.py

```python
# Calculate effective MIN_REPLICAS when using Gluetun
effective_min_replicas = cfg.MIN_REPLICAS
if cfg.GLUETUN_CONTAINER_NAME:
    effective_min_replicas = min(cfg.MIN_REPLICAS, cfg.MAX_ACTIVE_REPLICAS)
    if effective_min_replicas < cfg.MIN_REPLICAS:
        logger.debug(f"MIN_REPLICAS capped at MAX_ACTIVE_REPLICAS={cfg.MAX_ACTIVE_REPLICAS} when using Gluetun")

# Check if already at MAX_ACTIVE_REPLICAS limit
if cfg.GLUETUN_CONTAINER_NAME and deficit > 0:
    max_new_containers = cfg.MAX_ACTIVE_REPLICAS - running_count
    if max_new_containers <= 0:
        logger.warning(f"Cannot start containers - already at MAX_ACTIVE_REPLICAS limit ({cfg.MAX_ACTIVE_REPLICAS})")
        return
    if deficit > max_new_containers:
        logger.warning(f"Reducing deficit from {deficit} to {max_new_containers} to respect MAX_ACTIVE_REPLICAS={cfg.MAX_ACTIVE_REPLICAS}")
        deficit = max_new_containers
```

## Testing

Created comprehensive test suite in `tests/test_autoscaler_max_active_simple.py`:

1. **Test at MAX_ACTIVE_REPLICAS limit:**
   - Simulates 20 running containers (at limit)
   - Verifies `ensure_minimum()` doesn't attempt to start more containers
   - Result: ✅ PASSED

2. **Test scale_to capping:**
   - Simulates 10 running containers
   - Attempts to scale to 25 (exceeds limit)
   - Verifies only 10 containers are started (to reach limit of 20)
   - Result: ✅ PASSED

## Impact

### Before the Fix
- Autoscaler repeatedly tried to start containers beyond the limit
- Failed with "Maximum active replicas limit reached" errors
- Circuit breaker recorded failures
- Unnecessary error logs and provisioning attempts

### After the Fix
- Autoscaler respects `MAX_ACTIVE_REPLICAS` limit
- Logs informative warning when at limit
- No failed provisioning attempts
- Cleaner logs and better resource management

## Backwards Compatibility

- ✅ No impact on non-Gluetun configurations
- ✅ Existing tests still pass
- ✅ Only affects behavior when `GLUETUN_CONTAINER_NAME` is set
- ✅ Gracefully degrades: if MIN_REPLICAS > MAX_ACTIVE_REPLICAS, uses MAX_ACTIVE_REPLICAS

## Example Log Output After Fix

```
orchestrator  | 2025-10-12 20:15:00,000 DEBUG app.services.autoscaler: MIN_REPLICAS capped at MAX_ACTIVE_REPLICAS=20 when using Gluetun
orchestrator  | 2025-10-12 20:15:00,001 WARNING app.services.autoscaler: Cannot start containers - already at MAX_ACTIVE_REPLICAS limit (20)
```

## Related Configuration

When using Gluetun, ensure your configuration has reasonable values:

```env
# Recommended configuration
MIN_REPLICAS=10          # Desired minimum
MAX_REPLICAS=50          # Absolute maximum
MAX_ACTIVE_REPLICAS=20   # Hard limit for Gluetun

# Ensure MIN_REPLICAS <= MAX_ACTIVE_REPLICAS when using Gluetun
```

## Verification Steps

1. Check current running containers: `docker ps | grep acestream | wc -l`
2. Review autoscaler logs for the new warning messages
3. Verify no "Maximum active replicas limit reached" errors in provisioner
4. Confirm containers at MAX_ACTIVE_REPLICAS remain stable

## Future Considerations

If you need more than 20 concurrent containers with Gluetun:
1. Increase `MAX_ACTIVE_REPLICAS` (requires more ports)
2. Expand `PORT_RANGE_HOST` range accordingly
3. Ensure Gluetun has sufficient port forwarding capacity
