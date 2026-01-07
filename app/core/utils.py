"""Core utility functions and classes"""

import logging
import os
from typing import Optional
import redis

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis client wrapper for the application"""
    
    _instance: Optional[redis.Redis] = None
    
    @classmethod
    def get_client(cls, host: Optional[str] = None, port: Optional[int] = None, 
                   db: Optional[int] = None, max_retries: int = 3, 
                   retry_interval: int = 1) -> Optional[redis.Redis]:
        """Get or create a Redis client instance.
        
        Args:
            host: Redis host (default: from env REDIS_HOST or 127.0.0.1)
            port: Redis port (default: from env REDIS_PORT or 6379)
            db: Redis database number (default: from env REDIS_DB or 0)
            max_retries: Maximum connection retry attempts
            retry_interval: Seconds between retries
            
        Returns:
            Redis client instance or None if connection fails
        """
        # Use environment variables if not provided
        if host is None:
            host = os.getenv("REDIS_HOST", "127.0.0.1")
        if port is None:
            port = int(os.getenv("REDIS_PORT", "6379"))
        if db is None:
            db = int(os.getenv("REDIS_DB", "0"))
        
        if cls._instance is not None:
            # Test if existing connection is still alive
            try:
                cls._instance.ping()
                return cls._instance
            except (redis.ConnectionError, redis.TimeoutError):
                logger.warning("Existing Redis connection is dead, reconnecting...")
                cls._instance = None
        
        # Try to connect to Redis
        for attempt in range(max_retries):
            try:
                client = redis.Redis(
                    host=host,
                    port=port,
                    db=db,
                    decode_responses=False,  # Keep as bytes for binary data
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True,
                )
                
                # Test connection
                client.ping()
                
                cls._instance = client
                logger.info(f"Successfully connected to Redis at {host}:{port}")
                return client
                
            except (redis.ConnectionError, redis.TimeoutError) as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Redis connection attempt {attempt + 1}/{max_retries} failed: {e}. "
                        f"Retrying in {retry_interval}s..."
                    )
                    import time
                    time.sleep(retry_interval)
                else:
                    logger.warning(
                        f"Failed to connect to Redis at {host}:{port} after {max_retries} attempts. "
                        f"Stream multiplexing will use in-memory fallback."
                    )
                    return None
        
        return None
