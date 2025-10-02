# Troubleshooting Acexy-Orchestrator Integration

This guide helps diagnose and fix communication issues between Acexy and Orchestrator, particularly under high load.

## Common Symptoms

### 1. "Communication Between Services Fails Under Stress"

**Symptoms:**
- Timeout errors in Acexy logs
- 500/503 errors from Orchestrator
- Streams fail to start
- Provisioning requests fail

**Root Causes:**

#### A. Resource Exhaustion

**Check Docker Resources:**
```bash
# Check Docker disk usage
docker system df

# Check running containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.CPUPerc}}\t{{.MemUsage}}"

# Check Docker daemon load
docker info | grep -A 5 "Server Version"
```

**Check System Resources:**
```bash
# CPU and memory
top

# File descriptors
lsof | wc -l
ulimit -n

# Network connections
netstat -an | grep ESTABLISHED | wc -l
```

**Solution:**
```yaml
# docker-compose.yml - Increase resources
services:
  orchestrator:
    deploy:
      resources:
        limits:
          memory: 2G      # Up from 512M
          cpus: '2'       # Up from 1
        reservations:
          memory: 1G
          cpus: '1'
  
  docker:
    deploy:
      resources:
        limits:
          memory: 4G      # Docker needs more memory
          cpus: '4'
```

#### B. Aggressive Timeouts

**Check Current Timeouts:**
```bash
# Acexy timeouts
echo $ACEXY_ORCH_TIMEOUT  # Default: 3s

# Orchestrator timeouts
echo $API_REQUEST_TIMEOUT_S  # Default: 30s
echo $PROVISION_TIMEOUT_S     # Default: 120s
```

**Solution:**
```bash
# .env - Increase timeouts for Acexy
ACEXY_ORCH_TIMEOUT=10s

# .env - Increase timeouts for Orchestrator
API_REQUEST_TIMEOUT_S=60
API_LONG_OPERATION_TIMEOUT_S=120
PROVISION_TIMEOUT_S=180
PROVISION_STARTUP_GRACE_S=45
```

#### C. Rate Limiting Too Restrictive

**Check Current Limits:**
```bash
# View current rate limits
curl -H "Authorization: Bearer $API_KEY" http://localhost:8000/health/ready
```

**Solution:**
```bash
# .env - Increase rate limits
MAX_CONCURRENT_PROVISIONS=10      # Up from 5
MIN_PROVISION_INTERVAL_S=0.2      # Down from 0.5

# Also consider circuit breaker
CIRCUIT_BREAKER_THRESHOLD=10      # Up from 5
CIRCUIT_BREAKER_TIMEOUT_S=300     # 5 minutes
```

### 2. "Engines Not Available When Needed"

**Symptoms:**
- Acexy reports "no engines available"
- Provisioning takes too long
- All engines at capacity

**Root Causes:**

#### A. Insufficient Minimum Replicas

**Check Current Settings:**
```bash
echo $MIN_REPLICAS  # How many free engines to maintain
```

**Solution:**
```bash
# .env - Increase minimum free engines
MIN_REPLICAS=5     # Up from 3

# Also tune autoscaling
AUTOSCALE_INTERVAL_S=20     # Down from 30
ENGINE_GRACE_PERIOD_S=45    # Up from 30
```

#### B. Slow Provisioning

**Diagnose:**
```bash
# Time a provisioning request
time curl -X POST -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  http://localhost:8000/provision/acestream

# Check provisioning metrics
curl http://localhost:8000/metrics | grep orch_provision
```

**Solution:**
```bash
# Pre-provision engines during low load
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/scale/10

# Or increase MIN_REPLICAS to keep more ready
MIN_REPLICAS=10
```

#### C. Unhealthy Engines

**Check Engine Health:**
```bash
# List engines and their health status
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/engines | jq '.[] | {id: .container_id, health: .health_status, streams: .streams | length}'
```

