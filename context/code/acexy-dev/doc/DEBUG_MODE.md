# Debug Mode Documentation

## Overview

Acexy includes a comprehensive debug mode that writes detailed performance logs during normal operations and stress situations. This feature helps investigate performance issues, correlate events with the orchestrator, and optimize the system.

## Purpose

Debug mode provides:

1. **Complete visibility** into proxy operations during stress situations
2. **Correlation** with orchestrator logs for end-to-end analysis
3. **Automatic detection** of performance issues and stress situations
4. **Data-driven optimization** based on real timing metrics
5. **Faster debugging** of production issues

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG_MODE` | Enable debug mode | `false` |
| `DEBUG_LOG_DIR` | Directory for debug logs | `./debug_logs` |

### Command Line Flags

| Flag | Description | Default |
|------|-------------|---------|
| `-debugMode` | Enable debug mode | `false` |
| `-debugLogDir` | Directory for debug logs | `./debug_logs` |

### Docker Compose Example

```yaml
services:
  acexy-proxy:
    image: ghcr.io/javinator9889/acexy:latest
    environment:
      - DEBUG_MODE=true
      - DEBUG_LOG_DIR=/app/debug_logs
    volumes:
      - ./proxy_debug_logs:/app/debug_logs
```

### Docker Run Example

```bash
docker run -d \
  -e DEBUG_MODE=true \
  -e DEBUG_LOG_DIR=/app/debug_logs \
  -v ./proxy_debug_logs:/app/debug_logs \
  -p 8080:8080 \
  ghcr.io/javinator9889/acexy:latest
```

## Log Structure

Debug logs are written to JSON Lines (JSONL) files organized by category. Each session creates a set of log files with a timestamp prefix.

### File Naming Convention

```
<timestamp>_<category>.jsonl
```

Example:
```
20240318_143052_requests.jsonl
20240318_143052_engine_selection.jsonl
20240318_143052_provisioning.jsonl
```

### Log Categories

#### 1. Session Logs (`*_session.jsonl`)

Records session lifecycle events.

**Fields:**
- `session_id`: Unique session identifier
- `timestamp`: ISO 8601 timestamp with nanosecond precision
- `elapsed_seconds`: Time elapsed since session start
- `event`: Event type (e.g., "session_start")

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:30:52.123456789Z",
  "elapsed_seconds": 0.0,
  "event": "session_start"
}
```

#### 2. Request Logs (`*_requests.jsonl`)

Records HTTP request timing and outcomes.

**Fields:**
- `method`: HTTP method (GET, POST, etc.)
- `path`: Request path
- `duration_ms`: Request duration in milliseconds
- `status_code`: HTTP status code
- `ace_id`: AceStream content ID

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:31:15.234567890Z",
  "elapsed_seconds": 23.111,
  "method": "GET",
  "path": "/ace/getstream",
  "duration_ms": 1250,
  "status_code": 200,
  "ace_id": "dd1e67078381739d14beca697356ab76d49d1a2"
}
```

#### 3. Engine Selection Logs (`*_engine_selection.jsonl`)

Records engine selection decisions and timing.

**Fields:**
- `operation`: Operation type (e.g., "select_best_engine")
- `selected_host`: Selected engine host
- `selected_port`: Selected engine port
- `container_id`: Engine container ID
- `duration_ms`: Selection duration in milliseconds
- `error`: Error message (if any)

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:31:15.456789012Z",
  "elapsed_seconds": 23.333,
  "operation": "select_best_engine",
  "selected_host": "localhost",
  "selected_port": 19000,
  "container_id": "acestream-abc123",
  "duration_ms": 245,
  "error": ""
}
```

#### 4. Provisioning Logs (`*_provisioning.jsonl`)

Records provisioning operations with retry information.

**Fields:**
- `operation`: Operation type (e.g., "provision_success", "provision_attempt_failed")
- `duration_ms`: Operation duration in milliseconds
- `success`: Whether operation succeeded
- `error`: Error message (if any)
- `retry_count`: Number of retries attempted

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:31:20.678901234Z",
  "elapsed_seconds": 28.555,
  "operation": "provision_success",
  "duration_ms": 3500,
  "success": true,
  "error": "",
  "retry_count": 2
}
```

#### 5. Orchestrator Health Logs (`*_orchestrator_health.jsonl`)

Records orchestrator health check results.

**Fields:**
- `status`: Orchestrator status (e.g., "healthy", "degraded")
- `can_provision`: Whether provisioning is allowed
- `blocked_reason`: Reason provisioning is blocked (if any)
- `blocked_reason_code`: Error code for blocked reason
- `recovery_eta_seconds`: Estimated recovery time
- `vpn_connected`: VPN connection status
- `capacity_total`: Total capacity
- `capacity_used`: Used capacity
- `capacity_available`: Available capacity

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:31:25.890123456Z",
  "elapsed_seconds": 33.767,
  "status": "healthy",
  "can_provision": true,
  "blocked_reason": "",
  "blocked_reason_code": "",
  "recovery_eta_seconds": 0,
  "vpn_connected": true,
  "capacity_total": 10,
  "capacity_used": 3,
  "capacity_available": 7
}
```

