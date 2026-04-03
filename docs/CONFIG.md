# Settings Reference

The AceStream Orchestrator uses a UI-driven configuration system. All settings are persisted to JSON files (`app/config/orchestrator_settings.json` and `vpn_settings.json`) and can be managed via the **Settings** dashboard.

## Orchestrator Settings

### Engine Lifecycle
- **Startup Timeout (s)**: Max time to wait for an engine to become ready. Default: 25s.
- **Idle Engine TTL (s)**: How long an idle engine lives before being cleaned up. Default: 600s.
- **Engine Grace Period (s)**: Delay before stopping an engine after its last stream ends. Default: 30s.
- **Autoscale Check Interval (s)**: How often to evaluate and adjust engine count. Default: 30s.
- **Debug Mode**: Enables verbose logging for troubleshooting.

### Health Management (Expert)
- **Health Check Interval (s)**: How often engine health is evaluated. Default: 20s.
- **Failure Threshold**: Consecutive failures before an engine is marked unhealthy. Default: 3.
- **Unhealthy Grace Period (s)**: Time before replacing an unhealthy engine. Default: 60s.
- **Replacement Cooldown (s)**: Minimum time between engine replacements. Default: 60s.

### Circuit Breaker (Expert)
- **Failure Threshold**: Failures before opening the circuit breaker for provisioning. Default: 5.
- **Recovery Timeout (s)**: Time before attempting recovery. Default: 300s.
- **Replacement Failure Threshold**: Failed replacements before circuit opens. Default: 3.

### Port Ranges
- **Host Port Range**: Dynamic ports used on the Docker host. Default: `19000-19999`.
- **AceStream HTTP Range**: Range for internal HTTP ports. Default: `40000-44999`.
- **AceStream HTTPS Range**: Range for internal HTTPS ports. Default: `45000-49999`.

---

## VPN Settings (Gluetun)

### Connection
- **VPN Mode**: `single` (one container) or `redundant` (two containers).
- **Container Name(s)**: Docker container name(s) of the Gluetun VPN. Default: `gluetun`.
- **Gluetun HTTP API Port**: Must match `HTTP_CONTROL_SERVER_ADDRESS` in Gluetun. Default: `8001`.

### Health & Recovery (Expert)
- **Health Check Interval (s)**: How often to check VPN health. Default: 5s.
- **Port Cache TTL (s)**: How long to cache forwarded port info. Default: 60s.
- **Unhealthy Restart Timeout (s)**: Force-restart VPN container after this period of unhealthiness. Default: 60s.
- **Restart Engines on VPN Reconnect**: Refresh engine routes when VPN reconnects. Default: `true`.

### Dynamic VPN Management
- **Dynamic VPN Management**: Enables controller-managed Gluetun nodes provisioned at runtime from credential pool.
- **Providers**: Preferred VPN providers for dynamic provisioning.
- **Protocol**: `wireguard` or `openvpn`.
- **Regions**: Placement filters (countries/cities/regions/hostnames).
- **Credentials**: Finite credential pool used for lease-based VPN node provisioning.
- **Preferred Engines per VPN**: Density target used by the controller to compute desired VPN count.

When dynamic VPN management is enabled, desired VPN nodes are computed as:

- `Required_VPNs = ceil(Total_Engines / Preferred_Engines_Per_VPN)`
- `Desired_VPNs = min(Required_VPNs, Total_Credentials)`

If engine demand exceeds credential capacity, engine density per VPN increases automatically (dynamic density behavior).

---

## Proxy Settings
- **Stream Mode**: `TS` (MPEG-TS) or `HLS`.
- **Engine Control Mode**: `http` (default) or `api` (socket control).
- **Connection Timeouts**: Timeouts for client/stream/chunk handling.
- **Buffer Settings**: Initial chunk buffer and chunk sizes.

### Proxy Mode Compatibility

- `HLS` is supported in both control modes.
- Legacy aliases (`LEGACY_HTTP`, `LEGACY_API`) are accepted and normalized to canonical values.

