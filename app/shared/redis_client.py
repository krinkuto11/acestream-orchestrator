import redis
import logging
from ..core.config import cfg

logger = logging.getLogger(__name__)

class RedisClient:
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = redis.Redis(
                host=cfg.REDIS_HOST,
                port=cfg.REDIS_PORT,
                db=cfg.REDIS_DB,
                decode_responses=False
            )
            try:
                cls._instance.ping()
                logger.info(f"Connected to Redis at {cfg.REDIS_HOST}:{cfg.REDIS_PORT}")
            except Exception as e:
                logger.error(f"Failed to connect to Redis at {cfg.REDIS_HOST}:{cfg.REDIS_PORT}: {e}")
        return cls._instance

def get_redis_client():
    return RedisClient.get_instance()
