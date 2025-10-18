# Debug Mode Documentation

## Overview

The orchestrator includes a comprehensive debugging mode designed to help diagnose performance issues during stress situations. When enabled, the system writes detailed logs to a persistent folder structure, capturing timing metrics, operation traces, and automatic stress situation detection.

## Purpose

Debug mode helps identify and resolve:
- Performance degradation during high load
- Slow provisioning or health check operations
- VPN connectivity problems
- Circuit breaker activations and cascading failures
- Resource allocation bottlenecks
- Container startup delays
- Health monitoring issues

## Configuration

### Environment Variables

Add these variables to your `.env` file:

```bash
# Enable debug mode
DEBUG_MODE=true

# Directory where debug logs will be stored
DEBUG_LOG_DIR=./debug_logs
```

### Docker Compose

When using Docker, ensure the debug log directory is mounted as a volume:

```yaml
services:
  orchestrator:
    volumes:
      - ./debug_logs:/app/debug_logs
    environment:
      - DEBUG_MODE=true
      - DEBUG_LOG_DIR=/app/debug_logs
```

## Log Structure

### Session-Based Logging

Each time the orchestrator starts, a new session is created with a unique timestamp:

```
debug_logs/
├── 20251018_144430_session.jsonl
├── 20251018_144430_provisioning.jsonl
├── 20251018_144430_health.jsonl
├── 20251018_144430_vpn.jsonl
├── 20251018_144430_circuit_breaker.jsonl
├── 20251018_144430_performance.jsonl
├── 20251018_144430_stress.jsonl
└── 20251018_144430_errors.jsonl
```

### Log Categories

Each category captures specific operational aspects:

#### 1. Session (`*_session.jsonl`)
- Session start/end markers
- Global orchestrator lifecycle events

#### 2. Provisioning (`*_provisioning.jsonl`)
- Container creation attempts
- Provisioning duration and success/failure
- Port allocation details
- Image pull operations
- Startup timeouts

**Example Entry:**
```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:45:12.123456Z",
  "elapsed_seconds": 42.5,
  "operation": "start_acestream_success",
  "container_id": "abc123def456",
  "container_name": "acestream-5",
  "host_http_port": 19023,
  "duration_ms": 8234.56,
  "success": true
}
```

#### 3. Health (`*_health.jsonl`)
- Health check cycles
- Engine health status changes
- Health check timing
- Unhealthy engine counts

**Example Entry:**
```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:45:32.789012Z",
  "elapsed_seconds": 62.3,
  "component": "health_monitor_cycle",
  "status": "completed",
  "duration_ms": 1234.5,
  "total_engines": 10,
  "healthy": 9,
  "unhealthy": 1
}
```

#### 4. VPN (`*_vpn.jsonl`)
- Gluetun health checks
- VPN connection status changes
- Port forwarding operations
- VPN reconnection events

**Example Entry:**
```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:45:15.234567Z",
  "elapsed_seconds": 45.1,
  "operation": "transition",
  "status": "unhealthy",
  "old_status": true,
  "new_status": false
}
```

#### 5. Circuit Breaker (`*_circuit_breaker.jsonl`)
- Circuit state changes
- Failure count tracking
- Circuit breaker activations

**Example Entry:**
```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:46:23.456789Z",
  "elapsed_seconds": 113.2,
  "operation_type": "provisioning",
  "state": "open",
  "failure_count": 5,
  "threshold": 5
}
```

#### 6. Performance (`*_performance.jsonl`)
- Operation timing metrics
- Performance threshold violations
- Slow operation detection

#### 7. Stress (`*_stress.jsonl`)
- Automatic stress situation detection
- High unhealthy engine ratios
- Slow provisioning alerts
- VPN disconnections
- Circuit breaker activations

**Example Entry:**
```json
{
  "session_id": "20251018_144430",
  "timestamp": "2025-10-18T14:47:45.678901Z",
  "elapsed_seconds": 195.5,
  "event_type": "slow_acestream_provisioning",
  "severity": "warning",
  "description": "AceStream provisioning took 18.45s (>17.5s threshold)",
  "container_id": "xyz789abc123",
  "duration": 18.45
}
```

