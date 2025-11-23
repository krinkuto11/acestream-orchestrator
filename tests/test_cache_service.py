"""
Tests for the cache service to ensure it works correctly.
"""

import time
import asyncio
import pytest
from app.services.cache import SimpleCache, cached, get_cache, invalidate_cache


def test_simple_cache_basic():
    """Test basic cache operations."""
    cache = SimpleCache()
    
    # Test set and get
    cache.set("key1", "value1", ttl=5.0)
    assert cache.get("key1") == "value1"
    
    # Test get non-existent key
    assert cache.get("non_existent") is None
    
    # Test delete
    cache.delete("key1")
    assert cache.get("key1") is None


def test_simple_cache_expiration():
    """Test that cache entries expire after TTL."""
    cache = SimpleCache()
    
    # Set with short TTL
    cache.set("key1", "value1", ttl=0.1)
    assert cache.get("key1") == "value1"
    
    # Wait for expiration
    time.sleep(0.2)
    assert cache.get("key1") is None


def test_simple_cache_cleanup():
    """Test cleanup of expired entries."""
    cache = SimpleCache()
    
    # Add some entries with different TTLs
    cache.set("key1", "value1", ttl=0.1)
    cache.set("key2", "value2", ttl=10.0)
    
    # Wait for first to expire
    time.sleep(0.2)
    
    # Cleanup
    cache.cleanup_expired()
    
    # key1 should be gone, key2 should remain
    assert cache.get("key1") is None
    assert cache.get("key2") == "value2"


def test_simple_cache_stats():
    """Test cache statistics."""
    cache = SimpleCache()
    
    # Add some entries
    cache.set("key1", "value1", ttl=5.0)
    cache.set("key2", "value2", ttl=5.0)
    
    # Generate some hits and misses
    cache.get("key1")  # hit
    cache.get("key1")  # hit
    cache.get("non_existent")  # miss
    
    stats = cache.get_stats()
    assert stats["size"] == 2
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(66.67, rel=0.1)


def test_cached_decorator_sync():
    """Test the @cached decorator with synchronous functions."""
    call_count = 0
    
    @cached(ttl=1.0, key_prefix="test")
    def expensive_function(x):
        nonlocal call_count
        call_count += 1
        return x * 2
    
    # First call should execute function
    result1 = expensive_function(5)
    assert result1 == 10
    assert call_count == 1
    
    # Second call should use cache
    result2 = expensive_function(5)
    assert result2 == 10
    assert call_count == 1  # Not incremented
    
    # Different argument should execute function
    result3 = expensive_function(10)
    assert result3 == 20
    assert call_count == 2


@pytest.mark.asyncio
async def test_cached_decorator_async():
    """Test the @cached decorator with asynchronous functions."""
    call_count = 0
    
    @cached(ttl=1.0, key_prefix="test_async")
    async def expensive_async_function(x):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return x * 3
    
    # First call should execute function
    result1 = await expensive_async_function(5)
    assert result1 == 15
    assert call_count == 1
    
    # Second call should use cache
    result2 = await expensive_async_function(5)
    assert result2 == 15
    assert call_count == 1  # Not incremented
    
    # Different argument should execute function
    result3 = await expensive_async_function(10)
    assert result3 == 30
    assert call_count == 2


def test_cache_invalidation():
    """Test cache invalidation."""
    # Use the global cache instance
    cache = get_cache()
    cache.clear()  # Clear any previous state
    
    # Add some entries
    cache.set("prefix:key1", "value1", ttl=5.0)
    cache.set("prefix:key2", "value2", ttl=5.0)
    cache.set("other:key3", "value3", ttl=5.0)
    
    # Use the invalidate_cache function for pattern-based invalidation
    invalidate_cache("prefix:")
    
    # prefix keys should be gone
    assert cache.get("prefix:key1") is None
    assert cache.get("prefix:key2") is None
    # other key should remain
    assert cache.get("other:key3") == "value3"
    
    # Clean up
    cache.clear()


def test_cache_thread_safety():
    """Test that cache operations are thread-safe."""
    import threading
    
    cache = SimpleCache()
    errors = []
    iterations = 50  # Reduced for faster tests
    
    def worker():
        try:
            for i in range(iterations):
                cache.set(f"key{i % 10}", f"value{i}", ttl=1.0)
                cache.get(f"key{i % 10}")
        except Exception as e:
            errors.append(e)
    
    # Create multiple threads
    threads = [threading.Thread(target=worker) for _ in range(3)]
    
    # Start all threads
    for t in threads:
        t.start()
    
    # Wait for completion
    for t in threads:
        t.join()
    
    # Should have no errors
    assert len(errors) == 0


@pytest.mark.asyncio
async def test_cache_cleanup_task():
    """Test the background cache cleanup task."""
    from app.services.cache import start_cleanup_task, stop_cleanup_task
    
    cache = get_cache()
    cache.clear()
    
    # Add an entry that will expire quickly
    cache.set("test_key", "test_value", ttl=0.1)
    
    # Start cleanup task with short interval
    await start_cleanup_task(interval=0.2)
    
    # Wait for entry to expire and cleanup to run
    await asyncio.sleep(0.4)
    
    # Entry should be cleaned up
    stats = cache.get_stats()
    assert stats["size"] == 0
    
    # Stop cleanup task
    await stop_cleanup_task()
    
    cache.clear()


def test_global_cache_instance():
    """Test that get_cache returns the same instance."""
    cache1 = get_cache()
    cache2 = get_cache()
    
    assert cache1 is cache2
    
    # Setting value in one should be visible in the other
    cache1.set("test", "value", ttl=5.0)
    assert cache2.get("test") == "value"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
