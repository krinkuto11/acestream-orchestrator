import os
from pydantic import BaseModel
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

cfg = Cfg()