**Solution:**
```bash
# Delete unhealthy engines manually
curl -X DELETE -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/containers/{container_id}

# Or run garbage collection
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/gc

# Tune health monitoring
HEALTH_CHECK_INTERVAL_S=20     # Down from 30
HEALTH_CHECK_TIMEOUT_S=10      # Timeout for health checks
```

### 3. "High Latency in API Responses"

**Symptoms:**
- Slow response from `/engines` endpoint
- Slow provisioning
- Acexy waiting too long for responses

**Diagnose:**
```bash
# Measure API latency
time curl -H "Authorization: Bearer $API_KEY" http://localhost:8000/engines
time curl -H "Authorization: Bearer $API_KEY" http://localhost:8000/streams

# Check Prometheus metrics
curl http://localhost:8000/metrics | grep orch_api_duration
```

**Root Causes:**

#### A. Too Many Engines to Query

**Solution:**
```bash
# Implement caching in Acexy (see INTEGRATION_IMPROVEMENTS.md)
# Cache engine list for 5 seconds
# Use ETags for conditional requests

# Reduce query frequency
ACEXY_ENGINE_CACHE_TTL_S=5     # Cache engines for 5s
```

#### B. Slow Database Queries

**Diagnose:**
```bash
# Check database size
ls -lh orchestrator.db

# Check if vacuum is needed
sqlite3 orchestrator.db "PRAGMA integrity_check"
sqlite3 orchestrator.db "VACUUM"
```

**Solution:**
```bash
# Regular maintenance
sqlite3 orchestrator.db "VACUUM"
sqlite3 orchestrator.db "ANALYZE"

# Consider cleanup
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/gc
```

#### C. Blocking Operations

**Solution:**
```bash
# Implement async endpoints (see INTEGRATION_IMPROVEMENTS.md)
# Use background tasks for non-critical operations
# Return 202 Accepted for events instead of 200 OK
```

### 4. "Circuit Breaker Opens Frequently"

**Symptoms:**
- Orchestrator stops accepting provision requests
- "Circuit breaker open" messages in logs
- No new engines provisioned

**Diagnose:**
```bash
# Check circuit breaker status
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/health/ready | jq '.circuit_state'

# Check failure count
curl http://localhost:8000/metrics | grep circuit_breaker
```

**Root Causes:**

#### A. Docker Daemon Overloaded

**Solution:**
```bash
# Restart Docker daemon
systemctl restart docker

# Or in docker-compose
docker-compose restart docker

# Increase Docker resources (see section 1A)
```

#### B. Circuit Breaker Too Sensitive

**Solution:**
```bash
# .env - Tune circuit breaker
CIRCUIT_BREAKER_THRESHOLD=10      # More failures before opening
CIRCUIT_BREAKER_TIMEOUT_S=300     # 5 min recovery time
```

### 5. "VPN Connection Issues"

**Symptoms:**
- Engines can't connect to internet
- Slow stream startup
- Intermittent failures

**Diagnose:**
```bash
# Check VPN status
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/vpn/status

# Check Gluetun container
docker logs gluetun --tail 50

# Test connectivity from engine
docker exec <engine_container> curl -I https://www.google.com
```

**Solution:**
```bash
# Restart Gluetun if unhealthy
docker-compose restart gluetun

# Increase Gluetun health check tolerance
# docker-compose.gluetun.yml
healthcheck:
  interval: 30s
  timeout: 10s
  retries: 5      # Up from 3

# Increase port cache TTL
GLUETUN_PORT_CACHE_TTL_S=120     # Up from 60
```

## Monitoring Checklist

### Before High Load

```bash
# 1. Verify services are healthy
curl http://localhost:8000/health
curl http://localhost:8080/ace/status

# 2. Check resource availability
docker system df
docker stats --no-stream

# 3. Pre-provision engines
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/scale/$EXPECTED_CONCURRENT_STREAMS

# 4. Check circuit breaker is closed
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/health/ready | jq '.circuit_state'

# 5. Verify VPN is healthy (if using)
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/vpn/status
```