#### 6. Stream Logs (`*_streams.jsonl`)

Records stream lifecycle events.

**Fields:**
- `event_type`: Event type (e.g., "stream_started", "stream_ended")
- `stream_id`: Stream identifier
- `engine_id`: Engine container ID
- `duration_ms`: Event processing duration
- Additional fields vary by event type

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:31:16.012345678Z",
  "elapsed_seconds": 23.889,
  "event_type": "stream_started",
  "stream_id": "stream-xyz789",
  "engine_id": "acestream-abc123",
  "duration_ms": 125,
  "host": "localhost",
  "port": 19000,
  "key_type": "content_id",
  "key": "dd1e67078381739d14beca697356ab76d49d1a2",
  "playback_id": "playback-123"
}
```

#### 7. Stress Logs (`*_stress.jsonl`)

Records detected stress situations.

**Fields:**
- `event_type`: Stress event type
- `severity`: Severity level (e.g., "warning", "critical")
- `description`: Human-readable description
- Additional context fields

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:31:22.234567890Z",
  "elapsed_seconds": 30.111,
  "event_type": "slow_request",
  "severity": "warning",
  "description": "Request took 6.50s",
  "path": "/ace/getstream",
  "ace_id": "dd1e67078381739d14beca697356ab76d49d1a2",
  "duration": 6.5
}
```

**Stress Event Types:**
- `slow_request`: Request took longer than 5 seconds
- `slow_engine_selection`: Engine selection took longer than 2 seconds
- `provisioning_circuit_breaker`: Provisioning blocked by circuit breaker
- `orchestrator_degraded`: Orchestrator in degraded state

#### 8. Error Logs (`*_errors.jsonl`)

Records error details with context.

**Fields:**
- `component`: Component where error occurred
- `operation`: Operation that failed
- `error_type`: Error type name
- `error_message`: Error message
- Additional context fields

**Example:**
```json
{
  "session_id": "20240318_143052",
  "timestamp": "2024-03-18T14:31:18.456789012Z",
  "elapsed_seconds": 26.333,
  "component": "orchestrator_client",
  "operation": "provision_acestream",
  "error_type": "*errors.errorString",
  "error_message": "connection timeout",
  "host": "localhost",
  "port": 8000
}
```

## Analyzing Debug Logs

### Using Command-Line Tools

#### View all requests
```bash
cat debug_logs/*_requests.jsonl | jq
```

#### Find slow requests (>5 seconds)
```bash
cat debug_logs/*_requests.jsonl | jq 'select(.duration_ms > 5000)'
```

#### Track provisioning attempts
```bash
cat debug_logs/*_provisioning.jsonl | jq 'select(.operation == "provision_attempt_failed")'
```

#### Monitor orchestrator health transitions
```bash
cat debug_logs/*_orchestrator_health.jsonl | jq 'select(.status != "healthy")'
```

#### View all stress events
```bash
cat debug_logs/*_stress.jsonl | jq
```

### Python Analysis Example

```python
import json
from pathlib import Path
from datetime import datetime

def analyze_debug_logs(log_dir):
    # Load all request logs
    requests = []
    for log_file in Path(log_dir).glob("*_requests.jsonl"):
        with open(log_file) as f:
            for line in f:
                requests.append(json.loads(line))
    
    # Calculate statistics
    durations = [r['duration_ms'] for r in requests]
    avg_duration = sum(durations) / len(durations)
    max_duration = max(durations)
    
    print(f"Total requests: {len(requests)}")
    print(f"Average duration: {avg_duration:.2f}ms")
    print(f"Maximum duration: {max_duration}ms")
    
    # Find slow requests
    slow_requests = [r for r in requests if r['duration_ms'] > 5000]
    print(f"\nSlow requests (>5s): {len(slow_requests)}")
    for req in slow_requests:
        print(f"  - {req['timestamp']}: {req['duration_ms']}ms - {req['path']}")

# Usage
analyze_debug_logs("./debug_logs")
```

## Correlating with Orchestrator Logs

When both acexy and the orchestrator have debug mode enabled, you can correlate events using:

1. **Timestamps**: Both use RFC3339 format with nanosecond precision
2. **Container IDs**: Engine container IDs are logged in both systems
3. **Stream IDs**: Stream identifiers are consistent across systems

### Example: Finding End-to-End Provisioning Delays

```python
import json
from pathlib import Path
from datetime import datetime, timedelta

# Load proxy provisioning logs
proxy_provisions = []
for log_file in Path("proxy_debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            proxy_provisions.append(json.loads(line))

# Load orchestrator provisioning logs
orch_provisions = []
for log_file in Path("orchestrator_debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            orch_provisions.append(json.loads(line))

# Find correlated provisions (within 1 second)
for proxy_prov in proxy_provisions:
    if proxy_prov['operation'] != 'provision_success':
        continue
    
    proxy_time = datetime.fromisoformat(proxy_prov['timestamp'])
    
    for orch_prov in orch_provisions:
        orch_time = datetime.fromisoformat(orch_prov['timestamp'])
        
        if abs((proxy_time - orch_time).total_seconds()) < 1:
            print(f"Correlated provision:")
            print(f"  Proxy took: {proxy_prov['duration_ms']}ms")
            print(f"  Orchestrator took: {orch_prov['duration_ms']}ms")
            print(f"  Container: {orch_prov.get('container_id', 'unknown')}")
            print()
```

