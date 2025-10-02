# Quick Reference: Acexy-Orchestrator Integration

## TL;DR

**Question:** Should Acexy and Orchestrator be merged?

**Answer:** ❌ **NO** - Keep them separate for optimal performance, reliability, and scalability.

## One-Minute Summary

### Current Architecture (Optimal)
```
Clients → Acexy (Go proxy) → Orchestrator (Python API) → Docker → AceStream Engines
          ↓ Fast streaming    ↓ Smart management
          ↓ Low latency       ↓ Health monitoring
          ↓ Multiplexing      ↓ Auto-scaling
```

### Why This Works
- ✅ Go proxy: 10-50x faster for streaming
- ✅ Python orchestrator: Rich Docker ecosystem
- ✅ Independent scaling: Efficient resource use
- ✅ Fault isolation: Failures contained
- ✅ HTTP overhead: Only ~10ms per stream

### Why Not Merge
- ❌ Would lose Go's performance OR Python's ecosystem
- ❌ Single point of failure (everything crashes together)
- ❌ Coupled scaling (resource waste)
- ❌ 2-6 weeks development time
- ❌ Gains only ~10ms per stream

## Common Issues & Quick Fixes

### Issue: "Communication fails under stress"

**Root Cause:** Resource exhaustion or aggressive timeouts, NOT architecture

**Quick Fix:**
```bash
# 1. Increase timeouts in .env
ACEXY_ORCH_TIMEOUT=10s
API_REQUEST_TIMEOUT_S=60
PROVISION_TIMEOUT_S=180

# 2. Increase rate limits
MAX_CONCURRENT_PROVISIONS=10
MIN_PROVISION_INTERVAL_S=0.2

# 3. Increase resources
docker-compose.yml:
  orchestrator:
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '2'

# 4. Restart services
docker-compose restart
```

### Issue: "Not enough engines available"

**Quick Fix:**
```bash
# Increase minimum free engines
MIN_REPLICAS=5  # or higher for your load

# Pre-provision before load
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/scale/10
```

### Issue: "Engines unhealthy"

**Quick Fix:**
```bash
# Check health
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/engines | jq '.[] | {id: .container_id, health: .health_status}'

# Run garbage collection
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/gc
```

## Quick Configuration Templates

### For 10-50 Streams (Recommended Start)
```bash
MIN_REPLICAS=5
MAX_CONCURRENT_PROVISIONS=5
MIN_PROVISION_INTERVAL_S=0.5
API_REQUEST_TIMEOUT_S=45
HEALTH_CHECK_INTERVAL_S=30
```

### For 50-100 Streams
```bash
MIN_REPLICAS=10
MAX_CONCURRENT_PROVISIONS=8
MIN_PROVISION_INTERVAL_S=0.3
API_REQUEST_TIMEOUT_S=60
HEALTH_CHECK_INTERVAL_S=20
```

### For 100+ Streams
```bash
MIN_REPLICAS=20
MAX_CONCURRENT_PROVISIONS=10
MIN_PROVISION_INTERVAL_S=0.2
API_REQUEST_TIMEOUT_S=90
HEALTH_CHECK_INTERVAL_S=15
```

## Essential Commands

### Health Checks
```bash
# Check Orchestrator
curl http://localhost:8000/health

# Check readiness
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/health/ready

# Check Acexy
curl http://localhost:8080/ace/status
```

### Monitoring
```bash
# List engines
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/engines

# Active streams
curl -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/streams?status=started

# Metrics
curl http://localhost:8000/metrics | grep orch_
```

### Management
```bash
# Provision engines
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/scale/10

# Garbage collect
curl -X POST -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/gc

# Delete specific engine
curl -X DELETE -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/containers/{container_id}
```

### Debugging
```bash
# Enable debug logging
LOG_LEVEL=DEBUG
ACEXY_LOG_LEVEL=DEBUG

# Watch logs
docker-compose logs -f orchestrator
docker-compose logs -f orchestrator | grep ERROR

# Check resources
docker stats --no-stream
docker system df
```

## When to Scale What