The API validates control mode values and normalizes legacy aliases for backward compatibility.

### Preflight Diagnostics

Proxy settings include a preflight diagnostic tool (Settings > Proxy) backed by:

- `GET /ace/preflight?id=<content_id>&tier=light|deep`

Tier behavior:

- `light`: resolve and canonicalize only
- `deep`: resolve + start + short status/livepos sampling + stop

Use preflight to validate content before opening client sessions, especially when tuning `api` behavior.

---

## Environment Variable Reference

All settings can be supplied as environment variables. Variables marked **[UI]** are also configurable at runtime via the Settings dashboard and will be persisted to `app/config/*.json`. The env var value is used on container start; the UI value overrides it after the first save.

A ready-to-use template with all variables is provided in [`.env.example`](../.env.example).

### Security

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | *(none)* | Bearer token required for protected endpoints. Auth header: `Authorization: Bearer <token>`. Leave empty to disable authentication. |

### Application

| Variable | Default | Description |
|---|---|---|
| `APP_PORT` | `8000` | Port the orchestrator API listens on inside the container. |
| `DOCKER_NETWORK` | *(none)* | Docker network to attach engine containers to. |
| `CONTAINER_LABEL` | `ondemand.app=myservice` | Docker label (`key=value`) applied to managed engine containers. |
| `DB_URL` | `sqlite:///./orchestrator.db` | SQLAlchemy database URL. |
| `AUTO_DELETE` | `true` | Delete idle engines automatically when `IDLE_TTL_S` elapses. |
| `M3U_TIMEOUT` | `15` | Seconds to wait when fetching an M3U playlist. |
| `DEBUG_MODE` | `false` | **[UI]** Enable verbose debug logging. |
| `MONITOR_INTERVAL_S` | `10` | Seconds between Docker container state polls. |

### Engine Lifecycle

| Variable | Default | Description |
|---|---|---|
| `MIN_REPLICAS` | `2` | **[UI]** Minimum engine containers to keep running at all times. |
| `MIN_FREE_REPLICAS` | `1` | **[UI]** Minimum idle (unassigned) engines to maintain. |
| `MAX_REPLICAS` | `6` | **[UI]** Maximum concurrent engine containers. |
| `STARTUP_TIMEOUT_S` | `25` | **[UI]** Seconds to wait for a new engine to become healthy. |
| `IDLE_TTL_S` | `600` | **[UI]** Seconds before an idle engine is terminated. |
| `ENGINE_GRACE_PERIOD_S` | `30` | **[UI]** Seconds after last stream ends before stopping an engine. |
| `AUTOSCALE_INTERVAL_S` | `30` | **[UI]** Seconds between autoscaler evaluation cycles. |
| `MAX_STREAMS_PER_ENGINE` | `3` | **[UI]** Maximum streams routed to a single engine simultaneously. |
| `MAX_CONCURRENT_PROVISIONS` | `5` | Maximum number of engines that can be provisioned concurrently. |
| `MIN_PROVISION_INTERVAL_S` | `0.5` | Minimum seconds between consecutive provision requests. |

### Engine Image

| Variable | Default | Description |
|---|---|---|
| `ENGINE_VARIANT` | *(arch-detected)* | Built-in variant: `AceServe-amd64`, `AceServe-arm32`, `AceServe-arm64`, or `krinkuto11-amd64`. |
| `ENGINE_ARM32_VERSION` | `arm32-v3.2.13` | AceServe ARM32 image tag. |
| `ENGINE_ARM64_VERSION` | `arm64-v3.2.13` | AceServe ARM64 image tag. |
| `ENGINE_MEMORY_LIMIT` | *(none)* | Docker memory limit per engine container (e.g. `512m`, `2g`). |

### Port Ranges

