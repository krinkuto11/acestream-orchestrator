# Capacity Calculation Bug - Analysis and Fix

## Problem Statement
The system was experiencing an issue where the capacity calculation in the `/orchestrator/status` endpoint was reporting `capacity_used > capacity_total`, which is logically impossible and indicates a bug in how capacity is calculated.

## Root Cause Analysis

### Evidence from Logs
In the acexy proxy logs (`context/logs/acexy/20251020_162643_orchestrator_health.jsonl`), we observed:

```json
{"capacity_available":0,"capacity_total":12,"capacity_used":14,...}
{"capacity_available":0,"capacity_total":13,"capacity_used":15,...}
```

This shows that `capacity_used` (14 and 15) exceeded `capacity_total` (12 and 13), which should never happen.

### The Bug
The bug was in `app/main.py` in the `/orchestrator/status` endpoint at lines 417-419:

```python
# OLD (BUGGY) CODE:
total_capacity = len(engines)
used_capacity = len(active_streams)  # BUG: counts total streams
available_capacity = max(0, total_capacity - used_capacity)
```

**The Issue:** The code was counting the **total number of active streams** as `used_capacity`, but this is incorrect because:
- A single engine can handle **multiple streams concurrently**
- With 12 engines and 14 active streams, if 12 engines each have 1 stream and 2 of those engines also have a second stream, we have 12 engines in use, not 14

## The Fix

Changed the capacity calculation to count **unique engines that have active streams**, not the total number of streams:

```python
# NEW (FIXED) CODE:
total_capacity = len(engines)
engines_with_streams = len(set(stream.container_id for stream in active_streams))
used_capacity = engines_with_streams  # FIXED: counts unique engines
available_capacity = max(0, total_capacity - used_capacity)
```

### Example Scenario

Given:
- 3 engines total
- 5 active streams distributed as:
  - Engine 1: 3 streams
  - Engine 2: 1 stream
  - Engine 3: 1 stream

**Old (buggy) calculation:**
- total_capacity: 3
- used_capacity: 5 (counts all streams)
- available_capacity: 0 (max(0, 3-5) = 0)
- **PROBLEM:** used > total (5 > 3)

**New (fixed) calculation:**
- total_capacity: 3
- used_capacity: 3 (counts unique engines: {Engine1, Engine2, Engine3})
- available_capacity: 0
- **CORRECT:** All 3 engines are in use

## Testing

Created `tests/test_capacity_calculation_fix.py` to verify:
1. The logic correctly counts unique engines with streams
2. The fix is present in the codebase
3. The bug scenario no longer produces invalid results

All tests pass ✓

## Security Check

Ran CodeQL security analysis - no vulnerabilities found ✓

## Impact

This fix ensures:
- Capacity metrics are now logically consistent
- The proxy (acexy) will receive accurate capacity information
- Provisioning decisions based on capacity will be correct
- No more confusing scenarios where used > total capacity