### More Traffic (Many Clients)
→ Add more Acexy instances
```yaml
services:
  acexy-1:
    ...
  acexy-2:
    ...
  nginx:  # Load balancer
    ...
```

### More Streams (Many Channels)
→ Increase MIN_REPLICAS
```bash
MIN_REPLICAS=20  # More free engines
```

### Complex Orchestration
→ Upgrade Orchestrator resources
```yaml
orchestrator:
  deploy:
    resources:
      limits:
        memory: 4G
        cpus: '4'
```

## Documentation Map

| Need | Document |
|------|----------|
| **Executive decision** | [RECOMMENDATION_SUMMARY.md](RECOMMENDATION_SUMMARY.md) |
| **Technical analysis** | [ACEXY_MERGER_ANALYSIS.md](ACEXY_MERGER_ANALYSIS.md) |
| **Visual comparison** | [ARCHITECTURE_COMPARISON.md](ARCHITECTURE_COMPARISON.md) |
| **Practical improvements** | [INTEGRATION_IMPROVEMENTS.md](INTEGRATION_IMPROVEMENTS.md) |
| **Troubleshooting issues** | [TROUBLESHOOTING_INTEGRATION.md](TROUBLESHOOTING_INTEGRATION.md) |

## Decision Tree

```
Are you experiencing issues?
│
├─ Yes → Is it under high load?
│        │
│        ├─ Yes → See TROUBLESHOOTING_INTEGRATION.md
│        │        ↓
│        │        Check resources, tune timeouts, increase rate limits
│        │
│        └─ No → Check individual service logs
│                 ↓
│                 Likely configuration or deployment issue
│
└─ No → Considering merging for performance?
         │
         └─ Don't. Read RECOMMENDATION_SUMMARY.md
            ↓
            Current architecture is optimal
            ↓
            Implement INTEGRATION_IMPROVEMENTS.md instead
```

## Key Metrics to Watch

| Metric | Good | Warning | Critical |
|--------|------|---------|----------|
| API Latency | <10ms | 10-50ms | >50ms |
| Provision Time | <5s | 5-10s | >10s |
| Free Engines | ≥MIN_REPLICAS | 1-2 | 0 |
| Healthy Engines | >80% | 50-80% | <50% |
| Circuit Breaker | Closed | Half-Open | Open |
| Memory Usage | <60% | 60-80% | >80% |

## Red Flags (Don't Do These)

- ❌ Don't merge Acexy and Orchestrator
- ❌ Don't set timeouts below 3s for Acexy
- ❌ Don't set MIN_REPLICAS to 0
- ❌ Don't ignore health monitoring
- ❌ Don't run without monitoring/metrics
- ❌ Don't blame architecture for config issues

## Green Lights (Do These)

- ✅ Keep services separate
- ✅ Tune timeouts based on your load
- ✅ Monitor metrics continuously
- ✅ Pre-provision engines before high load
- ✅ Scale Acexy horizontally as needed
- ✅ Implement improvements from docs
- ✅ Use configuration templates

## Getting Help

1. **Collect diagnostics:**
   ```bash
   ./collect_diagnostics.sh > diagnostics.txt
   ```
   (Script in TROUBLESHOOTING_INTEGRATION.md)

2. **Check documentation:**
   - Start with RECOMMENDATION_SUMMARY.md
   - Then TROUBLESHOOTING_INTEGRATION.md
   - Deep dive: ACEXY_MERGER_ANALYSIS.md

3. **Enable debug logging:**
   ```bash
   LOG_LEVEL=DEBUG
   docker-compose restart
   ```

4. **Review metrics:**
   ```bash
   curl http://localhost:8000/metrics
   ```

## Final Word

The architecture is **sound and optimal**. Communication issues under stress are **configuration/resource problems**, not architectural flaws.

**Action Plan:**
1. ✅ Use configuration templates from this guide
2. ✅ Monitor metrics
3. ✅ Tune based on actual load
4. ✅ Keep services separate

**Don't:**
1. ❌ Merge services
2. ❌ Rewrite working code
3. ❌ Optimize prematurely

**Time Investment:**
- Proper tuning: 1-2 days → Significant improvement
- Merging services: 2-6 weeks → Negative value

**The choice is obvious.**
