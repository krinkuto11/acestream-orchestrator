# Configuration (.env)

Variables and default values:

- `APP_PORT=8000`
- `DOCKER_NETWORK=` Docker network name. Empty → default network.
- `ENGINE_VARIANT=krinkuto11-amd64` AceStream engine variant. Options: `krinkuto11-amd64`, `jopsis-amd64`, `jopsis-arm32`, `jopsis-arm64`. Each variant uses its predefined Docker image.
- `ENGINE_ARM32_VERSION=arm32-v3.2.13` Docker image version tag for ARM32 variant. Used when `ENGINE_VARIANT=jopsis-arm32`.
- `ENGINE_ARM64_VERSION=arm64-v3.2.13` Docker image version tag for ARM64 variant. Used when `ENGINE_VARIANT=jopsis-arm64`.
- `MIN_REPLICAS=1` · `MAX_REPLICAS=20` (MIN_REPLICAS must be >= 1 to ensure at least 1 free replica)
- `CONTAINER_LABEL=ondemand.app=myservice` management label.
- `STARTUP_TIMEOUT_S=25` max container startup time.
- `IDLE_TTL_S=600` reserved for inactivity GC.

Collector:
- `COLLECT_INTERVAL_S=5`
- `STATS_HISTORY_MAX=720` samples stored in memory per stream.

Monitoring:
- `MONITOR_INTERVAL_S=10` Docker container monitoring interval.
- `ENGINE_GRACE_PERIOD_S=30` grace period before stopping empty engines.
- `AUTOSCALE_INTERVAL_S=30` autoscaling check interval.

Gluetun VPN Integration:
- `GLUETUN_CONTAINER_NAME=` Name of Gluetun VPN container. When set, all AceStream engines will route through this VPN container.
- `GLUETUN_HEALTH_CHECK_INTERVAL_S=5` Frequency of VPN health checks (in seconds). High frequency is recommended for availability.
- `VPN_RESTART_ENGINES_ON_RECONNECT=true` If `true`, restarts all engines when VPN reconnects after a disconnection.

Ports:
- `PORT_RANGE_HOST=19000-19999` available host ports.
- `ACE_HTTP_RANGE=40000-44999` internal ports for `--http-port`.
- `ACE_HTTPS_RANGE=45000-49999` internal ports for `--https-port`.
- `ACE_MAP_HTTPS=false` if `true` also maps HTTPS to host.

Security:
- `API_KEY=...` API Bearer for `/provision/*` and `/events/*`.

Persistence:
- `DB_URL=sqlite:///./orchestrator.db`

Auto-GC:
- `AUTO_DELETE=false` if `true`, deletes container on `stream_ended`.

Labels on created containers:
- `acestream.http_port=<int>`
- `acestream.https_port=<int>`
- `host.http_port=<int>`
- `host.https_port=<int>` optional if `ACE_MAP_HTTPS=true`
- and `CONTAINER_LABEL` (key=value) to identify managed ones.