### During High Load

```bash
# Monitor API latency
watch -n 5 'curl -s http://localhost:8000/metrics | grep orch_api_duration_seconds'

# Monitor active streams
watch -n 5 'curl -s -H "Authorization: Bearer $API_KEY" http://localhost:8000/streams?status=started | jq "length"'

# Monitor engine health
watch -n 5 'curl -s -H "Authorization: Bearer $API_KEY" http://localhost:8000/engines | jq "[.[] | .health_status] | group_by(.) | map({status: .[0], count: length})"'

# Monitor Docker resources
watch -n 5 'docker stats --no-stream'
```

### After High Load

```bash
# Clean up idle engines
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/gc

# Check for errors
docker-compose logs orchestrator | grep ERROR
docker-compose logs acexy | grep ERROR

# Verify no stuck containers
docker ps -a --filter "status=exited"

# Check database size
ls -lh orchestrator.db
```

## Performance Tuning Matrix

| Load Level | MIN_REPLICAS | MAX_CONCURRENT_PROVISIONS | PROVISION_INTERVAL_S | TIMEOUTS |
|-----------|--------------|--------------------------|---------------------|----------|
| **Light** (1-10 streams) | 2 | 3 | 1.0 | 30s |
| **Medium** (10-50 streams) | 5 | 5 | 0.5 | 45s |
| **High** (50-100 streams) | 10 | 8 | 0.3 | 60s |
| **Very High** (100+ streams) | 20 | 10 | 0.2 | 90s |

## Configuration Templates

### High Availability Setup

```bash
# .env for HA
# Aggressive provisioning
MIN_REPLICAS=10
MAX_CONCURRENT_PROVISIONS=10
MIN_PROVISION_INTERVAL_S=0.2

# Quick detection
HEALTH_CHECK_INTERVAL_S=15
MONITOR_INTERVAL_S=5

# Short grace periods
ENGINE_GRACE_PERIOD_S=20
AUTOSCALE_INTERVAL_S=20

# Generous timeouts
API_REQUEST_TIMEOUT_S=60
PROVISION_TIMEOUT_S=180

# Resilient circuit breaker
CIRCUIT_BREAKER_THRESHOLD=10
CIRCUIT_BREAKER_TIMEOUT_S=300
```

### Resource-Constrained Setup

```bash
# .env for limited resources
# Conservative provisioning
MIN_REPLICAS=3
MAX_CONCURRENT_PROVISIONS=3
MIN_PROVISION_INTERVAL_S=1.5

# Less frequent checks
HEALTH_CHECK_INTERVAL_S=60
MONITOR_INTERVAL_S=15

# Longer grace periods
ENGINE_GRACE_PERIOD_S=60
AUTOSCALE_INTERVAL_S=60

# Very generous timeouts
API_REQUEST_TIMEOUT_S=120
PROVISION_TIMEOUT_S=300

# Sensitive circuit breaker
CIRCUIT_BREAKER_THRESHOLD=3
CIRCUIT_BREAKER_TIMEOUT_S=600
```

### Balanced Setup (Recommended)

```bash
# .env for balanced performance
# Moderate provisioning
MIN_REPLICAS=5
MAX_CONCURRENT_PROVISIONS=5
MIN_PROVISION_INTERVAL_S=0.5

# Standard monitoring
HEALTH_CHECK_INTERVAL_S=30
MONITOR_INTERVAL_S=10

# Standard grace periods
ENGINE_GRACE_PERIOD_S=30
AUTOSCALE_INTERVAL_S=30

# Reasonable timeouts
API_REQUEST_TIMEOUT_S=45
PROVISION_TIMEOUT_S=150

# Moderate circuit breaker
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT_S=300
```

## Emergency Procedures

### Complete System Reset

