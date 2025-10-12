# Gluetun Port Double-Counting Fix

## Problem

When using Gluetun VPN integration, the orchestrator was experiencing premature `MAX_ACTIVE_REPLICAS` limit errors. The logs showed:

```
2025-10-12 22:05:55,057 ERROR app.services.autoscaler: Failed to start AceStream container 1/1: Maximum active replicas limit reached (20)
```

This happened even though only 10 containers were running, when the limit was set to 20.

### Root Cause

The port allocator was **double-counting Gluetun ports** during container reindexing and cleanup:

1. **During reindex** (`reindex.py`): Both `HOST_LABEL_HTTP` and `ACESTREAM_LABEL_HTTP` ports were being reserved
2. **During cleanup** (`provisioner.py`): Both ports were being freed

This meant:
- 10 containers × 2 ports each = 20 ports reserved
- Hitting the `MAX_ACTIVE_REPLICAS=20` limit immediately
- Circuit breaker opening after 5 consecutive failures
- No new containers could be started

## Solution

Changed both `reindex.py` and `provisioner.py` to only count **one port per container** when using Gluetun:

### In `reindex.py`
```python
# Reserve Gluetun ports if using Gluetun
# Only reserve one port per container (use HOST_LABEL_HTTP as the primary port)
# to avoid double-counting which would cause MAX_ACTIVE_REPLICAS limit to be hit prematurely
if cfg.GLUETUN_CONTAINER_NAME:
    try:
        if HOST_LABEL_HTTP in lbl: 
            alloc.reserve_gluetun_port(int(lbl[HOST_LABEL_HTTP]))
    except Exception: pass
```

### In `provisioner.py`
```python
# Release Gluetun ports if using Gluetun
# Only free one port per container (use HOST_LABEL_HTTP as the primary port)
# to match the reserve behavior and avoid double-counting
if cfg.GLUETUN_CONTAINER_NAME:
    try:
        hp = labels.get(HOST_LABEL_HTTP); alloc.free_gluetun_port(int(hp) if hp else None)
    except Exception: pass
```

## Additional Improvements

### 1. MIN_REPLICAS Validation

Added validation to ensure `MIN_REPLICAS >= 1`:

```python
@model_validator(mode='after')
def validate_min_replicas(self):
    if self.MIN_REPLICAS < 1:
        raise ValueError('MIN_REPLICAS must be >= 1 to ensure at least 1 free replica is always available')
    return self
```

**Why?** The user requirement stated: "has to be a minimum of 1 empty replica"

### 2. Better Error Logging

Improved logging when hitting `MAX_ACTIVE_REPLICAS` limit:

```python
logger.warning(
    f"Cannot start containers - already at MAX_ACTIVE_REPLICAS limit ({cfg.MAX_ACTIVE_REPLICAS}). "
    f"Current state: total={total_running}, used={used_engines}, free={free_count}. "
    f"To maintain MIN_REPLICAS={cfg.MIN_REPLICAS} free engines with current usage, "
    f"increase MAX_ACTIVE_REPLICAS or reduce MIN_REPLICAS."
)
```

This provides clear actionable information when the limit is reached.

## Testing

Added comprehensive test in `tests/test_gluetun_port_double_counting_fix.py`:

1. **Port Allocation Test**: Verifies that ports are allocated one per container up to `MAX_ACTIVE_REPLICAS`
2. **Reindex Test**: Verifies that reindexing only reserves one port per container
3. **Limit Test**: Verifies that allocation properly fails when limit is reached
4. **Release Test**: Verifies that port release works correctly

## Impact

After this fix:
- ✅ 20 containers can run with `MAX_ACTIVE_REPLICAS=20` (previously only 10)
- ✅ Circuit breaker no longer opens due to this issue
- ✅ Autoscaler can maintain desired `MIN_REPLICAS` free engines
- ✅ Clear error messages when legitimate limits are reached
- ✅ Enforced minimum of 1 free replica for availability

## Configuration Changes

- **Default `MIN_REPLICAS`**: Changed from `0` to `1`
- **Validation**: `MIN_REPLICAS` must now be >= 1
- **Documentation**: Updated to reflect new minimum requirement
