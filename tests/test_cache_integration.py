"""
Integration test for cache functionality with API endpoints.
Tests that caching improves response times without breaking functionality.

Note: These tests verify cache integration logic but don't require full app startup.
"""

import time
import pytest
from app.services.cache import get_cache, invalidate_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache = get_cache()
    cache.clear()
    yield
    cache.clear()


def test_cache_basic_operations():
    """Test basic cache operations work correctly."""
    cache = get_cache()
    
    # Set and get
    cache.set("test_key", {"data": "value"}, ttl=5.0)
    result = cache.get("test_key")
    assert result == {"data": "value"}
    
    # Stats
    stats = cache.get_stats()
    assert stats["hits"] > 0
    assert stats["size"] == 1


def test_cache_pattern_invalidation():
    """Test pattern-based cache invalidation."""
    cache = get_cache()
    
    # Add entries with different patterns
    cache.set("orchestrator:status", {"status": "ok"}, ttl=5.0)
    cache.set("stats:total", {"total": 10}, ttl=5.0)
    cache.set("vpn:status", {"vpn": "connected"}, ttl=5.0)
    
    # Invalidate orchestrator status
    invalidate_cache("orchestrator:status")
    
    assert cache.get("orchestrator:status") is None
    assert cache.get("stats:total") == {"total": 10}
    assert cache.get("vpn:status") == {"vpn": "connected"}


def test_cache_multiple_invalidation():
    """Test invalidating multiple cache patterns."""
    cache = get_cache()
    
    # Add entries
    cache.set("orchestrator:status", {"status": "ok"}, ttl=5.0)
    cache.set("stats:total", {"total": 10}, ttl=5.0)
    cache.set("vpn:status", {"vpn": "connected"}, ttl=5.0)
    
    # Invalidate both orchestrator and stats
    invalidate_cache("orchestrator:status")
    invalidate_cache("stats:total")
    
    assert cache.get("orchestrator:status") is None
    assert cache.get("stats:total") is None
    assert cache.get("vpn:status") == {"vpn": "connected"}


def test_cache_ttl_expiration():
    """Test that cache entries respect TTL."""
    cache = get_cache()
    
    # Set with short TTL
    cache.set("short_lived", "value", ttl=0.1)
    
    # Should be available immediately
    assert cache.get("short_lived") == "value"
    
    # Wait for expiration
    time.sleep(0.2)
    
    # Should be gone
    assert cache.get("short_lived") is None


def test_cache_stats_tracking():
    """Test that cache correctly tracks statistics."""
    cache = get_cache()
    cache.clear()
    
    # Generate some hits and misses
    cache.set("key1", "value1", ttl=5.0)
    
    cache.get("key1")  # hit
    cache.get("key1")  # hit
    cache.get("missing")  # miss
    cache.get("missing")  # miss
    
    stats = cache.get_stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 2
    assert stats["hit_rate"] == 50.0


def test_cache_size_tracking():
    """Test that cache correctly tracks its size."""
    cache = get_cache()
    cache.clear()
    
    # Start with empty cache
    assert cache.get_stats()["size"] == 0
    
    # Add entries
    cache.set("key1", "value1", ttl=5.0)
    assert cache.get_stats()["size"] == 1
    
    cache.set("key2", "value2", ttl=5.0)
    assert cache.get_stats()["size"] == 2
    
    # Delete one
    cache.delete("key1")
    assert cache.get_stats()["size"] == 1


def test_cache_clear_all():
    """Test clearing entire cache."""
    cache = get_cache()
    
    # Add several entries
    cache.set("key1", "value1", ttl=5.0)
    cache.set("key2", "value2", ttl=5.0)
    cache.set("key3", "value3", ttl=5.0)
    
    assert cache.get_stats()["size"] == 3
    
    # Clear all
    cache.clear()
    
    assert cache.get_stats()["size"] == 0
    assert cache.get("key1") is None
    assert cache.get("key2") is None
    assert cache.get("key3") is None


def test_cache_performance_benefit():
    """Test that cache provides performance benefit."""
    cache = get_cache()
    cache.clear()
    
    # Simulate expensive operation
    def expensive_operation():
        time.sleep(0.01)  # 10ms operation
        return {"result": "computed"}
    
    # First call - no cache
    start = time.time()
    result1 = expensive_operation()
    time1 = time.time() - start
    cache.set("expensive_key", result1, ttl=5.0)
    
    # Second call - from cache
    start = time.time()
    result2 = cache.get("expensive_key")
    time2 = time.time() - start
    
    # Cached call should be significantly faster
    assert result1 == result2
    assert time2 < time1  # Cache should be faster


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
