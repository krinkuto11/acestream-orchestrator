# Testing Guide for Orchestrator-Acexy Integration

## Quick Start

This guide helps you test the orchestrator-acexy integration improvements on your server.

## Prerequisites

- Docker and docker-compose installed
- Orchestrator deployed
- (Optional) Gluetun VPN container running

## Test 1: Verify Orchestrator is Running

```bash
# Check if orchestrator is accessible
curl http://localhost:8000/engines

# Expected: JSON array of engines (may be empty)
```

## Test 2: Check New Status Endpoint

```bash
# Get comprehensive orchestrator status
curl http://localhost:8000/orchestrator/status | jq

# Expected output:
# {
#   "status": "healthy" or "degraded",
#   "engines": { ... },
#   "vpn": { ... },
#   "provisioning": {
#     "can_provision": true/false,
#     "blocked_reason": null or "..."
#   }
# }
```

**Check:**
- ✅ `status` is "healthy" if engines are running
- ✅ `provisioning.can_provision` is true
- ✅ If using VPN, `vpn.connected` is true

## Test 3: Test Provisioning (Critical!)

This is the most important test - it verifies the main fix.

```bash
# Provision a new engine
curl -X POST http://localhost:8000/provision/acestream \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"labels": {"test": "manual"}, "env": {}}' | jq

# Expected output:
# {
#   "container_id": "abc123...",
#   "container_name": "acestream-1",
#   "host_http_port": 19000,
#   "container_http_port": 40000,
#   "container_https_port": 45000
# }
```

**Note the container_id**, then immediately check:

```bash
# Check if engine appears in state (should be immediate!)
curl http://localhost:8000/engines | jq

# Expected: Engine with matching container_id is in the list
```

**Critical Success:** If you see the engine in the list, the main fix is working! ✅

## Test 4: Verify Docker Container

```bash
# Check if container is actually running
docker ps | grep acestream

# Expected: Container with matching ID is running
```

## Test 5: Automated Test Script

Run the comprehensive test script:

```bash
cd tests
./test_provision_behavior.sh
```

**Expected output:**
```
✅ Orchestrator is accessible
✅ Status endpoint accessible
✅ Provisioning successful
✅ CRITICAL: Engine appeared in state
✅ Docker container is running
```

If **Test 5** shows "CRITICAL: Engine appeared in state" ✅, you're good to go!

## Test 6: VPN Status (If Using Gluetun)

```bash
# Check VPN status
curl http://localhost:8000/vpn/status | jq

# Expected:
# {
#   "enabled": true,
#   "connected": true,
#   "health": "healthy",
#   "forwarded_port": 12345
# }
```

**If VPN is unhealthy:**
```bash
# Check Gluetun container
docker logs gluetun --tail 50

# Restart if needed
docker restart gluetun

# Wait 30 seconds for reconnection
sleep 30

# Check status again
curl http://localhost:8000/vpn/status | jq
```

## Test 7: Test with Acexy (Optional)

If you have acexy configured:

1. **Configure acexy:**
   ```bash
   export ACEXY_ORCH_URL=http://orchestrator:8000
   export ACEXY_ORCH_APIKEY=YOUR_API_KEY
   export ACEXY_MAX_STREAMS_PER_ENGINE=1
   ```

2. **Request a stream:**
   ```bash
   curl "http://localhost:6878/ace/getstream?id=SOME_CONTENT_ID"
   ```

3. **Check orchestrator logs:**
   ```bash
   docker logs orchestrator --tail 50
   
   # Look for:
   # - "Reindexed after provisioning engine"
   # - Stream started/ended events
   ```

4. **Verify stream in orchestrator:**
   ```bash
   curl http://localhost:8000/streams?status=started | jq
   ```

## Common Issues and Solutions

### Issue: "Orchestrator is not accessible"

**Solution:**
```bash
# Check if orchestrator is running
docker ps | grep orchestrator

# Check logs
docker logs orchestrator --tail 100

# Restart if needed
docker restart orchestrator
```

### Issue: "Provisioning failed with 503"

**Check status:**
```bash
curl http://localhost:8000/orchestrator/status | jq '.provisioning'
```

**If blocked_reason is "VPN not connected":**
```bash
# Check and fix Gluetun
docker logs gluetun --tail 50
docker restart gluetun
```

**If blocked_reason is "Circuit breaker is open":**
```bash
# Reset circuit breaker
curl -X POST http://localhost:8000/health/circuit-breaker/reset \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Issue: "Engine not in state after provisioning"

This means the fix didn't apply correctly.

**Verify fix is applied:**
```bash
# Check orchestrator logs for reindex message
docker logs orchestrator --tail 100 | grep -i reindex

# Should see: "Reindexed after provisioning engine abc123"
```

**If not seeing reindex messages:**
1. Pull latest orchestrator image
2. Rebuild: `docker-compose build orchestrator`
3. Restart: `docker-compose up -d orchestrator`
4. Test again

### Issue: "Container created but not running"

**Check container logs:**
```bash
# Find container ID from provisioning response
docker logs CONTAINER_ID --tail 50
```

**Common causes:**
- Image not found: Pull the image manually
- Port conflict: Check if port is already in use
- VPN not ready: Ensure Gluetun is healthy

## Success Criteria

All tests pass if:

1. ✅ Orchestrator status endpoint is accessible
2. ✅ Provisioning creates a container
3. ✅ **CRITICAL:** Engine appears in `/engines` immediately after provisioning
4. ✅ Docker container is running
5. ✅ VPN is healthy (if configured)
6. ✅ Test script shows all green checkmarks

## What to Send for Support

If you encounter issues, provide:

1. **Orchestrator status:**
   ```bash
   curl http://localhost:8000/orchestrator/status | jq > orchestrator_status.json
   ```

2. **Orchestrator logs:**
   ```bash
   docker logs orchestrator --tail 200 > orchestrator_logs.txt
   ```

3. **Test script output:**
   ```bash
   ./tests/test_provision_behavior.sh > test_output.txt 2>&1
   ```

4. **Docker containers:**
   ```bash
   docker ps -a | grep acestream > containers.txt
   ```

5. **Environment:**
   ```bash
   docker exec orchestrator env | grep -E "(MIN_REPLICAS|MAX_REPLICAS|TARGET_IMAGE|GLUETUN)" > env.txt
   ```

## Next Steps After Testing

Once all tests pass:

1. ✅ Deploy to production
2. ✅ Configure acexy with orchestrator URL
3. ✅ Set up monitoring on `/orchestrator/status`
4. ✅ Configure alerts for VPN failures
5. ✅ Test with real streams
6. ✅ Monitor performance

## Quick Reference

| Endpoint | Purpose | Auth Required |
|----------|---------|---------------|
| `GET /engines` | List engines | No |
| `GET /orchestrator/status` | Comprehensive status | No |
| `GET /vpn/status` | VPN status | No |
| `POST /provision/acestream` | Provision engine | Yes |
| `GET /health/status` | Health details | No |
| `POST /health/circuit-breaker/reset` | Reset circuit breaker | Yes |
| `GET /streams?status=started` | Active streams | No |

## Support

For detailed information, see:
- `docs/ACEXY_INTEGRATION.md` - Complete integration guide
- `docs/INTEGRATION_SUMMARY.md` - Summary of changes
- `docs/TROUBLESHOOTING.md` - General troubleshooting

---

**Remember:** The most critical test is verifying that provisioned engines appear in `/engines` immediately. This was the main bug that was fixed!
