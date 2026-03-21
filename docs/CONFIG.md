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

---

## Proxy Settings
- **Stream Mode**: Toggle between `TS` (MPEG-TS) and `HLS`.
- **Connection Timouts**: Various timeouts for client/stream/chunk handling.
- **Buffer Settings**: Initial chunk buffer and chunk sizes.

---

## Advanced Management (CLI/Env)
While UI is preferred, environmental variables can still be passed to the container for initial deployment or overriding:
- `API_KEY`: API Bearer for protected endpoints.
- `DOCKER_NETWORK`: Specify a custom network for engines.
- `DB_URL`: Backend database path (Default: `sqlite:///./orchestrator.db`).
