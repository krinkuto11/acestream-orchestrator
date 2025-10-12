# Orchestrator-Acexy Integration Summary

## What Was Fixed

### Critical Issue: Provisioning State Synchronization ❌ → ✅

**Problem:**
- Acexy proxy calls `/provision/acestream` to create a new engine
- Orchestrator creates Docker container successfully
- **BUT** the engine is NOT added to internal state
- Acexy waits 10 seconds and calls `/engines` to find the engine
- **Engine is not in the list!** ❌
- Acexy cannot use the newly provisioned engine

**Root Cause:**
The `/provision/acestream` endpoint called `start_acestream()` which creates the container, but never added it to the state dictionary. Only `ensure_minimum()` and `scale_to()` called `reindex_existing()` to sync state.

**Solution:**
```python
@app.post("/provision/acestream", response_model=AceProvisionResponse)
def provision_acestream(req: AceProvisionRequest):
    response = start_acestream(req)
    
    # ✅ CRITICAL FIX: Immediately add engine to state
    reindex_existing()
    
    return response
```

**Result:** Engine appears in `/engines` endpoint immediately after provisioning! ✅

---

## What Was Added

### 1. Comprehensive Status Endpoint

**Endpoint:** `GET /orchestrator/status`

**Purpose:** Gives acexy full visibility into orchestrator state

**Response:**
```json
{
  "status": "healthy",
  "engines": {
    "total": 3,
    "running": 3,
    "healthy": 2,
    "unhealthy": 1
  },
  "streams": {
    "active": 5,
    "total": 10
  },
  "capacity": {
    "total": 3,
    "used": 5,
    "available": -2,
    "max_replicas": 10,
    "min_replicas": 1
  },
  "vpn": {
    "enabled": true,
    "connected": true,
    "health": "healthy",
    "forwarded_port": 54321
  },
  "provisioning": {
    "can_provision": true,
    "circuit_breaker_state": "closed",
    "blocked_reason": null
  }
}
```

**Use Case:** Acexy can check this before attempting to provision:
```go
func (c *orchClient) checkCanProvision() bool {
    status := c.getStatus()
    return status.Provisioning.CanProvision
}
```

### 2. Improved Error Handling

**Before:**
```
POST /provision/acestream → 500 Internal Server Error
```

**After:**
```
POST /provision/acestream → 503 Service Unavailable
{
  "detail": "Cannot provision engine: VPN not available - Gluetun VPN container 'gluetun' is not healthy"
}
```

**Error Codes:**
- `503`: VPN not available (temporary failure, retry later)
- `503`: Circuit breaker open (temporary failure, retry later)
- `500`: Permanent error (image not found, configuration issue)

### 3. VPN Status Integration

**Status includes VPN awareness:**
```json
{
  "provisioning": {
    "can_provision": false,
    "blocked_reason": "VPN not connected"
  }
}
```

**Provisioning checks VPN:**
- If VPN is configured and unhealthy → Return 503
- If VPN is disabled → Proceed normally
- If VPN is healthy → Proceed normally

### 4. Complete Documentation

**Created:**
- `docs/ACEXY_INTEGRATION.md` - Full integration guide
- `tests/test_orchestrator_acexy_integration.py` - Integration test
- `tests/test_provision_behavior.sh` - Manual test script
- `tests/test_vpn_failure_handling.py` - VPN failure scenarios

---

## Testing Your Changes

### Quick Test (Local)

Run the integration test:
```bash
python3 tests/test_orchestrator_acexy_integration.py
```

**Expected Output:**
```
✅ CRITICAL SUCCESS: Provisioned engine found in state immediately!
✅ ALL INTEGRATION TESTS PASSED!
```

### Production Test (Your Server)

Run the shell script:
```bash
cd tests
./test_provision_behavior.sh
```

This will:
1. Check orchestrator accessibility
2. Test status endpoint
3. Provision a new engine
4. **Verify engine appears in state immediately** ← Critical test!
5. Verify Docker container is running
6. Clean up

### VPN Failure Test

Run the VPN test:
```bash
python3 tests/test_vpn_failure_handling.py
```

This documents all VPN failure scenarios and expected behavior.

---

## What to Test with Acexy

### Scenario 1: Normal Provisioning
1. Start orchestrator with MIN_REPLICAS=0
2. Configure acexy with orchestrator URL
3. Request stream via acexy
4. Acexy calls `/provision/acestream`
5. **Verify:** Engine appears in `/engines` immediately
6. **Verify:** Acexy successfully routes stream to engine

