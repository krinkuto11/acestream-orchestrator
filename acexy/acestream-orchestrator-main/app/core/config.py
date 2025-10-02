import os
from pydantic import BaseModel, validator
from dotenv import load_dotenv
load_dotenv()

class Cfg(BaseModel):
    APP_PORT: int = int(os.getenv("APP_PORT", 8000))
    DOCKER_NETWORK: str | None = os.getenv("DOCKER_NETWORK")
    TARGET_IMAGE: str = os.getenv("TARGET_IMAGE", "acestream/engine:latest")
    MIN_REPLICAS: int = int(os.getenv("MIN_REPLICAS", 0))
    MAX_REPLICAS: int = int(os.getenv("MAX_REPLICAS", 20))
    CONTAINER_LABEL: str = os.getenv("CONTAINER_LABEL", "ondemand.app=myservice")
    STARTUP_TIMEOUT_S: int = int(os.getenv("STARTUP_TIMEOUT_S", 25))
    IDLE_TTL_S: int = int(os.getenv("IDLE_TTL_S", 600))

    COLLECT_INTERVAL_S: int = int(os.getenv("COLLECT_INTERVAL_S", 5))
    STATS_HISTORY_MAX: int = int(os.getenv("STATS_HISTORY_MAX", 720))

    PORT_RANGE_HOST: str = os.getenv("PORT_RANGE_HOST", "19000-19999")
    ACE_HTTP_RANGE: str = os.getenv("ACE_HTTP_RANGE", "40000-44999")
    ACE_HTTPS_RANGE: str = os.getenv("ACE_HTTPS_RANGE", "45000-49999")
    ACE_MAP_HTTPS: bool = os.getenv("ACE_MAP_HTTPS", "false").lower() == "true"

    API_KEY: str | None = os.getenv("API_KEY")
    DB_URL: str = os.getenv("DB_URL", "sqlite:///./orchestrator.db")
    AUTO_DELETE: bool = os.getenv("AUTO_DELETE", "false").lower() == "true"

    @validator('MIN_REPLICAS')
    def validate_min_replicas(cls, v):
        if v < 0:
            raise ValueError('MIN_REPLICAS must be >= 0')
        return v

    @validator('MAX_REPLICAS')
    def validate_max_replicas(cls, v, values):
        if v <= 0:
            raise ValueError('MAX_REPLICAS must be > 0')
        min_replicas = values.get('MIN_REPLICAS', 0)
        if v < min_replicas:
            raise ValueError('MAX_REPLICAS must be >= MIN_REPLICAS')
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

    @validator('STARTUP_TIMEOUT_S', 'IDLE_TTL_S', 'COLLECT_INTERVAL_S')
    def validate_positive_timeouts(cls, v):
        if v <= 0:
            raise ValueError('Timeout values must be > 0')
        return v

    @validator('STATS_HISTORY_MAX')
    def validate_stats_history_max(cls, v):
        if v <= 0:
            raise ValueError('STATS_HISTORY_MAX must be > 0')
        return v

cfg = Cfg()
