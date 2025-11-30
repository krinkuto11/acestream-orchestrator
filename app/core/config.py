import os
from pydantic import BaseModel, validator, field_validator, model_validator
from dotenv import load_dotenv
load_dotenv()

class Cfg(BaseModel):
    APP_PORT: int = int(os.getenv("APP_PORT", 8000))
    DOCKER_NETWORK: str | None = os.getenv("DOCKER_NETWORK")
    ENGINE_VARIANT: str = os.getenv("ENGINE_VARIANT", "krinkuto11-amd64")
    ENGINE_ARM32_VERSION: str = os.getenv("ENGINE_ARM32_VERSION", "arm32-v3.2.13")
    ENGINE_ARM64_VERSION: str = os.getenv("ENGINE_ARM64_VERSION", "arm64-v3.2.13")
    MIN_REPLICAS: int = int(os.getenv("MIN_REPLICAS", 1))
    MIN_FREE_REPLICAS: int = int(os.getenv("MIN_FREE_REPLICAS", 1))
    MAX_REPLICAS: int = int(os.getenv("MAX_REPLICAS", 20))
    CONTAINER_LABEL: str = os.getenv("CONTAINER_LABEL", "ondemand.app=myservice")
    STARTUP_TIMEOUT_S: int = int(os.getenv("STARTUP_TIMEOUT_S", 25))
    IDLE_TTL_S: int = int(os.getenv("IDLE_TTL_S", 600))

    COLLECT_INTERVAL_S: int = int(os.getenv("COLLECT_INTERVAL_S", 5))
    STATS_HISTORY_MAX: int = int(os.getenv("STATS_HISTORY_MAX", 720))
    
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
    GLUETUN_CONTAINER_NAME: str | None = os.getenv("GLUETUN_CONTAINER_NAME")
    GLUETUN_CONTAINER_NAME_2: str | None = os.getenv("GLUETUN_CONTAINER_NAME_2")
    VPN_MODE: str = os.getenv("VPN_MODE", "single")  # Options: single, redundant
    GLUETUN_API_PORT: int = int(os.getenv("GLUETUN_API_PORT", 8000))
    GLUETUN_HEALTH_CHECK_INTERVAL_S: int = int(os.getenv("GLUETUN_HEALTH_CHECK_INTERVAL_S", 5))
    GLUETUN_PORT_CACHE_TTL_S: int = int(os.getenv("GLUETUN_PORT_CACHE_TTL_S", 60))
    VPN_RESTART_ENGINES_ON_RECONNECT: bool = os.getenv("VPN_RESTART_ENGINES_ON_RECONNECT", "true").lower() == "true"
    VPN_UNHEALTHY_RESTART_TIMEOUT_S: int = int(os.getenv("VPN_UNHEALTHY_RESTART_TIMEOUT_S", 60))
    
    # Maximum active replicas when using Gluetun (port range allocation)
    MAX_ACTIVE_REPLICAS: int = int(os.getenv("MAX_ACTIVE_REPLICAS", 20))
    
    # VPN-specific port ranges for redundant mode
    # These map VPN container names to their port ranges in the format "min-max"
    # Example: GLUETUN_PORT_RANGE_1=19000-19499 for first VPN
    #          GLUETUN_PORT_RANGE_2=19500-19999 for second VPN
    GLUETUN_PORT_RANGE_1: str | None = os.getenv("GLUETUN_PORT_RANGE_1")
    GLUETUN_PORT_RANGE_2: str | None = os.getenv("GLUETUN_PORT_RANGE_2")
    
    # Engine provisioning performance settings
    MAX_CONCURRENT_PROVISIONS: int = int(os.getenv("MAX_CONCURRENT_PROVISIONS", "5"))
    MIN_PROVISION_INTERVAL_S: float = float(os.getenv("MIN_PROVISION_INTERVAL_S", "0.5"))

    PORT_RANGE_HOST: str = os.getenv("PORT_RANGE_HOST", "19000-19999")
    ACE_HTTP_RANGE: str = os.getenv("ACE_HTTP_RANGE", "40000-44999")
    ACE_HTTPS_RANGE: str = os.getenv("ACE_HTTPS_RANGE", "45000-49999")
    ACE_MAP_HTTPS: bool = os.getenv("ACE_MAP_HTTPS", "false").lower() == "true"
    
    # Engine resource limits
    ENGINE_MEMORY_LIMIT: str | None = os.getenv("ENGINE_MEMORY_LIMIT")

    API_KEY: str | None = os.getenv("API_KEY")
    DB_URL: str = os.getenv("DB_URL", "sqlite:///./orchestrator.db")
    AUTO_DELETE: bool = os.getenv("AUTO_DELETE", "false").lower() == "true"
    
    # Debug mode configuration
    DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"
    DEBUG_LOG_DIR: str = os.getenv("DEBUG_LOG_DIR", "./debug_logs")
    
    # Acexy proxy integration
    # When enabled, the orchestrator syncs with Acexy to detect and cleanup stale streams
    ACEXY_ENABLED: bool = os.getenv("ACEXY_ENABLED", "false").lower() == "true"
    ACEXY_URL: str | None = os.getenv("ACEXY_URL")  # e.g., "http://acexy:8080"
    ACEXY_SYNC_INTERVAL_S: int = int(os.getenv("ACEXY_SYNC_INTERVAL_S", 30))

    @model_validator(mode='after')
    def validate_replicas(self):
        if self.MIN_REPLICAS < 0:
            raise ValueError('MIN_REPLICAS must be >= 0')
        if self.MIN_FREE_REPLICAS < 0:
            raise ValueError('MIN_FREE_REPLICAS must be >= 0')
        if self.MIN_FREE_REPLICAS > self.MAX_REPLICAS:
            raise ValueError('MIN_FREE_REPLICAS must be <= MAX_REPLICAS')
        return self

    @validator('MAX_REPLICAS')
    def validate_max_replicas(cls, v, values):
        if v <= 0:
            raise ValueError('MAX_REPLICAS must be > 0')
        min_replicas = values.get('MIN_REPLICAS', 0)
        if v < min_replicas:
            raise ValueError('MAX_REPLICAS must be >= MIN_REPLICAS')
        return v

    @validator('MAX_ACTIVE_REPLICAS')
    def validate_max_active_replicas(cls, v):
        if v <= 0:
            raise ValueError('MAX_ACTIVE_REPLICAS must be > 0')
        return v

    @validator('ENGINE_VARIANT')
    def validate_engine_variant(cls, v):
        valid_variants = ['krinkuto11-amd64', 'jopsis-amd64', 'jopsis-arm32', 'jopsis-arm64']
        if v not in valid_variants:
            raise ValueError(f'ENGINE_VARIANT must be one of: {", ".join(valid_variants)}')
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

    @validator('STARTUP_TIMEOUT_S', 'IDLE_TTL_S', 'COLLECT_INTERVAL_S', 'MONITOR_INTERVAL_S', 'ENGINE_GRACE_PERIOD_S', 'AUTOSCALE_INTERVAL_S', 'GLUETUN_HEALTH_CHECK_INTERVAL_S', 'GLUETUN_PORT_CACHE_TTL_S')
    def validate_positive_timeouts(cls, v):
        if v <= 0:
            raise ValueError('Timeout values must be > 0')
        return v

    @validator('STATS_HISTORY_MAX')
    def validate_stats_history_max(cls, v):
        if v <= 0:
            raise ValueError('STATS_HISTORY_MAX must be > 0')
        return v

    @validator('VPN_MODE')
    def validate_vpn_mode(cls, v):
        valid_modes = ['single', 'redundant']
        if v not in valid_modes:
            raise ValueError(f'VPN_MODE must be one of: {", ".join(valid_modes)}')
        return v

    @model_validator(mode='after')
    def validate_vpn_config(self):
        # If redundant mode is set, ensure second container name is provided
        if self.VPN_MODE == 'redundant':
            if not self.GLUETUN_CONTAINER_NAME:
                raise ValueError('GLUETUN_CONTAINER_NAME is required when VPN_MODE is "redundant"')
            if not self.GLUETUN_CONTAINER_NAME_2:
                raise ValueError('GLUETUN_CONTAINER_NAME_2 is required when VPN_MODE is "redundant"')
            if self.GLUETUN_CONTAINER_NAME == self.GLUETUN_CONTAINER_NAME_2:
                raise ValueError('GLUETUN_CONTAINER_NAME and GLUETUN_CONTAINER_NAME_2 must be different')
        return self
    
    @model_validator(mode='after')
    def validate_acexy_config(self):
        # If Acexy is enabled, ensure URL is provided
        if self.ACEXY_ENABLED and not self.ACEXY_URL:
            raise ValueError('ACEXY_URL is required when ACEXY_ENABLED is true')
        return self
    
    @validator('ACEXY_SYNC_INTERVAL_S')
    def validate_acexy_sync_interval(cls, v):
        if v <= 0:
            raise ValueError('ACEXY_SYNC_INTERVAL_S must be > 0')
        return v
    
    @validator('ENGINE_MEMORY_LIMIT')
    def validate_engine_memory_limit(cls, v):
        if v is None or v == "":
            return None
        # Import validation function
        from ..services.custom_variant_config import validate_memory_limit
        is_valid, error_msg = validate_memory_limit(v)
        if not is_valid:
            raise ValueError(f'ENGINE_MEMORY_LIMIT: {error_msg}')
        return v

cfg = Cfg()
