# API

Auth: add `Authorization: Bearer <API_KEY>` in protected endpoints.

## Provisioning
### POST /provision
Creates container with generic parameters (not AceStream).
Body:
```json
{
  "image": "acestream/engine:latest",
  "env": {"CONF":"..."},
  "labels": {"stream_id":"123"},
  "ports": {"40001/tcp": 19001}
}
```
Response:
```json
{"container_id": "…"}
```
### POST /provision/acestream

Starts AceStream with dynamic ports and built `CONF`.
Body:
```json
{
  "image": "acestream/engine:latest",
  "labels": {"stream_id":"123"},
  "env": {"EXTRA_ENV":"X"},
  "host_port": null
}
```
Response:
```json
{
  "container_id":"…",
  "host_http_port":19023,
  "container_http_port":40117,
  "container_https_port":45109
}
```
## Events
### POST /events/stream_started
Body:
```json
{
  "container_id":"…",
  "engine":{"host":"127.0.0.1","port":19023},
  "stream":{"key_type":"infohash","key":"0a48..."},
  "session":{
    "playback_session_id":"e0d10c40…",
    "stat_url":"http://127.0.0.1:19023/ace/stat/…",
    "command_url":"http://127.0.0.1:19023/ace/cmd/…",
    "is_live":1
  },
  "labels":{"stream_id":"ch-42"}
}
```
Response: `StreamState`
### POST /events/stream_ended
Body:
```json
{"container_id":"…","stream_id":"ch-42","reason":"player_stopped"}
```
Response:
```json
{"updated": true, "stream": {…}}
```
### Read Operations

 - GET /engines → EngineState[]
   - Now includes health monitoring fields:
     - `health_status`: "healthy" | "unhealthy" | "unknown"
     - `last_health_check`: ISO8601 timestamp of last health check
     - `last_stream_usage`: ISO8601 timestamp when stream was last loaded
     - `last_cache_cleanup`: ISO8601 timestamp of last cache cleanup (null if never cleaned)
     - `cache_size_bytes`: Size of cache that was cleared in bytes (null if never cleaned)

 - GET /engines/{container_id} → {engine, streams[]}

 - GET /streams?status=started|ended&container_id= → StreamState[]
   - By default, returns only `started` streams (active streams)
   - Use `status=started` to explicitly get active streams
   - Use `status=ended` to get ended streams
   - Can filter by `container_id` to get streams for a specific engine
   - **Note**: Ended streams older than 1 hour are automatically cleaned up from the system

 - GET /streams/{stream_id}/stats?since=<ISO8601> → StreamStatSnapshot[]

 - GET /containers/{container_id} → Docker inspection

 - GET /by-label?key=stream_id&value=ch-42 (protected)

 - GET /vpn/status → VPN status information
   ```json
   {
     "enabled": true,
     "status": "running",
     "container_name": "gluetun",
     "container": "gluetun",
     "health": "healthy",
     "connected": true,
     "forwarded_port": 12345,
     "last_check": "2023-09-23T13:45:30.123Z",
     "last_check_at": "2023-09-23T13:45:30.123Z"
   }
   ```
   
   **Fields:**
   - `enabled`: Whether VPN monitoring is enabled
   - `status`: Docker container status ("running", "stopped", etc.)
   - `container_name`: Name of the Gluetun container
   - `container`: Alias for `container_name` (frontend compatibility)
   - `health`: Health status ("healthy", "unhealthy", "starting", "unknown")
   - `connected`: Boolean indicating if VPN is connected (derived from health == "healthy")
   - `forwarded_port`: VPN forwarded port number (if available)
   - `last_check`: ISO8601 timestamp of last check
   - `last_check_at`: Alias for `last_check` (frontend compatibility)
   
   **Note**: The VPN health check now includes double-checking via engine network connectivity.
   When Gluetun container health appears "unhealthy", the system verifies actual network 
   connectivity by testing engines' `/server/api?api_version=3&method=get_network_connection_status` 
   endpoint. If any engine reports `{"result": {"connected": true}}`, the VPN is considered healthy.

### Control
 - DELETE /containers/{container_id} (protected)
 - POST /gc (protected)
 - POST /scale/{demand:int} (protected)

### Metrics
 - GET /metrics Prometheus:
   - orch_events_started_total
   - orch_events_ended_total
   - orch_collect_errors_total
   - orch_streams_active
   - orch_provision_total{kind="generic|acestream"}

## Custom Engine Variant

Custom engine variants allow fine-grained control over AceStream engine parameters via the UI.

### GET /custom-variant/platform

Get detected platform information.

Response:
```json
{
  "platform": "amd64",
  "supported_platforms": ["amd64", "arm32", "arm64"]
}
```

**Fields:**
- `platform`: Automatically detected system architecture
- `supported_platforms`: List of all supported platforms

### GET /custom-variant/config

Get current custom variant configuration.

