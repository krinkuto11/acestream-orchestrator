# Forwarded Engine Implementation

## Overview

This document describes the implementation of the "forwarded engine" feature for Gluetun VPN port forwarding management.

## Problem Statement

When using Gluetun VPN with port forwarding, only one engine can actually use the forwarded port for P2P connectivity. However, the previous implementation assigned the same forwarded port to ALL engines, which was:
1. Incorrect - only one engine can bind to the port
2. Confusing - no way to identify which engine has the port
3. Inefficient - proxies couldn't prioritize the engine with proper P2P connectivity

## Solution

### Core Concept

Mark exactly ONE engine as "forwarded" when using Gluetun. This engine:
- Receives the P2P port from Gluetun
- Is labeled with `acestream.forwarded=true`
- Shows a "FORWARDED" badge in the UI
- Is tracked in the database and state

### Implementation Details

#### 1. Database Schema Changes

**File:** `app/models/db_models.py`

Added `forwarded` boolean field to `EngineRow`:
```python
forwarded: Mapped[bool] = mapped_column(Boolean, default=False)
```

#### 2. API Schema Changes

**File:** `app/models/schemas.py`

Added `forwarded` field to `EngineState`:
```python
forwarded: bool = False
```

#### 3. Provisioner Logic

**File:** `app/services/provisioner.py`

Key changes:
- Check if a forwarded engine already exists before provisioning
- Only assign P2P port to the first engine (no forwarded engine exists)
- Add `FORWARDED_LABEL` constant for container labeling
- Set `acestream.forwarded=true` label on forwarded containers

```python
# Determine if this engine should be the forwarded engine
is_forwarded = False
p2p_port = None
if cfg.GLUETUN_CONTAINER_NAME:
    from .state import state
    if not state.has_forwarded_engine():
        is_forwarded = True
        from .gluetun import get_forwarded_port_sync
        p2p_port = get_forwarded_port_sync()
```

#### 4. State Management

**File:** `app/services/state.py`

Added methods to manage forwarded status:
- `set_forwarded_engine(container_id)` - Mark an engine as forwarded
- `get_forwarded_engine()` - Get the forwarded engine
- `has_forwarded_engine()` - Check if forwarded engine exists
- `remove_engine()` - Updated to handle forwarded engine removal

When a forwarded engine is removed:
- The autoscaler will automatically provision a new engine
- That new engine becomes the forwarded one (since none exists)

#### 5. Reindex Logic

**File:** `app/services/reindex.py`

Updated to:
- Read `FORWARDED_LABEL` from container labels
- Restore forwarded status when reindexing containers
- Call `state.set_forwarded_engine()` for forwarded containers

#### 6. UI Changes

**File:** `app/static/panel-react/src/components/EngineList.jsx`

Added visual indicator:
- "FORWARDED" chip/badge displayed next to engine name
- Uses primary color to stand out
- Only shown when `engine.forwarded === true`

### Behavior

#### Normal Operation (with Gluetun)

1. **Startup:**
   - Orchestrator provisions `MIN_REPLICAS` engines
   - First engine becomes forwarded, receives P2P port
   - Subsequent engines do NOT receive P2P port

2. **Runtime:**
   - Always exactly one forwarded engine exists
   - Forwarded status visible in UI and API responses
   - Proxies can prioritize the forwarded engine

3. **Engine Deletion:**
   - If forwarded engine is deleted
   - Autoscaler provisions replacement
   - New engine becomes forwarded

#### Without Gluetun

When `GLUETUN_CONTAINER_NAME` is not configured:
- No engines are marked as forwarded
- All engines provision normally without P2P ports
- Feature is effectively disabled

### API Impact

The `/engines` endpoint now includes the `forwarded` field:

```json
{
  "container_id": "abc123",
  "container_name": "acestream-1",
  "host": "gluetun",
  "port": 19000,
  "forwarded": true,  // ← New field
  "health_status": "healthy",
  "streams": [],
  ...
}
```