| Variable | Default | Description |
|---|---|---|
| `PORT_RANGE_HOST` | `19000-19999` | **[UI]** Host-side port range allocated to engine containers. Must match Gluetun port mappings. |
| `ACE_HTTP_RANGE` | `40000-44999` | **[UI]** Internal AceStream HTTP port range (container side). |
| `ACE_HTTPS_RANGE` | `45000-49999` | **[UI]** Internal AceStream HTTPS port range (container side). |
| `ACE_MAP_HTTPS` | `false` | Also expose the HTTPS port on the host. |

### VPN (Gluetun)

Orchestrator-managed dynamic VPN provisioning is the recommended mode.

| Variable | Default | Description |
|---|---|---|
| `DYNAMIC_VPN_MANAGEMENT` | `true` | **[UI]** Enable orchestrator-managed dynamic Gluetun node lifecycle. |
| `VPN_PROVIDER` | `protonvpn` | **[UI]** Default provider for dynamic VPN nodes. |
| `VPN_PROTOCOL` | `wireguard` | **[UI]** Default protocol for dynamic VPN nodes (`wireguard` or `openvpn`). |
| `PREFERRED_ENGINES_PER_VPN` | `10` | **[UI]** Target engines per dynamic VPN node; controller scales node count from this density. |
| `GLUETUN_API_PORT` | `8001` | **[UI]** Control-server port used by the orchestrator to query Gluetun nodes. |
| `GLUETUN_HEALTH_CHECK_INTERVAL_S` | `5` | **[UI]** Seconds between Gluetun health polls. |
| `GLUETUN_PORT_CACHE_TTL_S` | `60` | **[UI]** Seconds to cache the forwarded port value. |
| `VPN_RESTART_ENGINES_ON_RECONNECT` | `true` | **[UI]** Restart VPN-attached engines when the VPN reconnects. |
| `VPN_UNHEALTHY_RESTART_TIMEOUT_S` | `60` | **[UI]** Seconds of VPN unhealthiness before force-restarting the container. |
| `GLUETUN_CONTAINER_NAME` | *(none)* | Deprecated static fallback: attach engines to a pre-existing Gluetun container. |
| `GLUETUN_PORT_RANGE_1` | *(none)* | Host port sub-range assigned to the primary VPN (redundant mode, e.g. `19000-19499`). |
| `GLUETUN_PORT_RANGE_2` | *(none)* | Host port sub-range assigned to the secondary VPN (redundant mode, e.g. `19500-19999`). |

### Health Monitoring

| Variable | Default | Description |
|---|---|---|
| `HEALTH_CHECK_INTERVAL_S` | `20` | **[UI]** Seconds between engine health checks. |
| `HEALTH_FAILURE_THRESHOLD` | `3` | **[UI]** Consecutive failures before marking an engine unhealthy. |
| `HEALTH_UNHEALTHY_GRACE_PERIOD_S` | `60` | **[UI]** Seconds before replacement is triggered for an unhealthy engine. |
| `HEALTH_REPLACEMENT_COOLDOWN_S` | `60` | **[UI]** Minimum seconds between engine replacement attempts. |

### Circuit Breaker

| Variable | Default | Description |
|---|---|---|
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | **[UI]** Provisioning failures before the circuit breaker opens. |
| `CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S` | `300` | **[UI]** Seconds before a tripped circuit breaker attempts recovery. |
| `CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD` | `3` | **[UI]** Replacement failures before the replacement circuit opens. |
| `CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S` | `180` | **[UI]** Seconds before a tripped replacement circuit attempts recovery. |

### Statistics & Metrics

| Variable | Default | Description |
|---|---|---|
| `COLLECT_INTERVAL_S` | `1` | Seconds between stats collection cycles. |
| `STATS_HISTORY_MAX` | `720` | Maximum in-memory stats samples retained per engine. |
| `DASHBOARD_DEFAULT_WINDOW_S` | `900` | Default dashboard time window in seconds. |
| `DASHBOARD_PERSIST_INTERVAL_S` | `5` | Seconds between dashboard metric persistence flushes. |
| `DASHBOARD_METRICS_RETENTION_HOURS` | `168` | Hours to retain dashboard metric history in the database. |
| `LEGACY_STATS_PROBE_WORKERS` | `8` | Concurrent workers for API-mode stats probing. |

