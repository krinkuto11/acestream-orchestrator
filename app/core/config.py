import os
from pathlib import Path
from pydantic import BaseModel, validator, field_validator, model_validator
from dotenv import load_dotenv
from ..shared.proxy_modes import PROXY_MODE_HTTP, PROXY_MODE_API, normalize_proxy_mode
load_dotenv()

import platform as _platform
_machine = _platform.machine().lower()

# Determine a sensible default variant based on the platform to avoid 404 errors on first run
_default_variant = "AceServe-amd64"
if "aarch64" in _machine or "arm64" in _machine:
    _default_variant = "AceServe-arm64"
elif "arm" in _machine:
    _default_variant = "AceServe-arm32"

_default_db_path = (Path(__file__).resolve().parent.parent / "config" / "orchestrator.db").as_posix()
_default_db_url = f"sqlite:///{_default_db_path}"

class Cfg(BaseModel):
    APP_PORT: int = int(os.getenv("APP_PORT", 8000))
    DOCKER_NETWORK: str | None = os.getenv("DOCKER_NETWORK")
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    ENGINE_VARIANT: str = os.getenv("ENGINE_VARIANT", _default_variant)
    ENGINE_ARM32_VERSION: str = os.getenv("ENGINE_ARM32_VERSION", "arm32-v3.2.13")
    ENGINE_ARM64_VERSION: str = os.getenv("ENGINE_ARM64_VERSION", "arm64-v3.2.13")
    MIN_REPLICAS: int = 2
    MIN_FREE_REPLICAS: int = 1
    MAX_REPLICAS: int = 6
    # Backward compatibility alias retained for legacy tests and scripts.
    MAX_ACTIVE_REPLICAS: int = 6
    CONTAINER_LABEL: str = os.getenv("CONTAINER_LABEL", "ondemand.app=myservice")
    STARTUP_TIMEOUT_S: int = 25
    IDLE_TTL_S: int = 600

    COLLECT_INTERVAL_S: int = 1
    STATS_HISTORY_MAX: int = 720
    DASHBOARD_DEFAULT_WINDOW_S: int = 900
    DASHBOARD_PERSIST_INTERVAL_S: int = 5
    DASHBOARD_METRICS_RETENTION_HOURS: int = 168
    
    # Docker monitoring configuration
    MONITOR_INTERVAL_S: int = 10
    ENGINE_GRACE_PERIOD_S: int = 30
    AUTOSCALE_INTERVAL_S: int = 30
    
    # Health management configuration
    HEALTH_CHECK_INTERVAL_S: int = 20
    HEALTH_FAILURE_THRESHOLD: int = 3
    HEALTH_UNHEALTHY_GRACE_PERIOD_S: int = 60
    HEALTH_REPLACEMENT_COOLDOWN_S: int = 60
    
    # Circuit breaker configuration
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S: int = 300
    CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD: int = 3
    CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S: int = 180

    # Gluetun VPN integration
    DYNAMIC_VPN_MANAGEMENT: bool = True
    PREFERRED_ENGINES_PER_VPN: int = 10
    VPN_PROVIDER: str = "protonvpn"
    VPN_PROTOCOL: str = "wireguard"
    # Deprecated and ignored at runtime (dynamic VPN orchestration is always used).
    GLUETUN_CONTAINER_NAME: str | None = os.getenv("GLUETUN_CONTAINER_NAME")

    GLUETUN_API_PORT: int = 8001
    GLUETUN_HEALTH_CHECK_INTERVAL_S: int = 5
    GLUETUN_PORT_CACHE_TTL_S: int = 60
    VPN_RESTART_ENGINES_ON_RECONNECT: bool = True
    VPN_UNHEALTHY_RESTART_TIMEOUT_S: int = 60
    
    # VPN-specific port ranges for redundant mode
    # These map VPN container names to their port ranges in the format "min-max"
    # Example: GLUETUN_PORT_RANGE_1=19000-19499 for first VPN
    #          GLUETUN_PORT_RANGE_2=19500-19999 for second VPN
    GLUETUN_PORT_RANGE_1: str | None = os.getenv("GLUETUN_PORT_RANGE_1")
    GLUETUN_PORT_RANGE_2: str | None = os.getenv("GLUETUN_PORT_RANGE_2")
    
    # Engine provisioning performance settings
    MAX_CONCURRENT_PROVISIONS: int = 5
    MIN_PROVISION_INTERVAL_S: float = 0.5
    
    # Engine load balancing settings
    MAX_STREAMS_PER_ENGINE: int = 3

    PORT_RANGE_HOST: str = "19000-19999"
    ACE_HTTP_RANGE: str = "40000-44999"
    ACE_HTTPS_RANGE: str = "45000-49999"
    ACE_MAP_HTTPS: bool = os.getenv("ACE_MAP_HTTPS", "false").lower() == "true"
    PROXY_CONTROL_MODE: str = PROXY_MODE_API
    ACE_LIVE_EDGE_DELAY: int = 0
    PROXY_INITIAL_DATA_WAIT_TIMEOUT: int = 10
    PROXY_STREAM_TIMEOUT: int = 60
    PROXY_PREBUFFER_SECONDS: int = 3
    PACING_BITRATE_MULTIPLIER: float = 1.5
    STREAM_MODE: str = "TS"
    
    # HLS settings
    HLS_MAX_SEGMENTS: int = 20
    HLS_INITIAL_SEGMENTS: int = 3
    HLS_WINDOW_SIZE: int = 6
    HLS_BUFFER_READY_TIMEOUT: int = 30
    HLS_FIRST_SEGMENT_TIMEOUT: int = 30
    HLS_INITIAL_BUFFER_SECONDS: int = 10
    HLS_MAX_INITIAL_SEGMENTS: int = 10
    HLS_SEGMENT_FETCH_INTERVAL: float = 0.5
    
    # Engine resource limits
    ENGINE_MEMORY_LIMIT: str | None = os.getenv("ENGINE_MEMORY_LIMIT")
    
    M3U_TIMEOUT: float = float(os.getenv("M3U_TIMEOUT", "15"))

    API_KEY: str | None = os.getenv("API_KEY")
    DB_URL: str = os.getenv("DB_URL", _default_db_url)
    AUTO_DELETE: bool = True
    DEBUG_MODE: bool = False
    CLIENT_RECORD_TTL_S: int = 60

    @model_validator(mode='after')
    def validate_replicas(self):
        if self.MIN_REPLICAS < 0:
            raise ValueError('MIN_REPLICAS must be >= 0')
        if self.MIN_FREE_REPLICAS < 0:
            raise ValueError('MIN_FREE_REPLICAS must be >= 0')
        if self.MIN_FREE_REPLICAS > self.MAX_REPLICAS:
            raise ValueError('MIN_FREE_REPLICAS must be <= MAX_REPLICAS')
        if self.MAX_STREAMS_PER_ENGINE <= 0:
            raise ValueError('MAX_STREAMS_PER_ENGINE must be > 0')
        return self

    @validator('MAX_REPLICAS')
    def validate_max_replicas(cls, v, values):
        if v <= 0:
            raise ValueError('MAX_REPLICAS must be > 0')
        min_replicas = values.get('MIN_REPLICAS', 0)
        if v < min_replicas:
            raise ValueError('MAX_REPLICAS must be >= MIN_REPLICAS')
        return v

    @validator('ENGINE_VARIANT')
    def validate_engine_variant(cls, v):
        # We allow any variant name now because custom variants can be added through the UI
        # and persisted to JSON, which might not match the initial hardcoded list.
        if not v:
            raise ValueError('ENGINE_VARIANT cannot be empty')
        return v

    @validator('CONTAINER_LABEL')
    def validate_container_label(cls, v):
        if '=' not in v:
            raise ValueError('CONTAINER_LABEL must contain "=" (key=value format)')
        return v

    @validator('PORT_RANGE_HOST', 'ACE_HTTP_RANGE', 'ACE_HTTPS_RANGE')
    def validate_port_ranges(cls, v):
        try:
            start, end = v.split('-')
            start_port, end_port = int(start), int(end)
            if not (1 <= start_port <= 65535) or not (1 <= end_port <= 65535):
                raise ValueError(f'Ports must be between 1-65535')
            if start_port > end_port:
                raise ValueError(f'Start port must be <= end port')
            return v
        except (ValueError, AttributeError) as e:
            raise ValueError(f'Invalid port range format: {v}. Expected format: "start-end"')

    @validator('GLUETUN_API_PORT')
    def validate_gluetun_api_port(cls, v):
        if not (1 <= v <= 65535):
            raise ValueError('GLUETUN_API_PORT must be between 1-65535')
        return v

    @validator('STARTUP_TIMEOUT_S', 'IDLE_TTL_S', 'COLLECT_INTERVAL_S', 'MONITOR_INTERVAL_S', 'ENGINE_GRACE_PERIOD_S', 'AUTOSCALE_INTERVAL_S', 'GLUETUN_HEALTH_CHECK_INTERVAL_S', 'GLUETUN_PORT_CACHE_TTL_S', 'DASHBOARD_DEFAULT_WINDOW_S', 'DASHBOARD_PERSIST_INTERVAL_S', 'DASHBOARD_METRICS_RETENTION_HOURS')
    def validate_positive_timeouts(cls, v):
        if v <= 0:
            raise ValueError('Timeout values must be > 0')
        return v

    @validator('ACE_LIVE_EDGE_DELAY')
    def validate_live_edge_delay(cls, v):
        if v < 0:
            raise ValueError('ACE_LIVE_EDGE_DELAY must be >= 0')
        return v

    @validator('PROXY_CONTROL_MODE')
    def validate_proxy_control_mode(cls, v):
        normalized = normalize_proxy_mode(v, default=None)
        if normalized not in {PROXY_MODE_HTTP, PROXY_MODE_API}:
            raise ValueError("PROXY_CONTROL_MODE must be either 'http' or 'api'")
        return normalized

    @validator('STATS_HISTORY_MAX')
    def validate_stats_history_max(cls, v):
        if v <= 0:
            raise ValueError('STATS_HISTORY_MAX must be > 0')
        return v

    @validator('PREFERRED_ENGINES_PER_VPN')
    def validate_preferred_engines_per_vpn(cls, v):
        if v <= 0:
            raise ValueError('PREFERRED_ENGINES_PER_VPN must be > 0')
        return v

cfg = Cfg()
