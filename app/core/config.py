import os
from pydantic import BaseModel, validator, field_validator, model_validator
from dotenv import load_dotenv
from ..proxy.constants import PROXY_MODE_HTTP, PROXY_MODE_API, normalize_proxy_mode
load_dotenv()

import platform as _platform
_machine = _platform.machine().lower()

# Determine a sensible default variant based on the platform to avoid 404 errors on first run
_default_variant = "AceServe-amd64"
if "aarch64" in _machine or "arm64" in _machine:
    _default_variant = "AceServe-arm64"
elif "arm" in _machine:
    _default_variant = "AceServe-arm32"

class Cfg(BaseModel):
    APP_PORT: int = int(os.getenv("APP_PORT", 8000))
    DOCKER_NETWORK: str | None = os.getenv("DOCKER_NETWORK")
    ENGINE_VARIANT: str = os.getenv("ENGINE_VARIANT", _default_variant)
    ENGINE_ARM32_VERSION: str = os.getenv("ENGINE_ARM32_VERSION", "arm32-v3.2.13")
    ENGINE_ARM64_VERSION: str = os.getenv("ENGINE_ARM64_VERSION", "arm64-v3.2.13")
    MIN_REPLICAS: int = int(os.getenv("MIN_REPLICAS", 2))
    MIN_FREE_REPLICAS: int = int(os.getenv("MIN_FREE_REPLICAS", 1))
    MAX_REPLICAS: int = int(os.getenv("MAX_REPLICAS", 6))
    # Backward compatibility alias retained for legacy tests and scripts.
    MAX_ACTIVE_REPLICAS: int = int(os.getenv("MAX_ACTIVE_REPLICAS", os.getenv("MAX_REPLICAS", 6)))
    CONTAINER_LABEL: str = os.getenv("CONTAINER_LABEL", "ondemand.app=myservice")
    STARTUP_TIMEOUT_S: int = int(os.getenv("STARTUP_TIMEOUT_S", 25))
    IDLE_TTL_S: int = int(os.getenv("IDLE_TTL_S", 600))

    COLLECT_INTERVAL_S: int = int(os.getenv("COLLECT_INTERVAL_S", 1))
    STATS_HISTORY_MAX: int = int(os.getenv("STATS_HISTORY_MAX", 720))
    DASHBOARD_DEFAULT_WINDOW_S: int = int(os.getenv("DASHBOARD_DEFAULT_WINDOW_S", 900))
    DASHBOARD_PERSIST_INTERVAL_S: int = int(os.getenv("DASHBOARD_PERSIST_INTERVAL_S", 5))
    DASHBOARD_METRICS_RETENTION_HOURS: int = int(os.getenv("DASHBOARD_METRICS_RETENTION_HOURS", 168))
    
    # Docker monitoring configuration
    MONITOR_INTERVAL_S: int = int(os.getenv("MONITOR_INTERVAL_S", 10))
    ENGINE_GRACE_PERIOD_S: int = int(os.getenv("ENGINE_GRACE_PERIOD_S", 30))
    AUTOSCALE_INTERVAL_S: int = int(os.getenv("AUTOSCALE_INTERVAL_S", 30))
    
    # Health management configuration
    HEALTH_CHECK_INTERVAL_S: int = int(os.getenv("HEALTH_CHECK_INTERVAL_S", 20))
    HEALTH_FAILURE_THRESHOLD: int = int(os.getenv("HEALTH_FAILURE_THRESHOLD", 3))
    HEALTH_UNHEALTHY_GRACE_PERIOD_S: int = int(os.getenv("HEALTH_UNHEALTHY_GRACE_PERIOD_S", 60))
    HEALTH_REPLACEMENT_COOLDOWN_S: int = int(os.getenv("HEALTH_REPLACEMENT_COOLDOWN_S", 60))
    
    # Circuit breaker configuration
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5))
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S: int = int(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S", 300))
    CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD", 3))
    CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S: int = int(os.getenv("CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S", 180))

    # Gluetun VPN integration
    DYNAMIC_VPN_MANAGEMENT: bool = os.getenv("DYNAMIC_VPN_MANAGEMENT", "true").lower() == "true"
    PREFERRED_ENGINES_PER_VPN: int = int(os.getenv("PREFERRED_ENGINES_PER_VPN", 10))
    VPN_PROVIDER: str = os.getenv("VPN_PROVIDER", "protonvpn")
    VPN_PROTOCOL: str = os.getenv("VPN_PROTOCOL", "wireguard")
    # Deprecated and ignored at runtime (dynamic VPN orchestration is always used).
    GLUETUN_CONTAINER_NAME: str | None = os.getenv("GLUETUN_CONTAINER_NAME")

    GLUETUN_API_PORT: int = int(os.getenv("GLUETUN_API_PORT", 8001))
    GLUETUN_HEALTH_CHECK_INTERVAL_S: int = int(os.getenv("GLUETUN_HEALTH_CHECK_INTERVAL_S", 5))
    GLUETUN_PORT_CACHE_TTL_S: int = int(os.getenv("GLUETUN_PORT_CACHE_TTL_S", 60))
    VPN_RESTART_ENGINES_ON_RECONNECT: bool = os.getenv("VPN_RESTART_ENGINES_ON_RECONNECT", "true").lower() == "true"
    VPN_UNHEALTHY_RESTART_TIMEOUT_S: int = int(os.getenv("VPN_UNHEALTHY_RESTART_TIMEOUT_S", 60))
    
    # VPN-specific port ranges for redundant mode
    # These map VPN container names to their port ranges in the format "min-max"
    # Example: GLUETUN_PORT_RANGE_1=19000-19499 for first VPN
    #          GLUETUN_PORT_RANGE_2=19500-19999 for second VPN
    GLUETUN_PORT_RANGE_1: str | None = os.getenv("GLUETUN_PORT_RANGE_1")
    GLUETUN_PORT_RANGE_2: str | None = os.getenv("GLUETUN_PORT_RANGE_2")
    
    # Engine provisioning performance settings
    MAX_CONCURRENT_PROVISIONS: int = int(os.getenv("MAX_CONCURRENT_PROVISIONS", "5"))
    MIN_PROVISION_INTERVAL_S: float = float(os.getenv("MIN_PROVISION_INTERVAL_S", "0.5"))
    
    # Engine load balancing settings
    MAX_STREAMS_PER_ENGINE: int = int(os.getenv("MAX_STREAMS_PER_ENGINE", "3"))

    PORT_RANGE_HOST: str = os.getenv("PORT_RANGE_HOST", "19000-19999")
    ACE_HTTP_RANGE: str = os.getenv("ACE_HTTP_RANGE", "40000-44999")
    ACE_HTTPS_RANGE: str = os.getenv("ACE_HTTPS_RANGE", "45000-49999")
    ACE_MAP_HTTPS: bool = os.getenv("ACE_MAP_HTTPS", "false").lower() == "true"
    PROXY_CONTROL_MODE: str = normalize_proxy_mode(os.getenv("PROXY_CONTROL_MODE", PROXY_MODE_HTTP), default=PROXY_MODE_HTTP)
    ACE_LIVE_EDGE_DELAY: int = int(os.getenv("ACE_LIVE_EDGE_DELAY", "0"))
    
    # Engine resource limits
    ENGINE_MEMORY_LIMIT: str | None = os.getenv("ENGINE_MEMORY_LIMIT")
    
    M3U_TIMEOUT: float = float(os.getenv("M3U_TIMEOUT", "15"))

    API_KEY: str | None = os.getenv("API_KEY")
    DB_URL: str = os.getenv("DB_URL", "sqlite:///./orchestrator.db")
    AUTO_DELETE: bool = os.getenv("AUTO_DELETE", "true").lower() == "true"
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"
    # Stream loop detection configuration
    # Threshold for detecting stale streams (in seconds)
    # If live_last is behind current time by this amount, stream will be stopped
    STREAM_LOOP_DETECTION_THRESHOLD_S: int = int(os.getenv("STREAM_LOOP_DETECTION_THRESHOLD_S", "3600"))  # Default 1 hour
    STREAM_LOOP_DETECTION_ENABLED: bool = os.getenv("STREAM_LOOP_DETECTION_ENABLED", "false").lower() == "true"
    # Check interval for stream loop detection (in seconds)
    STREAM_LOOP_CHECK_INTERVAL_S: int = int(os.getenv("STREAM_LOOP_CHECK_INTERVAL_S", "10"))  # Default 10 seconds
    # Retention time for looping stream IDs in the tracker (in minutes)
    # 0 or None = indefinite retention
    STREAM_LOOP_RETENTION_MINUTES: int = int(os.getenv("STREAM_LOOP_RETENTION_MINUTES", "0"))  # Default indefinite

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
