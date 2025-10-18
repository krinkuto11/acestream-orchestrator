# Debug Mode - Quick Start Example

This guide provides a practical example of using debug mode to investigate a performance issue.

## Scenario: Investigating Slow Provisioning

### Step 1: Enable Debug Mode

Edit your `.env` file:

```bash
# Enable debug mode
DEBUG_MODE=true
DEBUG_LOG_DIR=./debug_logs
```

### Step 2: Restart Orchestrator

```bash
docker-compose restart orchestrator

# Or if running directly
docker-compose down
docker-compose up -d
```

You should see in the logs:
```
INFO app.main: Debug mode enabled. Logs will be written to: ./debug_logs
```

### Step 3: Reproduce the Issue

Trigger the slow provisioning scenario:

```bash
# Provision multiple engines
for i in {1..5}; do
  curl -X POST http://localhost:8000/provision/acestream \
    -H "Authorization: Bearer your-api-key" \
    -H "Content-Type: application/json" \
    -d '{"labels":{"test":"stress"}}'
  echo ""
  sleep 2
done
```

### Step 4: Examine Debug Logs

List the log files:
```bash
ls -lh debug_logs/
```

You'll see files like:
```
20251018_144430_session.jsonl
20251018_144430_provisioning.jsonl
20251018_144430_health.jsonl
20251018_144430_vpn.jsonl
20251018_144430_stress.jsonl
20251018_144430_performance.jsonl
```

### Step 5: Analyze Provisioning Times

View all provisioning events:
```bash
cat debug_logs/*_provisioning.jsonl | jq '.'
```

Find slow provisioning events:
```bash
cat debug_logs/*_provisioning.jsonl | jq 'select(.duration_ms > 10000)'
```

Calculate average provisioning time:
```bash
cat debug_logs/*_provisioning.jsonl | \
  jq -s 'map(select(.operation == "start_acestream_success" and .duration_ms != null)) | 
         map(.duration_ms) | 
         add / length'
```

### Step 6: Check for Stress Events

View all stress events:
```bash
cat debug_logs/*_stress.jsonl | jq '.'
```

Look for specific stress indicators:
```bash
# Slow provisioning events
cat debug_logs/*_stress.jsonl | jq 'select(.event_type == "slow_provisioning")'

# VPN issues
cat debug_logs/*_stress.jsonl | jq 'select(.event_type == "vpn_disconnection")'

# Circuit breaker activations
cat debug_logs/*_stress.jsonl | jq 'select(.event_type == "circuit_breaker_opened")'
```

### Step 7: Timeline Analysis

Create a timeline of events:
```bash
# Combine all logs and sort by timestamp
cat debug_logs/*.jsonl | jq -s 'sort_by(.timestamp)' > timeline.json

# View critical events in order
cat timeline.json | jq '.[] | select(.event_type or .operation) | 
  {time: .timestamp, type: (.event_type // .operation), desc: .description}'
```

## Example Output Analysis

### Finding 1: Slow Provisioning Pattern

```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:45:23.456Z",
  "elapsed_seconds": 53.2,
  "operation": "start_acestream_success",
  "container_id": "abc123",
  "duration_ms": 18450.5,
  "success": true
}
```

**Observation**: Provisioning took 18.45 seconds (above the 25s timeout threshold of 70% = 17.5s)

**Action**: Check VPN status, Docker daemon load, image availability

### Finding 2: VPN Health Issues

```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:45:15.234Z",
  "elapsed_seconds": 45.1,
  "event_type": "vpn_disconnection",
  "severity": "critical",
  "description": "Gluetun VPN became unhealthy"
}
```

**Observation**: VPN disconnection occurred 8 seconds before slow provisioning

**Action**: Investigate VPN stability, check Gluetun logs

### Finding 3: Health Check Degradation

```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:45:32.789Z",
  "elapsed_seconds": 62.3,
  "event_type": "high_unhealthy_engines",
  "severity": "warning",
  "description": "4/10 engines unhealthy (>30%)",
  "unhealthy_count": 4,
  "total": 10
}
```

**Observation**: After VPN issue, 40% of engines became unhealthy

**Action**: Implement auto-restart for engines after VPN recovery

## Correlation with Proxy Logs

If you've implemented debug mode in the proxy (see `PROXY_DEBUG_MODE_PROMPT.md`):

### Find Matching Provisioning Requests

```bash
# From orchestrator: when provisioning started
grep "start_acestream_begin" orchestrator_debug_logs/*_provisioning.jsonl

# From proxy: when provisioning was requested
grep "provision_request" proxy_debug_logs/*_provisioning.jsonl

# Compare timestamps to find proxy → orchestrator latency
```