### Scenario 2: VPN Failure
1. Stop Gluetun container
2. Request stream via acexy
3. Acexy tries to provision
4. **Verify:** Gets 503 error
5. **Verify:** Acexy uses existing engines or returns error to client

### Scenario 3: All Engines at Capacity
1. Start with 2 engines, each with 1 stream (max_streams_per_engine=1)
2. Request 3rd stream via acexy
3. **Verify:** Acexy provisions new engine
4. **Verify:** New engine appears in `/engines` immediately
5. **Verify:** Stream routes to new engine

---

## Configuration for Production

### Orchestrator (.env)
```bash
# Minimum engines to keep running
MIN_REPLICAS=2

# Maximum engines allowed
MAX_REPLICAS=10

# Docker image for engines
TARGET_IMAGE=ghcr.io/krinkuto11/acestream-http-proxy:latest

# API key for authentication
API_KEY=your-secure-key-here

# VPN configuration (optional)
GLUETUN_CONTAINER_NAME=gluetun
VPN_RESTART_ENGINES_ON_RECONNECT=true
```

### Acexy
```bash
# Orchestrator integration
ACEXY_ORCH_URL=http://orchestrator:8000
ACEXY_ORCH_APIKEY=your-secure-key-here

# One stream per engine (recommended)
ACEXY_MAX_STREAMS_PER_ENGINE=1

# Fallback if orchestrator unavailable
ACEXY_HOST=localhost
ACEXY_PORT=6878
```

---

## Monitoring

### Check Orchestrator Health
```bash
curl http://localhost:8000/orchestrator/status
```

### Check VPN Status
```bash
curl http://localhost:8000/vpn/status
```

### Check Engines
```bash
curl http://localhost:8000/engines
```

### Check Streams
```bash
curl http://localhost:8000/streams?status=started
```

---

## Troubleshooting

### Issue: Engine not found after provisioning

**Symptoms:** Acexy provisions engine but can't find it

**Check:**
```bash
# 1. Check orchestrator logs
docker logs orchestrator | grep -i "reindex"

# 2. Verify engine in Docker
docker ps | grep acestream

# 3. Check via API
curl http://localhost:8000/engines
```

**Solution:** Ensure orchestrator has the state sync fix (this PR)

### Issue: Provisioning returns 503

**Check:**
```bash
curl http://localhost:8000/orchestrator/status
```

**Look for:**
```json
{
  "provisioning": {
    "can_provision": false,
    "blocked_reason": "VPN not connected"  // or "Circuit breaker is open"
  }
}
```

**Solutions:**
- **VPN not connected:** Check Gluetun container, restart if needed
- **Circuit breaker open:** Wait 5 minutes or reset via `/health/circuit-breaker/reset`

### Issue: Engines not responding

**Check:**
```bash
# Check engine health
curl http://localhost:8000/health/status

# Check specific engine
curl http://engine-host:port/server/api?api_version=3&method=get_status
```

---

## Performance Recommendations

1. **MAX_STREAMS_PER_ENGINE=1**: One stream per engine for best performance
2. **MIN_REPLICAS=2-3**: Keep some engines ready
3. **MAX_REPLICAS=10-20**: Depending on server capacity
4. **Monitor capacity**: Alert when capacity is low
5. **Use VPN**: For better privacy and port forwarding

---

## Next Steps

1. ✅ Deploy updated orchestrator to your server
2. ✅ Run `test_provision_behavior.sh` to verify fixes
3. ✅ Configure acexy with orchestrator integration
4. ✅ Test with actual streams
5. ✅ Monitor `/orchestrator/status` endpoint
6. ✅ Set up alerts for VPN failures
7. ✅ Review logs for any issues

---

## Summary

**Before this PR:**
- ❌ Provisioned engines didn't appear in state
- ❌ Acexy couldn't use newly provisioned engines
- ❌ No visibility into orchestrator health
- ❌ Poor error messages on failures

**After this PR:**
- ✅ Engines appear in state immediately after provisioning
- ✅ Acexy can use engines right away (10s wait is sufficient)
- ✅ Comprehensive status endpoint for health checks
- ✅ Clear error messages with proper HTTP codes
- ✅ VPN status awareness
- ✅ Complete documentation and tests

**Result:** Production-ready orchestrator-acexy integration! 🚀
