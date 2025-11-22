"""
Simple in-memory cache service with TTL support for API endpoints.
Provides non-blocking, thread-safe caching to improve UI responsiveness.
"""

import asyncio
import logging
import time
from typing import Any, Optional, Callable, Dict
from functools import wraps
from threading import Lock

logger = logging.getLogger(__name__)


class CacheEntry:
    """Represents a cached value with expiration time."""
    
    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.time() + ttl
        self.created_at = time.time()
    
    def is_expired(self) -> bool:
        """Check if this cache entry has expired."""
        return time.time() > self.expires_at
    
    def age(self) -> float:
        """Get the age of this cache entry in seconds."""
        return time.time() - self.created_at


class SimpleCache:
    """
    Simple in-memory cache with TTL support.
    Thread-safe and non-blocking.
    """
    
    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from cache if it exists and hasn't expired.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value if found and not expired, None otherwise
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            
            if entry.is_expired():
                # Clean up expired entry
                del self._cache[key]
                self._misses += 1
                return None
            
            self._hits += 1
            logger.debug(f"Cache HIT for key: {key} (age: {entry.age():.2f}s)")
            return entry.value
    
    def set(self, key: str, value: Any, ttl: float):
        """
        Set a value in cache with a TTL.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds
        """
        with self._lock:
            self._cache[key] = CacheEntry(value, ttl)
            logger.debug(f"Cache SET for key: {key} (ttl: {ttl}s)")
    
    def delete(self, key: str):
        """
        Delete a specific key from cache.
        
        Args:
            key: Cache key to delete
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Cache DELETE for key: {key}")
    
    def clear(self):
        """Clear all cache entries and reset statistics."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            # Also reset statistics
            self._hits = 0
            self._misses = 0
            logger.info(f"Cache CLEAR: removed {count} entries and reset stats")
    
    def cleanup_expired(self):
        """Remove all expired entries from cache."""
        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if v.is_expired()]
            for key in expired_keys:
                del self._cache[key]
            if expired_keys:
                logger.debug(f"Cache CLEANUP: removed {len(expired_keys)} expired entries")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'size': len(self._cache),
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': round(hit_rate, 2),
                'entries': [
                    {
                        'key': key,
                        'age': round(entry.age(), 2),
                        'ttl_remaining': round(entry.expires_at - time.time(), 2)
                    }
                    for key, entry in self._cache.items()
                ]
            }


# Global cache instance
_cache = SimpleCache()


def get_cache() -> SimpleCache:
    """Get the global cache instance."""
    return _cache


def cached(ttl: float = 3.0, key_prefix: str = ""):
    """
    Decorator to cache function results with a TTL.
    Works with both sync and async functions.
    
    Args:
        ttl: Time-to-live in seconds (default: 3.0)
        key_prefix: Optional prefix for cache key
        
    Example:
        @cached(ttl=5.0, key_prefix="engines")
        async def get_engines():
            # expensive operation
            return data
    """
    def decorator(func: Callable):
        # Determine if function is async
        is_async = asyncio.iscoroutinefunction(func)
        
        if is_async:
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                # Build cache key from function name and arguments
                cache_key = f"{key_prefix}:{func.__name__}"
                if args or kwargs:
                    # Include args/kwargs in key if present
                    args_key = f"{args}:{kwargs}"
                    cache_key = f"{cache_key}:{hash(args_key)}"
                
                # Try to get from cache
                cached_value = _cache.get(cache_key)
                if cached_value is not None:
                    return cached_value
                
                # Cache miss - execute function
                logger.debug(f"Cache MISS for {cache_key}, executing function")
                result = await func(*args, **kwargs)
                
                # Store in cache
                _cache.set(cache_key, result, ttl)
                
                return result
            
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                # Build cache key from function name and arguments
                cache_key = f"{key_prefix}:{func.__name__}"
                if args or kwargs:
                    # Include args/kwargs in key if present
                    args_key = f"{args}:{kwargs}"
                    cache_key = f"{cache_key}:{hash(args_key)}"
                
                # Try to get from cache
                cached_value = _cache.get(cache_key)
                if cached_value is not None:
                    return cached_value
                
                # Cache miss - execute function
                logger.debug(f"Cache MISS for {cache_key}, executing function")
                result = func(*args, **kwargs)
                
                # Store in cache
                _cache.set(cache_key, result, ttl)
                
                return result
            
            return sync_wrapper
    
    return decorator


def invalidate_cache(key_pattern: Optional[str] = None):
    """
    Invalidate cache entries matching a pattern.
    If no pattern is provided, clears entire cache.
    
    Args:
        key_pattern: Optional pattern to match keys (simple substring match)
    """
    if key_pattern is None:
        _cache.clear()
    else:
        # Get all keys matching pattern and delete them
        with _cache._lock:
            matching_keys = [k for k in _cache._cache.keys() if key_pattern in k]
            # Delete directly without calling delete() to avoid re-acquiring lock
            for key in matching_keys:
                if key in _cache._cache:
                    del _cache._cache[key]
                    logger.debug(f"Cache DELETE for key: {key}")


# Background task for periodic cleanup
_cleanup_task = None


async def start_cleanup_task(interval: int = 60):
    """
    Start a background task that periodically cleans up expired cache entries.
    
    Args:
        interval: Cleanup interval in seconds (default: 60)
    """
    global _cleanup_task
    
    async def cleanup_loop():
        while True:
            try:
                await asyncio.sleep(interval)
                _cache.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cache cleanup task: {e}")
    
    _cleanup_task = asyncio.create_task(cleanup_loop())
    logger.info(f"Started cache cleanup task (interval: {interval}s)")


async def stop_cleanup_task():
    """Stop the cache cleanup background task."""
    global _cleanup_task
    
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        _cleanup_task = None
        logger.info("Stopped cache cleanup task")