### Stream Loop Detection

| Variable | Default | Description |
|---|---|---|
| `STREAM_LOOP_DETECTION_ENABLED` | `false` | **[UI]** Enable stale-stream detection. |
| `STREAM_LOOP_DETECTION_THRESHOLD_S` | `3600` | **[UI]** Seconds of inactivity on `live_last` before a stream is considered stale. |
| `STREAM_LOOP_CHECK_INTERVAL_S` | `10` | **[UI]** Seconds between loop-detection scan cycles. |
| `STREAM_LOOP_RETENTION_MINUTES` | `0` | **[UI]** Minutes to retain detected loop IDs in memory (`0` = indefinite). |

### Proxy

| Variable | Default | Description |
|---|---|---|
| `PROXY_STREAM_MODE` | `TS` | **[UI]** Stream delivery mode: `TS` (MPEG-TS) or `HLS`. |
| `PROXY_CONTROL_MODE` | `http` | **[UI]** Stream control path: `http` or `api`. Legacy aliases (`LEGACY_HTTP`, `LEGACY_API`) are accepted and normalized. |
| `PROXY_LEGACY_API_PREFLIGHT_TIER` | `light` | **[UI]** Preflight depth: `light` (resolve only) or `deep` (resolve + probe + stop). |
| `PROXY_CONNECTION_TIMEOUT` | `10` | Seconds before a connection attempt to an engine times out. |
| `PROXY_CLIENT_WAIT_TIMEOUT` | `30` | Seconds a client waits for an engine to accept the stream. |
| `PROXY_STREAM_TIMEOUT` | `60` | Seconds of silence on the stream before the proxy disconnects. |
| `PROXY_CHUNK_TIMEOUT` | `5` | Seconds to wait for a single chunk from the engine. |
| `PROXY_INITIAL_BEHIND_CHUNKS` | `4` | Number of chunks buffered before the first byte is sent to clients. |
| `PROXY_CHUNK_SIZE` | `8192` | Read chunk size in bytes. |
| `PROXY_BUFFER_CHUNK_SIZE` | `~1 MB` | Write buffer size in bytes. |
| `PROXY_MAX_RETRIES` | `3` | Retry attempts when an engine fails during streaming. |
| `PROXY_RETRY_WAIT_INTERVAL` | `0.5` | Seconds between retry attempts. |
| `PROXY_HEALTH_CHECK_INTERVAL` | `5` | Seconds between proxy-layer health checks. |
| `PROXY_GHOST_CLIENT_MULTIPLIER` | `5.0` | Inactivity multiplier (× heartbeat interval) before a client is considered a ghost. |
| `PROXY_INITIAL_DATA_WAIT_TIMEOUT` | `10` | Seconds to wait for the engine to produce initial stream data. |
| `PROXY_NO_DATA_TIMEOUT_CHECKS` | `60` | Number of consecutive no-data checks before aborting the stream. |
| `PROXY_NO_DATA_CHECK_INTERVAL` | `1.0` | Seconds between no-data checks. |
| `PROXY_GRACE_PERIOD` | `5` | Seconds before shutting down a channel after the last client disconnects. |
| `PROXY_INIT_TIMEOUT` | `30` | Seconds allowed for channel initialisation. |
| `PROXY_CLEANUP_INTERVAL` | `60` | Seconds between proxy housekeeping cycles. |
| `PROXY_CLEANUP_CHECK_INTERVAL` | `3` | Seconds between cleanup check ticks. |
| `PROXY_HEARTBEAT_INTERVAL` | `10` | Seconds between client heartbeat pings. |
| `PROXY_KEEPALIVE_INTERVAL` | `0.5` | Seconds between keep-alive writes to clients. |
| `PROXY_CLIENT_TTL` | `60` | Seconds before an idle proxy client record expires. |
| `PROXY_BUFFER_TTL` | `60` | Seconds before a buffer record expires. |