#### 8. Errors (`*_errors.jsonl`)
- Detailed error information
- Stack traces
- Error context

## Stress Situation Detection

Debug mode automatically detects and logs stress situations:

### 1. Slow Provisioning
Triggered when container provisioning exceeds 50% of the timeout threshold.

```json
{
  "event_type": "slow_provisioning",
  "severity": "warning",
  "description": "Container provisioning took 15.23s (>12.5s threshold)"
}
```

### 2. High Unhealthy Engine Ratio
Triggered when more than 30% of engines are unhealthy.

```json
{
  "event_type": "high_unhealthy_engines",
  "severity": "warning",
  "description": "4/10 engines unhealthy (>30%)",
  "unhealthy_count": 4,
  "total": 10
}
```

### 3. VPN Disconnection
Triggered when Gluetun VPN becomes unhealthy.

```json
{
  "event_type": "vpn_disconnection",
  "severity": "critical",
  "description": "Gluetun VPN became unhealthy"
}
```

### 4. Circuit Breaker Activation
Triggered when circuit breaker opens due to failures.

```json
{
  "event_type": "circuit_breaker_opened",
  "severity": "critical",
  "description": "Circuit breaker opened after 5 failures"
}
```

## Usage Examples

### Enable Debug Mode for Testing

1. Update `.env`:
```bash
DEBUG_MODE=true
DEBUG_LOG_DIR=./debug_logs
```

2. Restart the orchestrator:
```bash
docker-compose restart orchestrator
```

3. Logs will be written to `./debug_logs/`

### Analyze Performance Issues

1. Enable debug mode during stress test
2. Run your stress test scenario
3. Analyze the logs:

```bash
# Find slow provisioning events
grep "slow.*provisioning" debug_logs/*_stress.jsonl

# Find VPN issues
grep "vpn" debug_logs/*_vpn.jsonl

# Find circuit breaker activations
grep "open" debug_logs/*_circuit_breaker.jsonl

# Calculate average provisioning time
cat debug_logs/*_provisioning.jsonl | jq -s 'map(select(.operation == "start_acestream_success")) | map(.duration_ms) | add/length'
```

### Correlate Orchestrator and Proxy Logs

When debugging issues that span both orchestrator and proxy:

1. Enable debug mode on orchestrator (as shown above)
2. Enable debug mode on proxy (see `PROXY_DEBUG_MODE_PROMPT.md`)
3. Ensure both systems use synchronized time (NTP)
4. Use `session_id` and `timestamp` to correlate events
5. Look for timing patterns around failures

## Log Analysis Tools

### Using `jq` for JSON Parsing

```bash
# Get all provisioning operations sorted by duration
cat debug_logs/*_provisioning.jsonl | jq -s 'sort_by(.duration_ms) | reverse | .[]'

# Count stress events by type
cat debug_logs/*_stress.jsonl | jq -s 'group_by(.event_type) | map({type: .[0].event_type, count: length})'

# Find operations that took longer than 5 seconds
cat debug_logs/*_*.jsonl | jq -s 'map(select(.duration_ms > 5000))'
```

### Using Python for Analysis

```python
import json
from pathlib import Path
from datetime import datetime

# Load all provisioning logs
provisioning_logs = []
for log_file in Path("debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            provisioning_logs.append(json.loads(line))

# Calculate statistics
durations = [log["duration_ms"] for log in provisioning_logs 
             if log.get("success") and "duration_ms" in log]

print(f"Average: {sum(durations)/len(durations):.2f}ms")
print(f"Max: {max(durations):.2f}ms")
print(f"Min: {min(durations):.2f}ms")

# Find slow operations
slow_ops = [log for log in provisioning_logs 
            if log.get("duration_ms", 0) > 10000]
print(f"Operations slower than 10s: {len(slow_ops)}")
```

## Performance Impact

Debug mode adds minimal overhead:

- **CPU**: < 1% additional CPU usage
- **Memory**: ~10-20MB for buffering (depending on log volume)
- **Disk I/O**: Asynchronous writes, minimal impact
- **Network**: No network overhead

Logs are written asynchronously and buffered to minimize performance impact during stress situations.

## Best Practices

### 1. Enable Only When Needed
Debug mode is designed for troubleshooting. Enable it when:
- Investigating performance degradation
- Stress testing new configurations
- Diagnosing intermittent failures
- Preparing for capacity planning

### 2. Monitor Disk Space
Debug logs can grow large during extended stress tests:
- 1 hour of moderate load: ~50-100MB
- 24 hours of high load: ~2-5GB

Set up log rotation or cleanup:
```bash
# Keep only last 7 days of logs
find debug_logs/ -name "*.jsonl" -mtime +7 -delete
```

### 3. Correlate with System Metrics
Combine debug logs with system monitoring:
- Docker stats (`docker stats`)
- Prometheus metrics (`/metrics` endpoint)
- System resources (`top`, `htop`)

### 4. Archive Important Sessions
Save debug logs from critical incidents:
```bash
# Create archive of debug session
tar -czf debug_session_20251018.tar.gz debug_logs/20251018_*
```

### 5. Review Stress Events First
When analyzing issues, start with stress events:
```bash
cat debug_logs/*_stress.jsonl | jq -s 'sort_by(.timestamp)'
```

This gives you a high-level overview of problems.

## Integration with Monitoring

### Prometheus Integration
Export debug metrics to Prometheus:

```python
# Example: Export slow provisioning count
from prometheus_client import Counter

slow_provisioning_counter = Counter(
    'orchestrator_slow_provisioning_total',
    'Number of slow provisioning events'
)

# In debug logger
if duration > threshold:
    slow_provisioning_counter.inc()
```

### Alerting
Set up alerts based on debug log patterns:

```bash
# Alert on circuit breaker opening
tail -f debug_logs/*_stress.jsonl | grep "circuit_breaker_opened" | \
  while read line; do
    # Send alert (email, Slack, PagerDuty, etc.)
    notify_ops "$line"
  done
```

## Troubleshooting Debug Mode

### Debug Logs Not Being Created

**Check:**
1. `DEBUG_MODE=true` in `.env`
2. Directory permissions: `chmod 755 debug_logs`
3. Volume mounted correctly in Docker
4. Application logs for initialization messages

### Logs Are Empty

**Check:**
1. Operations are actually occurring (provision engines, health checks, etc.)
2. Log level in `app/utils/logging.py` (should be INFO or DEBUG)
3. File system has available space

### Performance Degradation with Debug Mode

**Check:**
1. Disk I/O performance (use `iostat`)
2. Log directory on fast storage (not network mount)
3. Log file size (implement rotation if very large)

## Security Considerations

### Sensitive Data
Debug logs may contain:
- Container IDs
- Port numbers
- Configuration details
- Error messages

**Recommendations:**
- Don't commit debug logs to version control (already in `.gitignore`)
- Sanitize logs before sharing externally
- Restrict file permissions: `chmod 600 debug_logs/*`
- Delete logs after analysis: `rm -rf debug_logs/`

### Production Use
For production environments:
- Enable debug mode only during incident investigation
- Set up automatic log rotation
- Implement log shipping to secure storage
- Configure alerts for stress events

## Summary

Debug mode provides comprehensive visibility into orchestrator operations during stress situations. By enabling it during troubleshooting or stress testing, you can:

1. **Identify bottlenecks** through timing metrics
2. **Detect stress situations** automatically
3. **Correlate events** across components
4. **Analyze patterns** in failures and slowdowns
5. **Optimize performance** based on real data

The structured JSON format makes logs easy to parse and analyze with standard tools like `jq`, Python, or log aggregation platforms.

## Next Steps

- See `PROXY_DEBUG_MODE_PROMPT.md` for implementing similar debugging in the proxy
- Review `TROUBLESHOOTING.md` for common issues
- Check `PERFORMANCE.md` for optimization techniques