### Example Correlation Script

```python
#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime, timedelta

# Load orchestrator provisions
orch_provisions = []
for log_file in Path("debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("operation") == "start_acestream_begin":
                orch_provisions.append(entry)

# Load proxy provisions
proxy_provisions = []
for log_file in Path("../proxy/debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("operation") == "provision_request":
                proxy_provisions.append(entry)

# Find matching provisions (within 1 second)
for proxy_prov in proxy_provisions:
    proxy_time = datetime.fromisoformat(proxy_prov["timestamp"].replace('Z', '+00:00'))
    
    for orch_prov in orch_provisions:
        orch_time = datetime.fromisoformat(orch_prov["timestamp"].replace('Z', '+00:00'))
        
        time_diff = (orch_time - proxy_time).total_seconds()
        if 0 <= time_diff <= 1:
            print(f"Matched provision:")
            print(f"  Proxy request: {proxy_time}")
            print(f"  Orch start:    {orch_time}")
            print(f"  Latency:       {time_diff:.3f}s")
            print()
```

## Common Patterns and Solutions

### Pattern 1: Slow Provisioning After VPN Reconnect

**Symptoms:**
- VPN disconnection event
- Multiple slow provisioning events shortly after
- High unhealthy engine count

**Root Cause:** Engines trying to start before VPN is fully stable

**Solution:**
```bash
# Increase VPN grace period in .env
GLUETUN_HEALTH_CHECK_INTERVAL_S=3
```

### Pattern 2: Circuit Breaker Opens During High Load

**Symptoms:**
- Multiple provisioning failures
- Circuit breaker opens
- Stress events for "circuit_breaker_opened"

**Root Cause:** System overload or underlying issue

**Solution:**
```bash
# Adjust circuit breaker thresholds in .env
CIRCUIT_BREAKER_FAILURE_THRESHOLD=10  # Increase from 5
CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S=180  # Decrease from 300
```

### Pattern 3: All Provisioning Slow During Peak Load

**Symptoms:**
- Every provisioning operation exceeds threshold
- High duration_ms values across the board
- No specific error events

**Root Cause:** Resource contention (CPU, memory, disk I/O)

**Solution:**
- Scale up Docker host resources
- Reduce MAX_REPLICAS to prevent overload
- Enable AUTO_DELETE to free resources faster

## Cleanup

After investigation, disable debug mode or clean up old logs:

```bash
# Disable debug mode
# Edit .env and set DEBUG_MODE=false

# Or keep enabled but clean old logs
find debug_logs/ -name "*.jsonl" -mtime +7 -delete

# Or archive important sessions
tar -czf investigation_20251018.tar.gz debug_logs/20251018_*
```

## Next Steps

1. Review findings and identify root causes
2. Implement fixes based on patterns discovered
3. Re-enable debug mode to verify fixes
4. Document recurring issues and solutions
5. Set up monitoring/alerting for stress events

## Advanced Analysis

### Generate Performance Report

```python
#!/usr/bin/env python3
import json
from pathlib import Path
from collections import defaultdict

stats = defaultdict(list)

# Collect all operation timings
for log_file in Path("debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            entry = json.loads(line)
            if "duration_ms" in entry and entry.get("success"):
                stats[entry["operation"]].append(entry["duration_ms"])

# Generate report
print("Performance Report")
print("=" * 50)
for operation, durations in stats.items():
    if durations:
        avg = sum(durations) / len(durations)
        max_d = max(durations)
        min_d = min(durations)
        print(f"\n{operation}:")
        print(f"  Count:   {len(durations)}")
        print(f"  Average: {avg:.2f}ms")
        print(f"  Min:     {min_d:.2f}ms")
        print(f"  Max:     {max_d:.2f}ms")
```

### Export to CSV for Spreadsheet Analysis

```bash
# Export provisioning data
cat debug_logs/*_provisioning.jsonl | \
  jq -r '[.timestamp, .operation, .duration_ms, .success] | @csv' > provisioning.csv

# Open in Excel/Google Sheets for visualization
```

## Summary

Debug mode provides:
- ✅ Detailed timing for every operation
- ✅ Automatic stress situation detection
- ✅ Complete operation trace for root cause analysis
- ✅ Correlation support with proxy logs
- ✅ Historical data for pattern recognition

Use it whenever investigating:
- Performance degradation
- Intermittent failures
- Resource bottlenecks
- Stress test results
- Production incidents
