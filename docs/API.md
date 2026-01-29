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
### Stream Management Operations

 - DELETE /streams/{stream_id} (protected) → Stop a single stream
   - Stops a stream by calling its command URL with method=stop
   - Marks the stream as ended in state
   - Returns: `{"message": "Stream stopped successfully", "stream_id": "..."}`

 - POST /streams/batch-stop (protected) → Stop multiple streams in batch
   - Body: Array of command URLs
   ```json
   [
     "http://127.0.0.1:19023/ace/cmd/...",
     "http://127.0.0.1:19024/ace/cmd/..."
   ]
   ```
   - Response:
   ```json
   {
     "total": 2,
     "success_count": 2,
     "failure_count": 0,
     "results": [
       {
         "command_url": "http://127.0.0.1:19023/ace/cmd/...",
         "success": true,
         "message": "Stream stopped successfully",
         "stream_id": "ch-42"
       },
       {
         "command_url": "http://127.0.0.1:19024/ace/cmd/...",
         "success": true,
         "message": "Stream stopped successfully",
         "stream_id": "ch-43"
       }
     ]
   }
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
   - By default, returns all streams currently in memory (active streams only)
   - **IMPORTANT**: Ended streams are immediately removed from memory when they end
   - Use `status=started` to explicitly get active streams (same as default)
   - Use `status=ended` to get ended streams (will typically return empty list since ended streams are immediately removed from memory)
   - Can filter by `container_id` to get streams for a specific engine
   - **Note**: A backup cleanup routine runs every 5 minutes to catch any streams that failed immediate removal

 - GET /streams/{stream_id}/stats?since=<ISO8601> → StreamStatSnapshot[]

 - GET /streams/{stream_id}/extended-stats → Extended stream metadata from AceStream engine
   - Returns additional metadata like content_type, title, is_live, mime, categories, etc.
   - Queries the AceStream analyze_content API for the stream

 - GET /streams/{stream_id}/livepos → Live position data for a stream
   - Returns livepos information including current position, buffer, and timestamps
   - Only applicable for live streams

 - GET /engines/stats/all → Docker stats for all engines (instant response from background collector)
   - Returns cached statistics from the background collector
   - Includes CPU, memory, network, and block I/O stats for each engine

 - GET /engines/stats/total → Aggregated Docker stats across all engines (instant response)
   - Returns total CPU, memory, network, and block I/O stats
   - Data is aggregated from background collector

 - GET /engines/{container_id}/stats → Docker stats for a specific engine (instant response)
   - Returns cached statistics for a single engine from background collector

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

 - GET /vpn/publicip → Get VPN public IP address
   - Returns the public IP address of the VPN connection

 - GET /health/status → Detailed health status and management information
   - Returns comprehensive health summary including healthy/unhealthy engine counts
   - Includes proactive health management status

 - GET /orchestrator/status → Comprehensive orchestrator status for proxy integration (cached for 2s)
   - Provides all information a proxy needs including VPN, provisioning, and health status
   - Includes detailed provisioning status with recovery guidance
   - Returns capacity information, circuit breaker state, and system configuration
   - Includes deprecated Acexy sync status for backwards compatibility

 - GET /acexy/status → **DEPRECATED** Acexy proxy integration status (for backwards compatibility)
   - Returns deprecation status indicating stream state is managed via stat URL checking
   ```json
   {
     "enabled": false,
     "deprecated": true,
     "message": "Acexy sync is deprecated. Stream state managed via stat URL checking.",
     "url": null,
     "healthy": null,
     "last_health_check": null,
     "sync_interval_seconds": null
   }
   ```
   
   **Note:** The acexy proxy is now stateless and only sends stream started events. 
   Stream lifecycle is managed by Acexy itself.

 - GET /cache/stats → Cache statistics for monitoring and debugging
   - Returns cache hit/miss rates, entry counts, and memory usage

### Control
 - DELETE /containers/{container_id} (protected)
 - POST /gc (protected)
 - POST /scale/{demand:int} (protected)
 - POST /health/circuit-breaker/reset?operation_type= (protected) → Reset circuit breakers for manual intervention
 - POST /cache/clear (protected) → Manually clear all cache entries

### Metrics
 - GET /metrics Prometheus:
   - orch_events_started_total
   - orch_events_ended_total
   - orch_collect_errors_total
   - orch_streams_active
   - orch_provision_total{kind="generic|acestream"}
   - orch_total_uploaded_bytes - Cumulative bytes uploaded from all engines (all-time)
   - orch_total_downloaded_bytes - Cumulative bytes downloaded from all engines (all-time)
   - orch_total_uploaded_mb - Cumulative MB uploaded from all engines
   - orch_total_downloaded_mb - Cumulative MB downloaded from all engines
   - orch_total_upload_speed_mbps - Current sum of upload speeds in MB/s
   - orch_total_download_speed_mbps - Current sum of download speeds in MB/s
   - orch_total_peers - Current total peers across all engines
   - orch_total_streams - Current number of active streams
   - orch_healthy_engines - Number of healthy engines
   - orch_unhealthy_engines - Number of unhealthy engines
   - orch_used_engines - Number of engines currently handling streams
   - orch_vpn_health - Current health status of primary VPN container
   - orch_vpn1_health - Health status of VPN1 container (redundant mode)
   - orch_vpn2_health - Health status of VPN2 container (redundant mode)
   - orch_vpn1_engines - Number of engines assigned to VPN1
   - orch_vpn2_engines - Number of engines assigned to VPN2
   - orch_extra_engines - Number of engines beyond MIN_REPLICAS

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

### GET /custom-variant/reprovision/status

Get current reprovisioning status.

Response:
```json
{
  "in_progress": false,
  "status": "idle",
  "message": null,
  "timestamp": null,
  "total_engines": 0,
  "engines_stopped": 0,
  "engines_provisioned": 0,
  "current_engine_id": null,
  "current_phase": null
}
```

**Status Values:**
- `idle`: No reprovisioning in progress
- `in_progress`: Reprovisioning currently running
- `success`: Last reprovisioning completed successfully
- `error`: Last reprovisioning failed

**Phases:**
- `preparing`: Initial phase
- `stopping`: Stopping existing engines
- `cleaning`: Cleaning up state
- `provisioning`: Provisioning new engines
- `complete`: Reprovisioning completed
- `error`: Error occurred

## Template Management

The orchestrator supports 10 template slots for saving and managing custom engine variant configurations.

### GET /custom-variant/templates

List all template slots with metadata.

Response:
```json
{
  "templates": [
    {
      "slot_id": 1,
      "name": "My Template",
      "exists": true
    },
    {
      "slot_id": 2,
      "name": "Template 2",
      "exists": false
    }
  ],
  "active_template_id": 1
}
```

### GET /custom-variant/templates/{slot_id}

Get a specific template by slot ID (1-10).

Response:
```json
{
  "slot_id": 1,
  "name": "My Template",
  "config": {
    "enabled": true,
    "platform": "amd64",
    "parameters": [...]
  }
}
```

### POST /custom-variant/templates/{slot_id} (protected)

Save a template to a specific slot (1-10).

**Headers:**
- `X-API-KEY`: API key for authentication

Body:
```json
{
  "name": "My Template",
  "config": {
    "enabled": true,
    "platform": "amd64",
    "parameters": [...]
  }
}
```

### DELETE /custom-variant/templates/{slot_id} (protected)

Delete a template from a specific slot. Cannot delete the currently active template.

**Headers:**
- `X-API-KEY`: API key for authentication

### PATCH /custom-variant/templates/{slot_id}/rename (protected)

Rename a template.

**Headers:**
- `X-API-KEY`: API key for authentication

Body:
```json
{
  "name": "New Template Name"
}
```

### POST /custom-variant/templates/{slot_id}/activate (protected)

Activate a template (load it as current config).

**Headers:**
- `X-API-KEY`: API key for authentication

Response:
```json
{
  "message": "Template 'My Template' activated successfully",
  "template_id": 1,
  "template_name": "My Template"
}
```

### GET /custom-variant/templates/{slot_id}/export

Export a template as JSON file.

Response: JSON file download with template data

### POST /custom-variant/templates/{slot_id}/import (protected)

Import a template from JSON.

**Headers:**
- `X-API-KEY`: API key for authentication

Body:
```json
{
  "json_data": "{\"slot_id\": 1, \"name\": \"Imported\", \"config\": {...}}"
}
```


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
