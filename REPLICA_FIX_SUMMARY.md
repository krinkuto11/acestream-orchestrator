# MIN_REPLICAS Fix Summary

## Problem Statement
The replica count was not working correctly - it appeared as if the system was full but no new instances were being created. The requirement was to maintain a minimum number of **EMPTY** replicas, not just total replicas.

**Example**: If there are 10 active replicas with at least 1 stream each, and MIN_REPLICAS=1, there should be an 11th replica that is empty.

## Root Cause
The autoscaler's `ensure_minimum()` and `can_stop_engine()` functions were checking against total running container count instead of the count of free/empty containers. This meant:
- MIN_REPLICAS was being interpreted as "minimum total replicas"
- When all replicas were busy, no new ones were provisioned
- The system appeared "full" even though it should have created more empty replicas

## Solution

### 1. Updated `ensure_minimum()` function
**File**: `app/services/autoscaler.py`

**Before**:
```python
deficit = cfg.MIN_REPLICAS - running_count
```
This calculated deficit based on total running containers.

**After**:
```python
total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
deficit = cfg.MIN_REPLICAS - free_count
```
Now calculates deficit based on FREE/EMPTY containers.

### 2. Updated `can_stop_engine()` function
**File**: `app/services/autoscaler.py`

**Before**:
```python
if running_count - 1 < cfg.MIN_REPLICAS:
    return False
```
Prevented stopping if total would go below MIN_REPLICAS.

**After**:
```python
total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
if free_count - 1 < cfg.MIN_REPLICAS:
    return False
```
Now prevents stopping if FREE count would go below MIN_REPLICAS.

## Impact

### Before the Fix
- **MIN_REPLICAS=1** with 10 busy replicas → 0 new replicas provisioned ❌
- System would wait until a replica became free before accepting new streams
- Could result in rejected requests when all replicas were busy

### After the Fix
- **MIN_REPLICAS=1** with 10 busy replicas → 1 new replica provisioned ✅
- System always maintains the specified number of empty replicas
- New streams can always be allocated to an available empty replica

## Formula
```
free_count = total_running - used_engines
deficit = max(0, MIN_REPLICAS - free_count)
```

Where:
- `total_running`: Total number of running containers (from Docker)
- `used_engines`: Number of containers with active streams
- `free_count`: Number of empty containers available
- `deficit`: Number of additional containers to provision

## Example Scenarios

### Scenario 1: Normal Operation
- MIN_REPLICAS=2
- Total: 5, Used: 3, Free: 2
- Deficit: max(0, 2 - 2) = 0
- ✅ No action needed, already have 2 free

### Scenario 2: All Busy (Problem Statement)
- MIN_REPLICAS=1
- Total: 10, Used: 10, Free: 0
- Deficit: max(0, 1 - 0) = 1
- ✅ Provision 1 new replica → Result: 11 total, 10 used, 1 free

### Scenario 3: Need Multiple
- MIN_REPLICAS=3
- Total: 5, Used: 3, Free: 2
- Deficit: max(0, 3 - 2) = 1
- ✅ Provision 1 new replica → Result: 6 total, 3 used, 3 free

## Testing

### New Tests Created
1. `test_min_free_replicas_simple.py` - Validates the mathematical formula
2. `test_problem_statement_scenario.py` - Directly tests the problem statement scenario
3. `test_min_empty_replicas.py` - Integration test with mocked provisioning

### Existing Tests Updated
1. `test_min_replicas_autoscaler.py` - Updated to work with new free-count logic
   - Added patching for `replica_validator.list_managed`
   - Updated test descriptions to clarify "free" vs "total"

### All Tests Pass ✅
- `test_min_replicas_autoscaler.py` - PASSED
- `test_min_free_replicas_simple.py` - PASSED
- `test_problem_statement_scenario.py` - PASSED
- `test_reliability_enhancements.py` - PASSED
- `test_engine_cleanup_verification.py` - PASSED

## Backward Compatibility

### Configuration
No changes to configuration format. `MIN_REPLICAS` still accepts the same integer values.

### Behavior Change
⚠️ **Important**: The meaning of `MIN_REPLICAS` has changed:
- **Old behavior**: Minimum total replicas
- **New behavior**: Minimum free/empty replicas

### Migration
Users should review their `MIN_REPLICAS` setting:
- If previously set to maintain "always-on" capacity → **No change needed**
- If set based on total expected load → **May need adjustment**

**Recommendation**: For most use cases, `MIN_REPLICAS=1` is sufficient to ensure at least one empty replica is always available for new requests.

## Code Changes Summary

### Modified Files
1. `app/services/autoscaler.py`
   - `ensure_minimum()`: Changed to use `free_count` instead of `running_count`
   - `can_stop_engine()`: Changed to check `free_count` instead of `running_count`

2. `tests/test_min_replicas_autoscaler.py`
   - Added patching for `replica_validator.list_managed`
   - Updated test descriptions and assertions

### New Files
1. `tests/test_min_free_replicas_simple.py`
2. `tests/test_problem_statement_scenario.py`
3. `tests/test_min_empty_replicas.py` (comprehensive integration test)
4. `tests/test_min_free_replicas_unit.py`

## Benefits
1. ✅ Always maintains minimum empty replicas for new requests
2. ✅ Prevents "system appears full" issues
3. ✅ Better resource utilization and auto-scaling
4. ✅ Improved reliability and availability
5. ✅ More intuitive behavior aligned with user expectations