### Labels

Forwarded engines have this Docker label:
```
acestream.forwarded=true
```

This label:
- Persists with the container
- Is read during reindex
- Can be queried with Docker commands

### Testing

#### Unit Tests

**File:** `tests/test_forwarded_engine.py`

Tests cover:
- ✓ Forwarded flag in EngineState schema
- ✓ Default value (False)
- ✓ Setting forwarded engine in state
- ✓ Clearing previous forwarded when setting new one
- ✓ Checking if forwarded engine exists
- ✓ Label constant value
- ✓ Forwarded engine removal behavior

All tests pass successfully.

#### Manual Testing

**File:** `tests/manual_test_forwarded.py`

Interactive script to verify:
1. VPN status and forwarded port
2. Engine listing with forwarded status
3. Provisioning new engines
4. Forwarded status distribution
5. Behavior when forwarded engine is deleted

**Usage:**
```bash
# Set environment if needed
export ORCHESTRATOR_URL="http://localhost:8000"
export API_KEY="your-api-key"

# Run the test
python tests/manual_test_forwarded.py
```

### Migration Notes

#### Database Migration

The `forwarded` column is added to the `engines` table with a default value of `False`. 

When upgrading:
1. Existing engines will have `forwarded=False`
2. On first reindex, the correct forwarded engine will be identified
3. No manual intervention required

#### Backward Compatibility

- API remains backward compatible
- New `forwarded` field is optional in responses
- Existing clients can ignore the field
- No breaking changes

### Validation Checklist

To verify the implementation works correctly:

- [ ] Only one engine shows `forwarded=true` at a time
- [ ] Forwarded engine receives P2P port from Gluetun
- [ ] Non-forwarded engines do NOT receive P2P port
- [ ] UI displays "FORWARDED" badge correctly
- [ ] Forwarded status persists across orchestrator restarts
- [ ] When forwarded engine is deleted, autoscaler creates new one
- [ ] New engine becomes forwarded (since none exists)
- [ ] `/engines` API includes forwarded field
- [ ] Container has `acestream.forwarded=true` label
- [ ] Reindex correctly restores forwarded status

### Future Enhancements

Potential improvements:
1. **Manual override:** Allow manually designating which engine should be forwarded
2. **Metrics:** Track P2P connectivity quality of forwarded engine
3. **Rotation:** Periodically rotate which engine is forwarded for load balancing
4. **Health-based selection:** Choose healthiest engine to be forwarded

### Troubleshooting

#### No engine is forwarded

**Symptom:** All engines show `forwarded=false`

**Possible causes:**
- Gluetun is not configured (`GLUETUN_CONTAINER_NAME` not set)
- Gluetun is not providing a forwarded port
- State was not properly initialized

**Solution:**
1. Check VPN status: `GET /vpn/status`
2. Verify `forwarded_port` is not null
3. Trigger reindex: Restart orchestrator or delete/recreate engines

#### Multiple engines are forwarded

**Symptom:** More than one engine shows `forwarded=true`

**Possible causes:**
- Race condition during concurrent provisioning
- State corruption

**Solution:**
1. Restart orchestrator (will trigger reindex)
2. Check logs for errors in `set_forwarded_engine()`
3. Manually fix via database if needed

#### Forwarded engine has no P2P port

**Symptom:** Engine marked as forwarded but no P2P connectivity

**Possible causes:**
- P2P port not retrieved from Gluetun
- Container started before port was available

**Solution:**
1. Check Gluetun logs for port forwarding issues
2. Restart the forwarded engine
3. Check engine environment variables for `P2P_PORT`

### References

- Original Issue: Problem statement in PR description
- Gluetun Documentation: https://github.com/qdm12/gluetun
- AceStream Port Documentation: Engine variant docs

---

**Status:** ✅ Implemented and tested
**Version:** 1.0
**Date:** 2025-10-30
