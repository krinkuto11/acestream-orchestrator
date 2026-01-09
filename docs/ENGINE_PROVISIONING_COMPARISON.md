# Engine Provisioning - Before vs After

## Summary of Changes

This document illustrates the difference between the old and new engine provisioning strategies.

---

## BEFORE: Sequential Filling (Broken)

### Problem
Engines were sorted by **most** streams first, causing one engine to fill completely before using the next:

```
MAX_STREAMS_PER_ENGINE = 5

Stream Assignment:
  Stream 1  → engine_0 [1/5] ←─ Forwarded
  Stream 2  → engine_0 [2/5] ←─ Fill this one first
  Stream 3  → engine_0 [3/5]
  Stream 4  → engine_0 [4/5]
  Stream 5  → engine_0 [5/5] ←─ FULL
  Stream 6  → engine_1 [1/5]
  Stream 7  → engine_1 [2/5]
  Stream 8  → engine_1 [3/5]
  Stream 9  → engine_1 [4/5]
  Stream 10 → engine_1 [5/5] ←─ FULL
  Stream 11 → engine_2 [1/5]
  ...

State after 10 streams:
  engine_0: [█████] 5/5 FULL
  engine_1: [█████] 5/5 FULL
  engine_2: [░░░░░] 0/5 EMPTY

❌ Autoscaler waiting for ALL engines to reach 4/5 before provisioning
❌ New engine not provisioned until stream 13+
❌ Gap in capacity when stream 11 arrives (engine_2 just started)
```

---

## AFTER: Layer-Based Filling with Lookahead ✅

### Solution
Engines sorted by **least** streams first, with lookahead provisioning:

```
MAX_STREAMS_PER_ENGINE = 5

Layer 1: All engines get 1 stream first
  Stream 1 → engine_0 [1/5] ←─ Forwarded (priority at tie)
  Stream 2 → engine_1 [1/5]
  Stream 3 → engine_2 [1/5]

Layer 2: All engines get 2 streams
  Stream 4 → engine_0 [2/5] ←─ Forwarded (priority at tie)
  Stream 5 → engine_1 [2/5]
  Stream 6 → engine_2 [2/5]

Layer 3: All engines get 3 streams
  Stream 7 → engine_0 [3/5]
  Stream 8 → engine_1 [3/5]
  Stream 9 → engine_2 [3/5]

Layer 4: LOOKAHEAD TRIGGER! 🔔
  Stream 10 → engine_0 [4/5] ←─ ⚠️ FIRST engine at 4/5: START PROVISIONING engine_3
  Stream 11 → engine_1 [4/5]
  Stream 12 → engine_2 [4/5]

Layer 5: Overflow buffer (while engine_3 provisions)
  Stream 13 → engine_0 [5/5] ←─ Using reserve slot
  Stream 14 → engine_1 [5/5]
  Stream 15 → engine_2 [5/5]

New Engine Ready: ✅
  Stream 16 → engine_3 [1/5] ←─ Prefers new engine (back to layer 1)

State after 12 streams:
  engine_0: [████░] 4/5
  engine_1: [████░] 4/5  ←─ Balanced load
  engine_2: [████░] 4/5
  engine_3: [░░░░░] 0/5 ←─ Provisioning started

✅ Balanced load across all engines
✅ Early provisioning (at stream 10, not stream 13)
✅ Buffer time for new engine to be ready
✅ No capacity gaps
```

---

## Key Improvements

### 1. Load Distribution

**Before:**
```
engine_0: [█████] 5/5
engine_1: [██░░░] 2/5  ← Unbalanced
engine_2: [░░░░░] 0/5
```

**After:**
```
engine_0: [███░░] 3/5
engine_1: [███░░] 3/5  ← Balanced
engine_2: [███░░] 3/5
```

### 2. Provisioning Trigger

| Metric | Before | After |
|--------|--------|-------|
| **Trigger condition** | ALL engines at 4/5 | FIRST engine at 4/5 |
| **Stream # when triggered** | Stream 13 | Stream 10 |
| **Buffer time** | Minimal | 3 stream-widths |
| **Risk of overflow** | High | Low |

### 3. Engine Selection Priority

**Before:**
```python
# Sort by MOST streams first (descending)
sorted(engines, key=lambda e: (
    -engine_loads.get(e.container_id, 0),  # Negative = descending
    not e.forwarded
))
# Result: Fill one engine at a time (sequential)
```

