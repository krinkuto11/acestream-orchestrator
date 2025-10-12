# Autoscaler MAX_ACTIVE_REPLICAS Fix Summary

## Problem

When using Gluetun for VPN integration, the autoscaler was attempting to start containers beyond the `MAX_ACTIVE_REPLICAS` limit, causing repeated provisioning failures:

```
orchestrator  | 2025-10-12 20:13:38,639 INFO app.services.autoscaler: Starting 1 AceStream containers to maintain MIN_REPLICAS=10 free engines (currently: total=10, used=1, free=9)
orchestrator  | 2025-10-12 20:13:38,639 WARNING app.services.circuit_breaker: Recorded failed general provisioning
orchestrator  | 2025-10-12 20:13:38,639 ERROR app.services.autoscaler: Failed to start AceStream container 1/1: Maximum active replicas limit reached (20)
```

Additionally, logs were noisy with HTTP access logs and verbose cache cleanup messages.

### Root Cause

When Gluetun is enabled, `MAX_ACTIVE_REPLICAS` acts as a hard limit on the total number of containers due to port range constraints. The port allocator (`PortAllocator.alloc_gluetun_port()`) enforces this limit by raising an exception when the limit is reached.

However, the autoscaler's `ensure_minimum()` and `scale_to()` functions were only checking `MIN_REPLICAS` and `MAX_REPLICAS`, without considering `MAX_ACTIVE_REPLICAS`. This caused the autoscaler to attempt provisioning containers that would inevitably fail during port allocation, resulting in:
- Unnecessary error logs
- Circuit breaker recording false failures
- Wasted provisioning attempts

## Solution

Modified the autoscaler to respect `MAX_ACTIVE_REPLICAS` as a hard limit when using Gluetun:

### Changes to `ensure_minimum()`

1. **Calculate effective minimum**: Cap `MIN_REPLICAS` at `MAX_ACTIVE_REPLICAS` when Gluetun is enabled
2. **Pre-check current state**: Before attempting any provisioning, verify we haven't already reached `MAX_ACTIVE_REPLICAS`
3. **Adjust deficit**: Reduce the number of containers to start if it would exceed the limit

```python
# When using Gluetun, respect MAX_ACTIVE_REPLICAS as a hard limit
effective_min_replicas = cfg.MIN_REPLICAS
if cfg.GLUETUN_CONTAINER_NAME:
    effective_min_replicas = min(cfg.MIN_REPLICAS, cfg.MAX_ACTIVE_REPLICAS)

# Check if already at MAX_ACTIVE_REPLICAS limit
if cfg.GLUETUN_CONTAINER_NAME and deficit > 0:
    max_new_containers = cfg.MAX_ACTIVE_REPLICAS - total_running
    if max_new_containers <= 0:
        logger.warning(f"Cannot start containers - already at MAX_ACTIVE_REPLICAS limit ({cfg.MAX_ACTIVE_REPLICAS})")
        return
```

### Changes to `scale_to()`

Applied similar logic to cap the desired container count at `MAX_ACTIVE_REPLICAS` when using Gluetun.

### Logging Improvements

1. **Suppressed HTTP access logs**: Disabled uvicorn access logs to eliminate HTTP request noise from stdout
2. **Shortened cache cleanup logs**: Cache cleanup now only logs when >0MB is cleared, with concise format: `Cleared 5.2MB cache from abc123def456`
3. **Docker socket as source of truth**: Added `get_docker_active_replicas_count()` method to query actual running containers from Docker for more reliable `MAX_ACTIVE_REPLICAS` enforcement, instead of relying solely on in-memory port tracking

## Impact

**Before this fix:**
- Autoscaler repeatedly attempted to provision beyond the limit
- Errors: `Maximum active replicas limit reached (20)`
- Circuit breaker recorded failures
- Noisy logs with HTTP requests and verbose cache cleanup messages
- Replica count based on in-memory port tracking

**After this fix:**
- Autoscaler respects the limit and stops gracefully
- Clean warning logs: `Cannot start containers - already at MAX_ACTIVE_REPLICAS limit (20)`
- No failed provisioning attempts
- Circuit breaker remains healthy
- HTTP access logs suppressed
- Cache cleanup only logs meaningful actions (>0MB)
- Replica count based on Docker socket (most reliable)

## Testing

Added comprehensive test suite (`test_autoscaler_max_active_simple.py`) covering:
- ✅ Autoscaler stops when at `MAX_ACTIVE_REPLICAS` limit
- ✅ Autoscaler caps scaling requests at the limit
- ✅ Existing reliability tests continue to pass

## Backwards Compatibility

This change only affects behavior when `GLUETUN_CONTAINER_NAME` is set. Non-Gluetun configurations are unaffected.

## Configuration Recommendations

When using Gluetun, ensure `MIN_REPLICAS ≤ MAX_ACTIVE_REPLICAS`:

```env
MIN_REPLICAS=10          # Desired minimum
MAX_REPLICAS=50          # Absolute maximum
MAX_ACTIVE_REPLICAS=20   # Hard limit for Gluetun (enforced by port range)
```

If you need more concurrent containers, increase both `MAX_ACTIVE_REPLICAS` and expand the `PORT_RANGE_HOST` accordingly.

## Files Modified

1. **app/services/autoscaler.py**
   - Added MAX_ACTIVE_REPLICAS checks in `ensure_minimum()`
   - Added MAX_ACTIVE_REPLICAS capping in `scale_to()`

2. **app/services/replica_validator.py**
   - Added `get_docker_active_replicas_count()` method for reliable replica counting

3. **app/services/provisioner.py**
   - Improved cache cleanup logging (concise, only when >0MB)

4. **app/services/state.py**
   - Reduced verbosity of cache cleanup initiation log

5. **Dockerfile**
   - Added `--access-log false` to uvicorn command to suppress HTTP access logs

6. **tests/test_autoscaler_max_active_simple.py**
   - New comprehensive test suite for MAX_ACTIVE_REPLICAS enforcement
