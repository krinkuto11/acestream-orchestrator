# Fix: Forwarded Engine Label Race Condition

## Problem Description

When starting multiple AceStream containers sequentially (e.g., 10 containers during initial startup), all containers were incorrectly receiving the `acestream.forwarded=true` Docker label, instead of just one container. This created confusion and made it impossible to identify which engine actually had the forwarded P2P port.

### Symptoms

From the logs, you would see:
```
orchestrator  | 2025-10-30 17:10:30,871 INFO app.services.provisioner: Provisioning new forwarded engine with P2P port 65376
orchestrator  | 2025-10-30 17:10:31,024 INFO app.services.provisioner: Engine 2bbf101c6582 provisioned as forwarded engine
orchestrator  | 2025-10-30 17:10:32,048 INFO app.services.provisioner: Provisioning new forwarded engine with P2P port 65376
orchestrator  | 2025-10-30 17:10:32,215 INFO app.services.provisioner: Engine 8b3379e39337 provisioned as forwarded engine
...
```

And during reindex:
```
orchestrator  | 2025-10-30 17:10:43,691 INFO app.services.state: Engine f429ffe4bee6 is now the forwarded engine
orchestrator  | 2025-10-30 17:10:43,691 INFO app.services.reindex: Reindexed forwarded engine: f429ffe4bee6
orchestrator  | 2025-10-30 17:10:43,728 INFO app.services.state: Engine a670190c4a55 is now the forwarded engine
orchestrator  | 2025-10-30 17:10:43,728 INFO app.services.reindex: Reindexed forwarded engine: a670190c4a55
...
```

All 10 containers had `acestream.forwarded=true` in their Docker labels, but only the last one processed during reindex would be marked as forwarded in state.

## Root Cause

The issue was a **race condition** in the provisioning logic:

1. **Sequential Provisioning Loop** (`autoscaler.py`):
   - Containers are provisioned one by one in a loop
   - Each container calls `start_acestream()` to provision a new engine

2. **Forwarded Check** (`provisioner.py`):
   - `start_acestream()` checks `state.has_forwarded_engine()` to determine if this should be the forwarded engine
   - If no forwarded engine exists, it sets `is_forwarded = True` and adds the Docker label `acestream.forwarded=true`

3. **State Update Timing** (THE BUG):
   - The engine was **not** added to state until much later
   - After all containers were provisioned, `reindex_existing()` would be called
   - Only then would engines be added to state

4. **Result**:
   - Container 1: Checks `has_forwarded_engine()` → False → Gets forwarded=true label
   - Container 2: Checks `has_forwarded_engine()` → **Still False** (not in state yet) → Gets forwarded=true label
   - Container 3-10: Same issue, all get forwarded=true label

## Solution

The fix is to **immediately add the engine to state** after provisioning, before moving to the next container.

### Changes in `app/services/provisioner.py`

After a container is successfully started, we now:

1. Create an `EngineState` object with all the container details
2. Add it to `state.engines` immediately
3. If it's marked as forwarded, call `state.set_forwarded_engine()` immediately
4. Persist to database

This ensures that when the next container is provisioned, `state.has_forwarded_engine()` will return `True`, preventing it from also being marked as forwarded.

### Changes in `app/services/reindex.py`

Improved the reindex logic to handle the case where multiple containers have the forwarded label (from the bug):

1. Check if a container has the forwarded label
2. **Only mark it as forwarded if no other engine is already forwarded**
3. This prevents multiple engines from being marked as forwarded during reindex

The key change:
```python
# Check if this container is marked as forwarded
is_forwarded_label = lbl.get(FORWARDED_LABEL, "false").lower() == "true"

# Only mark as forwarded if no other engine is already forwarded
# This handles the case where multiple containers have the forwarded label (bug scenario)
should_be_forwarded = is_forwarded_label and not state.has_forwarded_engine()

state.engines[key] = EngineState(..., forwarded=should_be_forwarded, ...)
```

## Testing

Created comprehensive tests in `tests/test_forwarded_label_race_condition.py`:

1. **Sequential Provisioning Test**: Simulates provisioning 10 containers and verifies only the first gets the forwarded label
2. **Reindex Test**: Simulates the bug scenario (all containers have forwarded=true labels) and verifies reindex only marks one as forwarded
3. **State Management Test**: Verifies `has_forwarded_engine()` works correctly during provisioning

All tests pass successfully.

## Verification

After applying this fix:

✅ Only ONE container receives `acestream.forwarded=true` Docker label  
✅ Only ONE engine is marked as forwarded in state  
✅ The first provisioned container is the forwarded one  
✅ Subsequent containers correctly see that a forwarded engine exists  
✅ Reindex correctly handles containers that have incorrect labels from before the fix  
✅ No log spam from repeatedly calling `set_forwarded_engine()`  

## Backward Compatibility

The fix is backward compatible:

- Existing containers with incorrect labels will be handled correctly during reindex
- Only the first one encountered will be marked as forwarded
- No manual intervention required
- The fix prevents new containers from getting incorrect labels

## Related Files

- `app/services/provisioner.py` - Immediate state update after provisioning
- `app/services/reindex.py` - Improved reindex logic to handle multiple forwarded labels
- `app/services/state.py` - State management methods (`has_forwarded_engine()`, `set_forwarded_engine()`)
- `tests/test_forwarded_label_race_condition.py` - Comprehensive test suite
- `tests/test_forwarded_engine.py` - Updated to run without pytest
