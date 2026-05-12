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

## Settings
### GET /settings/vpn

Returns persisted VPN configuration including static and dynamic VPN management fields.

### POST /settings/vpn

Updates VPN configuration at runtime and persists it.

Request fields (all optional):
- `enabled`
- `vpn_mode` (`single` or `redundant`)
- `container_name`
- `container_name_2`
- `api_port`
- `port_range_1`
- `port_range_2`
- `health_check_interval_s`
- `port_cache_ttl_s`
- `restart_engines_on_reconnect`
- `unhealthy_restart_timeout_s`
- `dynamic_vpn_management`
- `providers` (list of provider names, e.g. `protonvpn`, `mullvad`)
- `protocol` (`wireguard` or `openvpn`)
- `regions` (list of regions/countries/cities, can be prefixed as `country:`, `city:`, `region:`, `hostname:`)
- `credentials` (list of JSON objects used for dynamic VPN node provisioning)
- `trigger_migration` (optional bool; when toggling VPN state, marks engines that do not match the new target state as draining so new streams migrate without dropping active sessions)
- `vpn_servers_auto_refresh` (bool; enables scheduled server-list refresh)
- `vpn_servers_refresh_period_s` (int; refresh interval in seconds, minimum 60)
- `vpn_servers_refresh_source` (`proton_paid` or `gluetun_official`)
- `vpn_servers_gluetun_json_mode` (`none`, `update`, or `replace`)
- `vpn_servers_storage_path` (optional directory path to write server list files)
- `vpn_servers_official_url` (optional URL for official Gluetun catalog source)
- `vpn_servers_proton_credentials_source` (`env` or `settings`)
- `vpn_servers_proton_username_env`, `vpn_servers_proton_password_env`, `vpn_servers_proton_totp_code_env`, `vpn_servers_proton_totp_secret_env`
- `vpn_servers_proton_username`, `vpn_servers_proton_password`, `vpn_servers_proton_totp_code`, `vpn_servers_proton_totp_secret`
- Proton filter controls: `vpn_servers_filter_ipv6`, `vpn_servers_filter_secure_core`, `vpn_servers_filter_tor`, `vpn_servers_filter_free_tier` (`include|exclude|only`)

Example dynamic request payload:

```json
{
  "enabled": true,
  "dynamic_vpn_management": true,
  "protocol": "wireguard",
  "providers": ["protonvpn"],
  "regions": ["country:Spain", "country:France"],
  "credentials": [
    {
      "provider": "protonvpn",
      "protocol": "wireguard",
      "wireguard_private_key": "<base64-private-key-1>"
    },
    {
      "provider": "protonvpn",
      "protocol": "wireguard",
      "wireguard_private_key": "<base64-private-key-2>"
    }
  ]
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

 - GET /ace/preflight?id=<content_id>&tier=light|deep
   - Runs short availability probing without opening a long-lived proxy client session.
   - Uses least-loaded engine selection (same balancing policy as stream startup).
   - Works in both control paths:
     - `http`: probes through `/ace/getstream` with optional short status check in `deep` tier.
     - `api`: probes through API port (`HELLOBG/READY/LOADASYNC/START/STATUS/STOP`).
   - Supports two tiers:
     - `light`: resolve/canonicalize only (fast)
     - `deep`: resolve + START + STATUS/livepos sampling + STOP (richer diagnostics)
   - In `api` control mode:
     - Uses numeric `LOADASYNC` session IDs.
     - Canonicalizes aliases to resolved `infohash` before START.
     - Parses `STATUS` and `EVENT livepos` with HTTPAceProxy-compatible rules.
   - Backward compatibility: legacy values (`LEGACY_HTTP`, `LEGACY_API`) are accepted and normalized.
   - Response shape:
   ```json
   {
     "control_mode": "api",
     "tier": "deep",
     "engine": {
       "container_id": "...",
       "host": "127.0.0.1",
       "port": 6878,
       "api_port": 62062,
       "forwarded": false
     },
     "result": {
       "available": true,
       "infohash": "...",
       "status_code": 1,
       "status_probe": {
         "status_text": "dl",
         "peers": 3,
         "http_peers": 0,
         "progress": 0,
         "livepos": {
           "pos": "...",
           "buffer_pieces": 15
         }
       }
     }
   }
   ```

 - POST /ace/monitor/legacy/start (protected)
   - Starts an async monitor session that uses `api` control flow only for telemetry.
   - Flow: `HELLOBG/READY/LOADASYNC/START`, then `STATUS` probe once per `interval_s`.
   - No player clients are attached and no stream data is proxied to consumers.
   - Monitor session state is tracked in orchestrator state (including selected engine and latest status).
   - Body:
   ```json
   {
     "monitor_id": "my-monitor-001",
     "content_id": "6422e8bc34282871634c81947be093c04ad1bb29",
     "stream_name": "Example Channel",
     "interval_s": 1.0,
     "run_seconds": 0,
     "per_sample_timeout_s": 1.0,
     "engine_container_id": null
   }
   ```
   - Notes:
     - `interval_s` minimum is `0.5` (recommended: `1.0`).
     - `run_seconds=0` means run until manually stopped.
     - `engine_container_id` is optional; if omitted, engine selection uses the same balancing strategy as proxy stream allocation.
     - `stream_name` is optional and is persisted in monitor sessions (useful when sessions are created from playlist entries).
    - `monitor_id` is optional; when provided and a monitor with that ID already exists, the existing session is returned and no new monitor is started.

 - GET /ace/monitor/legacy (protected)
   - Lists all monitor sessions with latest STATUS sample and summary counters.
   - Includes engine assignment per monitor session and optional `stream_name` when provided at creation time.
   - Query params:
     - `include_recent_status=true|false` (default: `true`)
       - `true`: includes `recent_status` history in each monitor item.
       - `false`: omits `recent_status` and returns lightweight latest-status summaries.

 - POST /ace/monitor/legacy/parse-m3u (protected)
   - Parses uploaded/inline M3U content and extracts `acestream://<id>` entries.
   - Returns parsed stream names (from `#EXTINF`) and normalized content IDs.
   - Body:
   ```json
   {
     "m3u_content": "#EXTM3U\n#EXTINF:-1,My Stream\nacestream://aabb...\n"
   }
   ```
   - Response:
   ```json
   {
     "count": 1,
     "items": [
       {
         "content_id": "aabb...",
         "name": "My Stream",
         "line_number": "2"
       }
     ]
   }
   ```

 - GET /ace/monitor/legacy/{monitor_id} (protected)
   - Returns a single monitor session including `recent_status` history (in-memory ring buffer).
   - Query params:
     - `include_recent_status=true|false` (default: `true`)
       - `true`: includes `recent_status` history.
       - `false`: omits `recent_status` and returns a lightweight latest-status summary.
   - Includes `livepos_movement` summary with movement/stuck signals:
     - `is_moving`, `direction`, `pos_delta`, `last_ts_delta`, `downloaded_delta`, `movement_events`.
  - Sessions with non-moving `livepos` are labeled `status=stuck` and continue being monitored.
  - Sessions that timeout or fail to connect are marked `status=dead` and monitoring is stopped.

 - DELETE /ace/monitor/legacy/{monitor_id} (protected)
   - Stops a monitor session and closes the legacy API connection (`STOP` + `SHUTDOWN`).

 - DELETE /ace/monitor/legacy/{monitor_id}/entry (protected)
   - Stops the session (if still running) and removes the monitor entry from in-memory session list.

 - GET /ace/getstream?id=... (TS/HLS proxy)
   - If the requested content is already being monitored (`/ace/monitor/legacy`), proxy reuses that monitor session playback URL.
   - Reused sessions inherit monitor engine assignment (no duplicate START for the same content).
   - Stream stats remain visible under `/streams` by using monitor telemetry when direct legacy stat probing is unavailable.
   - In `HLS` mode:
     - `http` control mode uses the in-proxy HLS channel manager.
     - `api` control mode uses an external FFmpeg segmenter and returns rewritten manifests.

 - GET /ace/manifest.m3u8
   - HLS manifest entrypoint.
   - Serves manifests for both `http` and `api` control modes.

 - GET /api/v1/hls/{monitor_id}/{segment_filename}
   - Serves local FFmpeg-generated `.ts` segments for API-mode HLS sessions.
   - Returns `404` when segment/session is unavailable.

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

 - POST /proxy/migrate-stream (protected) → Trigger on-demand stream migration
   - Triggers TS/HLS stream migration via proxy manager without waiting for background draining reconciliation.
   - Body:
   ```json
   {
     "stream_key": "b3bdd6ef7f795c4f321a3ce5cf4907338f462929",
     "old_container_id": "optional-source-container-id",
     "new_container_id": "optional-target-container-id"
   }
   ```
   - Notes:
     - `stream_key` is required.
     - If `new_container_id` is omitted, orchestrator auto-selects a healthy target engine.
     - If `old_container_id` is provided, selection avoids reusing that engine.
   - Response:
   ```json
   {
     "migrated": true,
     "stream_key": "b3bdd6ef7f795c4f321a3ce5cf4907338f462929",
     "old_container_id": "...",
     "new_container_id": "...",
     "state_streams_reassigned": 1
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
   - `labels` may include legacy diagnostics fields when available:
     - `proxy.control_mode`: control path used for stream startup (`http` or `api`)
     - `stream.resolved_infohash`: canonical infohash used for START in legacy API flow
     - `stream.status_text`, `stream.peers`, `stream.http_peers`, `stream.progress`: best-effort probe values from startup sampling

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

 - POST /vpn/proton/refresh (protected) → Refresh Proton paid server catalog with token-based 2FA support
   - Fetches Proton logical servers using username/password and optional TOTP
   - Writes `servers-proton.json` and optionally updates `servers.json`
   - Request body fields:
     - `proton_username`, `proton_password` (optional if provided via env/secrets)
     - `proton_totp_code` (one-time 2FA token) or `proton_totp_secret` (base32 secret for code generation)
     - `storage_path` (directory path where server files will be written)
     - `gluetun_json_mode` (`none`, `replace`, or `update`)
     - filters: `ipv6`, `secure_core`, `tor`, `free_tier` (`include|exclude|only`)

 - POST /vpn/servers/refresh (protected) → Refresh VPN server list from configured source
   - Uses persisted VPN refresh settings by default
   - Optional body overrides:
     - `source` (`proton_paid` or `gluetun_official`)
     - `gluetun_json_mode` (`none`, `update`, or `replace`)
     - `reason` (string for audit/status)

 - GET /vpn/servers/refresh/status → Current scheduler status and last refresh result
   - Includes whether scheduler is running, in-progress state, last success/error, and effective config snapshot

 - GET /health/status → Detailed health status and management information
   - Returns comprehensive health summary including healthy/unhealthy engine counts
   - Includes proactive health management status

 - GET /orchestrator/status → Comprehensive orchestrator status for proxy integration (cached for 2s)
   - Provides all information a proxy needs including VPN, provisioning, and health status
   - Includes detailed provisioning status with recovery guidance
   - Returns capacity information, circuit breaker state, and system configuration

 - GET /cache/stats → Cache statistics for monitoring and debugging
   - Returns cache hit/miss rates, entry counts, and memory usage

### Control
 - DELETE /containers/{container_id} (protected)
 - GET /containers/{container_id}/logs (protected)
   - Returns recent Docker logs for a container
   - Query params:
     - `tail` (optional, default: 200, max: 2000): number of log lines
     - `since_seconds` (optional): return only logs newer than this age in seconds
     - `timestamps` (optional, default: false): include Docker timestamps per line
 - POST /gc (protected)
 - POST /scale/{demand:int} (protected)
 - POST /health/circuit-breaker/reset?operation_type= (protected) → Reset circuit breakers for manual intervention
 - POST /cache/clear (protected) → Manually clear all cache entries

Container inspect payload (`GET /containers/{container_id}`) includes:
- `restart_count` - Docker restart count for container pedigree/diagnostics

### Metrics
 - GET /metrics Prometheus:

   **Orchestrator gauges** (updated every 5 s by the background collector):
   - `orch_active_streams` — active streams count
   - `orch_engines_total` — total known engines
   - `orch_engines_healthy` — healthy engines
   - `orch_engines_unhealthy` — unhealthy engines
   - `orch_engines_draining` — draining engines
   - `orch_engines_used` — engines with at least one active stream
   - `orch_vpn_nodes_total` — total VPN nodes
   - `orch_vpn_nodes_healthy` — healthy VPN nodes
   - `orch_vpn_nodes_draining` — draining VPN nodes

   **Proxy metrics** (updated per request/connection in the proxy hot path):
   - `acestream_proxy_http_requests_total{mode,endpoint,status_code}` — request counter
   - `acestream_proxy_http_request_duration_seconds{mode,endpoint}` — response latency histogram
   - `acestream_proxy_http_ttfb_seconds{mode,endpoint}` — TTFB histogram
   - `acestream_proxy_active_sessions{mode}` — current active sessions by mode (`ts` / `hls`)
   - `acestream_proxy_bytes_ingress_total{mode}` — bytes received from engines
   - `acestream_proxy_bytes_egress_total{mode}` — bytes sent to clients
   - `acestream_proxy_connections_total{mode}` — total connections established

   **Control-plane counters/gauges** (updated by the engine controller):
   - `cp_engines_total{status}` — engine count by status (`healthy` / `unhealthy` / `unknown`)
   - `cp_desired_replicas` — current desired replica target
   - `cp_provisioning_total{result}` — provisioning outcomes (`success` / `failed` / `blocked` / `evicted`)
   - `cp_reconcile_total` — reconciliation loop invocations
   - `cp_health_check_total{status}` — health check results (`healthy` / `unhealthy`)
   - `cp_vpn_nodes_total{condition}` — VPN node count by condition (`healthy` / `draining` / `unhealthy`)
   - `cp_intent_queue_depth` — pending scaling intents
   - `cp_circuit_breaker_open{name}` — circuit breaker open state (`1` = open, `0` = closed)

 - GET /metrics/dashboard
   - Structured JSON snapshot used by the panel dashboard.
   - Query params:
     - `window_seconds` (optional): observation window in seconds. Range `60..604800`. Default: `900`.
   - Includes categories: `north_star`, `proxy`, `engines`, `streams`, `docker`.
   - Includes `observation_window_seconds` to reflect the effective window used.
   - `proxy.throughput` includes:
     - `ingress_mbps` / `egress_mbps` — current rates derived from active stream stats
   - Suitable for platform-agnostic dashboards and API consumers that prefer JSON over Prometheus text format.

Example:
```bash
curl "http://localhost:8000/metrics/dashboard?window_seconds=3600"
```

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
- `Authorization`: `Bearer <API_KEY>`
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
- `Authorization`: `Bearer <API_KEY>`

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
- `Authorization`: `Bearer <API_KEY>`

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
- `Authorization`: `Bearer <API_KEY>`

### PATCH /custom-variant/templates/{slot_id}/rename (protected)

Rename a template.

**Headers:**
- `Authorization`: `Bearer <API_KEY>`

Body:
```json
{
  "name": "New Template Name"
}
```

### POST /custom-variant/templates/{slot_id}/activate (protected)

Activate a template (load it as current config).

**Headers:**
- `Authorization`: `Bearer <API_KEY>`

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
- `Authorization`: `Bearer <API_KEY>`

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
- `Authorization`: `Bearer <API_KEY>`

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