**After:**
```python
# Sort by LEAST streams first (ascending)
sorted(engines, key=lambda e: (
    engine_loads.get(e.container_id, 0),   # Positive = ascending
    not e.forwarded
))
# Result: Fill all engines evenly (layers)
```

---

## Visual Timeline Comparison

### Before (Sequential)
```
Time    Event                          State
━━━━    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━   ━━━━━━━━━━━━━━━━━━━━━
T+0     Stream 1 arrives               [█░░░░][░░░░░][░░░░░]
T+10    Stream 5 arrives               [█████][░░░░░][░░░░░]
T+20    Stream 10 arrives              [█████][█████][░░░░░]
T+25    Stream 13 arrives              [█████][█████][███░░]
T+26    🔔 Trigger: ALL at 4+          [█████][█████][████░]
T+27    Start provisioning             → engine_3 starting...
T+35    Stream 15 fills all            [█████][█████][█████]
T+40    ❌ GAP: All full, engine_3 not ready yet
T+50    ✅ engine_3 ready              [█████][█████][█████][░░░░░]
```

### After (Layers + Lookahead)
```
Time    Event                          State
━━━━    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━   ━━━━━━━━━━━━━━━━━━━━━
T+0     Stream 1 arrives               [█░░░░][░░░░░][░░░░░]
T+3     Layer 1 complete               [█░░░░][█░░░░][█░░░░]
T+6     Layer 2 complete               [██░░░][██░░░][██░░░]
T+9     Layer 3 complete               [███░░][███░░][███░░]
T+10    🔔 Trigger: FIRST at 4         [████░][░░░░░][░░░░░]
T+11    Start provisioning             → engine_3 starting...
T+12    Layer 4 complete               [████░][████░][████░]
T+15    Layer 5 fills (buffer)         [█████][█████][█████]
T+20    ✅ engine_3 ready              [█████][█████][█████][░░░░░]
T+21    Stream 16 uses engine_3        [█████][█████][█████][█░░░░]
```

**Key Difference:** After implementation has engine_3 ready at T+20, before all engines are full, preventing capacity gaps.

---

## Testing Evidence

### Test: Layer-Based Filling
```bash
$ python tests/test_engine_selection_sequential.py

✅ Layer 1 complete: all engines have 1 stream
✅ Layer 2 complete: all engines have 2 streams
✅ Layer 3 complete: all engines have 3 streams
✅ Layer 4 complete: all engines have 4 streams
✅ All engines at layer 4 (MAX_STREAMS - 1 = 4)
   Now ready for new engine provisioning when autoscaler runs
```

### Demo: Visual Representation
```bash
$ python tests/demo_sequential_filling.py

Layer 1: ✓ Complete
  engine_0: [█░░░░] 1/5
  engine_1: [█░░░░] 1/5
  engine_2: [█░░░░] 1/5

Layer 2: ✓ Complete
  engine_0: [██░░░] 2/5
  engine_1: [██░░░] 2/5
  engine_2: [██░░░] 2/5

... (continues)
```

---

## Configuration

Control the behavior with environment variables:

```bash
# Number of streams per engine before considering it "full"
# Lower = more frequent provisioning, higher = more capacity per engine
ACEXY_MAX_STREAMS_PER_ENGINE=5

# Minimum engines on startup
MIN_REPLICAS=2

# Keep this many free engines during runtime (instant capacity)
# Higher = better for flash crowds, lower = more resource efficient
MIN_FREE_REPLICAS=1

# Maximum engines (when using VPN)
MAX_REPLICAS=6
```

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Strategy** | Sequential (one at a time) | Layer-based (round-robin) |
| **Load balance** | ❌ Unbalanced | ✅ Balanced |
| **Provisioning** | Wait for ALL at MAX-1 | Trigger on FIRST at MAX-1 |
| **Buffer time** | Minimal | 3x stream intervals |
| **Capacity gaps** | Likely | Prevented |
| **Flash crowd handling** | ❌ Poor | ✅ Good (with MIN_FREE_REPLICAS) |
| **Resource efficiency** | ❌ Poor (hotspots) | ✅ Good (even distribution) |

**Result:** High-density, high-availability strategy that handles predictable linear growth efficiently while preventing capacity gaps.