## Performance Impact

Debug mode is designed to have minimal performance impact:

- **Early Exit**: Checks enabled flag immediately
- **Async Writes**: File I/O happens in defer blocks
- **Buffered Output**: Uses Go's buffered I/O
- **Selective Logging**: Only logs when debug mode is enabled

**Expected Overhead**: <1% when debug mode is enabled

## Security Considerations

### Sensitive Data

Debug logs may contain:
- AceStream content IDs
- Stream identifiers
- Playback session IDs
- Network addresses and ports

**Recommendations:**
1. Store debug logs in a secure location
2. Rotate logs regularly
3. Restrict access to debug log directories
4. Avoid committing debug logs to version control

### Disk Space Management

Debug logs can grow quickly under high load:

- **Estimate**: ~1KB per request
- **High Load Example**: 100 req/s = ~360MB/hour

**Recommendations:**
1. Monitor disk space usage
2. Implement log rotation (e.g., using `logrotate`)
3. Set up automated cleanup of old logs
4. Consider log aggregation tools for long-term storage

### Log Rotation Example

```bash
# /etc/logrotate.d/acexy-debug
/app/debug_logs/*.jsonl {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 acexy acexy
}
```

## Troubleshooting

### Debug Logs Not Being Created

1. Check if debug mode is enabled:
   ```bash
   docker exec acexy-proxy env | grep DEBUG
   ```

2. Verify log directory permissions:
   ```bash
   docker exec acexy-proxy ls -la /app/debug_logs
   ```

3. Check container logs for errors:
   ```bash
   docker logs acexy-proxy | grep -i debug
   ```

### Missing Events in Logs

Debug logging uses defer blocks, so events may be delayed if operations hang. Check for:

1. Hanging requests
2. Network timeouts
3. Deadlocks in concurrent operations

### High Disk Usage

1. Verify log rotation is configured
2. Check for slow request patterns causing excessive logging
3. Consider increasing retention thresholds for stress events

## Best Practices

1. **Enable Selectively**: Only enable debug mode when investigating issues
2. **Monitor Disk Space**: Set up alerts for disk usage
3. **Correlate Events**: Use timestamps to correlate with orchestrator logs
4. **Automate Analysis**: Create scripts to analyze common patterns
5. **Share Findings**: Document patterns and solutions for team reference

## Example Debugging Workflow

### Problem: Slow stream startup times

1. **Enable debug mode** on both acexy and orchestrator
2. **Reproduce the issue** by starting a stream
3. **Collect logs** from both systems
4. **Analyze request logs** to find slow requests:
   ```bash
   cat debug_logs/*_requests.jsonl | jq 'select(.duration_ms > 5000)'
   ```
5. **Check engine selection timing**:
   ```bash
   cat debug_logs/*_engine_selection.jsonl | jq
   ```
6. **Review provisioning operations**:
   ```bash
   cat debug_logs/*_provisioning.jsonl | jq
   ```
7. **Correlate with orchestrator** to find bottlenecks
8. **Identify root cause** and optimize
9. **Disable debug mode** after investigation

## Integration with Monitoring Tools

Debug logs can be integrated with monitoring and observability tools:

### Prometheus + Grafana

Use a JSON log exporter to expose metrics:

```bash
# Example: Convert debug logs to Prometheus metrics
cat debug_logs/*_requests.jsonl | jq -r '[.timestamp, .duration_ms, .status_code, .ace_id] | @tsv'
```

### ELK Stack (Elasticsearch, Logstash, Kibana)

Ingest JSONL files directly:

```conf
input {
  file {
    path => "/app/debug_logs/*.jsonl"
    codec => "json_lines"
  }
}

filter {
  date {
    match => ["timestamp", "ISO8601"]
  }
}

output {
  elasticsearch {
    hosts => ["localhost:9200"]
    index => "acexy-debug-%{+YYYY.MM.dd}"
  }
}
```

### Grafana Loki

Stream debug logs to Loki:

```yaml
# promtail config
clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: acexy-debug
    static_configs:
      - targets:
          - localhost
        labels:
          job: acexy-debug
          __path__: /app/debug_logs/*.jsonl
```

## Summary

Debug mode provides comprehensive visibility into acexy operations, enabling:

- **Rapid troubleshooting** of performance issues
- **Data-driven optimization** decisions
- **End-to-end tracing** across acexy and orchestrator
- **Automatic detection** of stress situations
- **Historical analysis** of system behavior

For questions or issues, please refer to the main documentation or open an issue on GitHub.
