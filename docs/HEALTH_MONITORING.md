# Health Monitoring & Usage Tracking

The Acestream Orchestrator includes intelligent health monitoring and usage tracking capabilities to ensure optimal engine performance and enable smart proxy selection.

## Health Monitoring

### Overview
The orchestrator continuously monitors the health of all managed Acestream engines using their native API endpoints. When engines hang or become unresponsive, they are automatically detected and marked as unhealthy.

### Health Check Mechanism
- **Endpoint**: `/server/api?api_version=3&method=get_status`
- **Method**: HTTP GET with 5-second timeout
- **Frequency**: Every 30 seconds via background service
- **Detection**: When engines hang, this endpoint becomes unresponsive

### Health Status Values
- **healthy**: Engine responds normally to API requests
- **unhealthy**: Engine not responding or returning errors
- **unknown**: Health status pending or unable to determine

### API Integration
Health information is included in all engine endpoints:

```json
{
  "container_id": "engine123",
  "container_name": "acestream-engine",
  "host": "127.0.0.1",
  "port": 8080,
  "health_status": "healthy",
  "last_health_check": "2023-09-23T13:45:30.123Z",
  "last_stream_usage": "2023-09-23T12:30:15.456Z",
  "streams": ["stream1", "stream2"]
}
```

### Background Service
The health monitoring service runs automatically:
- **Start**: Launched during application startup
- **Interval**: 30-second health checks
- **Logging**: Debug logs for health status changes
- **Graceful shutdown**: Stops cleanly during application shutdown

## Usage Tracking

### Purpose
Track when streams were last loaded into each engine to enable intelligent engine selection for proxy services and load balancing.

### Implementation
- **Timestamp**: Updated when streams start on engines
- **Format**: ISO8601 datetime with timezone
- **Persistence**: Stored in engine state
- **API exposure**: Available via `/engines` endpoint

### Use Cases
1. **Proxy Selection**: Choose engines with longest idle time
2. **Load Balancing**: Distribute streams across least recently used engines
3. **Resource Management**: Identify frequently vs rarely used engines
4. **Capacity Planning**: Understand engine utilization patterns

### Dashboard Integration
- **Human-readable format**: "5m ago", "2h ago", "Never"
- **Visual indicators**: Clear display of usage patterns
- **Real-time updates**: Automatically refreshes with new data

## Configuration

### Health Check Interval
The health check interval is configured in the health monitor service:
```python
# Default: 30 seconds
health_monitor = HealthMonitor(check_interval=30)
```

### Timeout Settings
API health checks use a 5-second timeout:
```python
response = httpx.get(url, timeout=5)
```

## Monitoring & Alerts

### Dashboard Indicators
- **Color coding**: Green (healthy), Red (unhealthy), Gray (unknown)
- **Status counts**: KPI showing healthy vs total engines
- **Last check**: Timestamp of most recent health verification
- **Usage patterns**: Visual indication of engine idle time

### Logging
Health monitoring events are logged for troubleshooting:
```
INFO app.services.health_monitor: Health monitor started with 30s interval
DEBUG app.services.health_monitor: Updated health status for all engines
ERROR app.services.health_monitor: Error during health check: Connection timeout
```

## Troubleshooting

### Common Issues

#### Engine Marked as Unhealthy
1. **Check engine logs**: Verify Acestream engine is running
2. **Network connectivity**: Ensure engine API is accessible
3. **Port conflicts**: Verify no port binding issues
4. **Resource constraints**: Check CPU/memory usage

#### Health Checks Not Running
1. **Service status**: Verify health monitor started successfully
2. **Background tasks**: Check asyncio task execution
3. **Application logs**: Look for startup/shutdown messages

#### Inconsistent Status
1. **Timing issues**: Allow 30+ seconds for status updates
2. **Engine recovery**: Unhealthy engines may recover automatically
3. **Manual refresh**: Force refresh in dashboard if needed

### Debugging Commands
```bash
# Check engine health manually
curl -s "http://engine-host:port/server/api?api_version=3&method=get_status"

# View health status via API
curl -s "http://localhost:8000/engines" | jq '.[] | {id: .container_id, health: .health_status, last_check: .last_health_check}'
```

## Performance Impact

### Resource Usage
- **CPU**: Minimal overhead from periodic HTTP requests
- **Memory**: Small footprint for health status storage
- **Network**: One HTTP request per engine every 30 seconds
- **Latency**: No impact on stream processing performance

### Scalability
- **Engine count**: Scales linearly with number of engines
- **Check interval**: Configurable to balance monitoring vs resource usage
- **Timeout handling**: Prevents hanging health checks from blocking other operations

## Stale Stream Detection

### Overview
The orchestrator automatically detects and handles stale streams using the Acestream stat endpoint. When a stream stops or becomes invalid, the engine returns an error indicating the playback session is unknown.

### Detection Mechanism
- **Endpoint**: `/ace/stat/<playback_session_id>`
- **Detection Pattern**: `{"response": null, "error": "unknown playback session id"}`
- **Frequency**: Every `COLLECT_INTERVAL_S` (default: 5 seconds)
- **Action**: Automatically ends the stream in the orchestrator state

### How It Works
1. **Periodic Polling**: The collector service polls the stat URL for each active stream
2. **Response Analysis**: Checks if the response indicates a stale/stopped stream
3. **Automatic Cleanup**: When detected, the stream is automatically marked as "ended"
4. **Resource Management**: This triggers cleanup processes (cache clearing, container management)

### Benefits
- **Resilient Tracking**: Prevents orphaned stream records when streams stop unexpectedly
- **Accurate State**: Ensures orchestrator state matches actual engine state
- **Automatic Recovery**: No manual intervention needed when streams become stale
- **Resource Efficiency**: Enables timely cleanup of idle engines

### Metrics
Monitor stale stream detection using Prometheus metrics:
```
# Total number of stale streams detected and auto-ended
orch_stale_streams_detected_total
```

### Behavior
- **Normal streams**: Continue to collect statistics as usual
- **Stale streams**: Automatically ended and removed from active tracking
- **HTTP errors**: Do not trigger stale detection (network issues handled separately)
- **Other errors**: Only the specific "unknown playback session id" error triggers detection

### Example Scenario
```
1. Stream starts: POST /events/stream_started
2. Collector polls: GET /ace/stat/session_123 → {"response": {...stats...}}
3. Stream stops on engine side (user disconnects, error, etc.)
4. Collector polls: GET /ace/stat/session_123 → {"response": null, "error": "unknown playback session id"}
5. Orchestrator detects stale stream
6. Stream automatically ended: state.on_stream_ended(...)
7. Cleanup processes triggered (cache clear, container management)
```

### Configuration
Stale stream detection is always enabled and uses the existing `COLLECT_INTERVAL_S` configuration:
```bash
# .env
COLLECT_INTERVAL_S=5  # How often to poll stream stats (default: 5 seconds)
```

### Logging
Stale stream detection events are logged for monitoring:
```
INFO app.services.collector: Detected stale stream stream_id_123: unknown playback session id
INFO app.services.collector: Automatically ending stale stream stream_id_123
```