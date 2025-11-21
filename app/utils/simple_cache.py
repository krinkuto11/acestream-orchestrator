"""
Simple in-memory cache with TTL support.
Used for caching expensive API calls to improve UI performance.

Note: This cache cannot distinguish between a cached None value and a cache miss,
as both return None from the get() method. If you need to cache None values,
consider using a sentinel value or a different cache implementation.
"""
import time
from typing import Optional, Any, Dict, Tuple
from threading import Lock
import logging

logger = logging.getLogger(__name__)


class SimpleCache:
    """Thread-safe in-memory cache with TTL (time-to-live) support."""
    
    def __init__(self, default_ttl: int = 60):
        """
        Initialize the cache.
        
        Args:
            default_ttl: Default time-to-live in seconds for cached items
        """
        self.default_ttl = default_ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = Lock()
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from the cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found or expired
        """
        with self._lock:
            if key not in self._cache:
                return None
            
            value, expires_at = self._cache[key]
            
            # Check if expired
            if time.time() > expires_at:
                del self._cache[key]
                logger.debug(f"Cache expired for key: {key}")
                return None
            
            logger.debug(f"Cache hit for key: {key}")
            return value
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set a value in the cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if not provided)
        """
        if ttl is None:
            ttl = self.default_ttl
        
        expires_at = time.time() + ttl
        
        with self._lock:
            self._cache[key] = (value, expires_at)
            logger.debug(f"Cache set for key: {key} (TTL: {ttl}s)")
    
    def delete(self, key: str) -> None:
        """
        Delete a value from the cache.
        
        Args:
            key: Cache key
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Cache deleted for key: {key}")
    
    def clear(self) -> None:
        """Clear all cached items."""
        with self._lock:
            self._cache.clear()
            logger.debug("Cache cleared")
    
    def cleanup_expired(self) -> int:
        """
        Remove all expired items from the cache.
        
        Returns:
            Number of items removed
        """
        with self._lock:
            current_time = time.time()
            expired_keys = [
                key for key, (_, expires_at) in self._cache.items()
                if current_time > expires_at
            ]
            
            for key in expired_keys:
                del self._cache[key]
            
            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            
            return len(expired_keys)
    
    def size(self) -> int:
        """Return the number of items in the cache."""
        with self._lock:
            return len(self._cache)