Response:
```json
{
  "enabled": false,
  "platform": "amd64",
  "arm_version": "3.2.13",
  "parameters": [
    {
      "name": "--client-console",
      "type": "flag",
      "value": true,
      "enabled": true
    },
    {
      "name": "--live-cache-size",
      "type": "bytes",
      "value": 268435456,
      "enabled": true
    }
  ]
}
```

**Fields:**
- `enabled`: Whether custom variant is active (overrides ENGINE_VARIANT env var)
- `platform`: Platform architecture ("amd64", "arm32", "arm64")
- `arm_version`: AceStream version for ARM platforms ("3.2.13" or "3.2.14")
- `parameters`: Array of engine parameter configurations
  - `name`: Parameter name (e.g., "--client-console")
  - `type`: Parameter type ("flag", "string", "int", "bytes", "path")
  - `value`: Parameter value (type depends on `type` field)
  - `enabled`: Whether this parameter is active

### POST /custom-variant/config (protected)

Update custom variant configuration. Validates configuration before saving.

**Headers:**
- `X-API-KEY`: API key for authentication
- `Content-Type`: application/json

Body:
```json
{
  "enabled": true,
  "platform": "amd64",
  "arm_version": "3.2.13",
  "parameters": [
    {
      "name": "--client-console",
      "type": "flag",
      "value": true,
      "enabled": true
    }
  ]
}
```

Response:
```json
{
  "message": "Configuration saved successfully",
  "config": { ... }
}
```

**Validation:**
- Platform must be one of: "amd64", "arm32", "arm64"
- ARM version must be one of: "3.2.13", "3.2.14" (only for ARM platforms)
- Parameter types must be valid: "flag", "string", "int", "bytes", "path"
- Parameter values must match their declared type

**Error Responses:**
- `400 Bad Request`: Invalid configuration (validation failed)
- `500 Internal Server Error`: Failed to save configuration

### POST /custom-variant/reprovision (protected)

Delete all engines and reprovision them with current custom variant settings. This is a potentially disruptive operation that interrupts all active streams.

**Headers:**
- `X-API-KEY`: API key for authentication

Response:
```json
{
  "message": "Started reprovisioning of 5 engines",
  "deleted_count": 5
}
```

**Process:**
1. All existing engines are stopped and removed
2. Engine state is cleared
3. Custom variant config is reloaded from disk
4. Minimum replicas are provisioned with new settings

**Note:** This operation runs entirely in the background as a non-blocking task. The endpoint returns immediately after starting the operation. The API and UI remain fully accessible during reprovisioning. Use `GET /custom-variant/reprovision/status` to check the progress.


## Event Logging

The event logging system tracks significant operational events for transparency and traceability.

### GET /events

Retrieve application events with optional filtering and pagination.

**Query Parameters:**
- `limit` (int, optional): Maximum number of events to return (1-1000, default: 100)
- `offset` (int, optional): Pagination offset (default: 0)
- `event_type` (string, optional): Filter by event type - one of: `engine`, `stream`, `vpn`, `health`, `system`
- `category` (string, optional): Filter by category (e.g., "created", "deleted", "started", "ended", "failed", "recovered")
- `container_id` (string, optional): Filter by container ID
- `stream_id` (string, optional): Filter by stream ID
- `since` (datetime, optional): Only return events after this timestamp (ISO 8601 format)

Response:
```json
[
  {
    "id": 123,
    "timestamp": "2025-11-21T12:18:47.502472",
    "event_type": "health",
    "category": "warning",
    "message": "High proportion of unhealthy engines: 1/1 (>30%)",
    "details": {
      "unhealthy_count": 1,
      "total_engines": 1,
      "percentage": 100.0
    },
    "container_id": null,
    "stream_id": null
  }
]
```

**Event Types:**
- `engine`: Engine provisioning, deletion, and lifecycle events
- `stream`: Stream start and end events
- `vpn`: VPN connection, disconnection, and recovery events
- `health`: Health check warnings and failures
- `system`: Auto-scaling and system-level events

**Common Categories:**
- `created`, `deleted`: Resource lifecycle
- `started`, `ended`: Operation lifecycle
- `connected`, `disconnected`: Connection states
- `failed`, `recovered`: Failure and recovery
- `warning`: Warning conditions
- `scaling`: Auto-scaling operations

### GET /events/stats

Get statistics about logged events.

Response:
```json
{
  "total": 15,
  "by_type": {
    "engine": 1,
    "stream": 1,
    "vpn": 1,
    "health": 8,
    "system": 4
  },
  "oldest": "2025-11-21T12:17:45.677028",
  "newest": "2025-11-21T12:18:47.502472"
}
```

### POST /events/cleanup (protected)

Manually trigger cleanup of old events.

**Headers:**
- `X-API-KEY`: API key for authentication

**Query Parameters:**
- `max_age_days` (int, optional): Delete events older than this many days (default: 30, minimum: 1)

Response:
```json
{
  "deleted": 42,
  "message": "Cleaned up 42 events older than 30 days"
}
```

**Note:** Events are also automatically cleaned up when the total count exceeds 10,000 or when events are older than 30 days.