```bash
# 1. Stop all services
docker-compose down

# 2. Clean up containers
docker rm -f $(docker ps -aq --filter "label=acestream-orchestrator")

# 3. Clean up images (optional)
docker image prune -f

# 4. Clean up volumes (WARNING: loses database)
docker volume prune -f

# 5. Restart services
docker-compose up -d

# 6. Verify health
curl http://localhost:8000/health
curl http://localhost:8080/ace/status
```

### Partial Reset (Keep Database)

```bash
# 1. Stop orchestrator
docker-compose stop orchestrator

# 2. Clean up managed containers
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/gc || docker rm -f $(docker ps -aq --filter "label=acestream-orchestrator")

# 3. Restart orchestrator
docker-compose start orchestrator

# 4. Verify it reindexed
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/engines
```

### Rolling Restart (Zero Downtime)

```bash
# If running multiple Acexy instances:

# 1. Stop acexy-1
docker-compose stop acexy-1

# 2. Update configuration
# Edit .env or docker-compose.yml

# 3. Start acexy-1
docker-compose up -d acexy-1

# 4. Verify health
curl http://acexy-1:8080/ace/status

# 5. Repeat for acexy-2, acexy-3, etc.

# 6. Finally, restart orchestrator (services can fall back during restart)
docker-compose restart orchestrator
```

## Getting Help

### Collect Diagnostic Information

```bash
#!/bin/bash
# Save as: collect_diagnostics.sh

echo "=== System Information ==="
date
uname -a
docker version
docker-compose version

echo -e "\n=== Docker Resources ==="
docker system df
docker stats --no-stream

echo -e "\n=== Services Status ==="
docker-compose ps

echo -e "\n=== Orchestrator Health ==="
curl -s http://localhost:8000/health || echo "Orchestrator not responding"

echo -e "\n=== Engines ==="
curl -s -H "Authorization: Bearer $API_KEY" http://localhost:8000/engines | jq

echo -e "\n=== Active Streams ==="
curl -s -H "Authorization: Bearer $API_KEY" http://localhost:8000/streams?status=started | jq

echo -e "\n=== Metrics ==="
curl -s http://localhost:8000/metrics | grep -E "orch_|circuit_"

echo -e "\n=== Recent Logs ==="
echo "--- Orchestrator ---"
docker-compose logs --tail=50 orchestrator

echo -e "\n--- Acexy ---"
docker-compose logs --tail=50 acexy 2>/dev/null || echo "Acexy not in compose"

echo -e "\n--- Docker Daemon ---"
docker-compose logs --tail=50 docker
```

Run and share output:
```bash
chmod +x collect_diagnostics.sh
./collect_diagnostics.sh > diagnostics_$(date +%Y%m%d_%H%M%S).txt
```

### Enable Debug Logging

```bash
# .env - Enable debug logging
LOG_LEVEL=DEBUG
ACEXY_LOG_LEVEL=DEBUG

# Restart services
docker-compose restart
```

### Watch Live Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f orchestrator

# Filter for errors
docker-compose logs -f orchestrator | grep ERROR

# Multiple services
docker-compose logs -f orchestrator acexy
```

## Summary

Most "communication failures under stress" are due to:

1. ✅ **Resource exhaustion** - Solution: Increase Docker/system resources
2. ✅ **Aggressive timeouts** - Solution: Tune timeout configuration
3. ✅ **Rate limiting** - Solution: Increase concurrent provisions
4. ✅ **Insufficient free engines** - Solution: Increase MIN_REPLICAS
5. ✅ **Docker daemon overload** - Solution: Add resources, enable throttling

**Not due to:**
- ❌ Architecture (separate services is optimal)
- ❌ HTTP overhead (negligible ~10ms per stream)
- ❌ Missing features (all necessary features exist)

**Action Items:**
1. Start with "Balanced Setup" configuration
2. Monitor performance during load
3. Tune based on actual bottlenecks
4. Implement improvements from `INTEGRATION_IMPROVEMENTS.md`

**Remember:** The architecture is sound. Focus on operational tuning, not architectural changes.
