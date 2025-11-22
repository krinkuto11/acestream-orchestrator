"""
Integration test for cache invalidation when engines change.
Tests that cache is properly invalidated when engines are added/removed.
"""

import pytest
from app.services.cache import get_cache, invalidate_cache


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache = get_cache()
    cache.clear()
    yield
    cache.clear()


def test_cache_invalidation_patterns():
    """Test that cache invalidation patterns work correctly."""
    cache = get_cache()
    
    # Simulate caching engine stats
    cache.set("stats:engine:container_123", {"cpu_percent": 25.0}, ttl=10.0)
    cache.set("stats:engine:container_456", {"cpu_percent": 30.0}, ttl=10.0)
    cache.set("stats:all", {"container_123": {}, "container_456": {}}, ttl=10.0)
    cache.set("stats:total", {"total_cpu_percent": 55.0}, ttl=10.0)
    
    # Verify all entries exist
    assert cache.get("stats:engine:container_123") is not None
    assert cache.get("stats:engine:container_456") is not None
    assert cache.get("stats:all") is not None
    assert cache.get("stats:total") is not None
    
    # Invalidate all stats cache (e.g., when engines change)
    invalidate_cache("stats:")
    
    # Verify all stats entries are gone
    assert cache.get("stats:engine:container_123") is None
    assert cache.get("stats:engine:container_456") is None
    assert cache.get("stats:all") is None
    assert cache.get("stats:total") is None


def test_cache_individual_invalidation():
    """Test invalidating individual cache entries."""
    cache = get_cache()
    
    # Set multiple entries
    cache.set("stats:engine:container_123", {"cpu_percent": 25.0}, ttl=10.0)
    cache.set("stats:engine:container_456", {"cpu_percent": 30.0}, ttl=10.0)
    cache.set("stats:all", {"data": "batch"}, ttl=10.0)
    
    # Invalidate only one individual engine
    invalidate_cache("stats:engine:container_123")
    
    # Verify only that one is gone
    assert cache.get("stats:engine:container_123") is None
    assert cache.get("stats:engine:container_456") is not None
    assert cache.get("stats:all") is not None


def test_cache_clear_all():
    """Test clearing entire cache."""
    cache = get_cache()
    
    # Add various cache entries
    cache.set("stats:engine:container_123", {"cpu_percent": 25.0}, ttl=10.0)
    cache.set("orchestrator:status", {"status": "ok"}, ttl=10.0)
    cache.set("vpn:status", {"connected": True}, ttl=10.0)
    
    assert cache.get_stats()["size"] == 3
    
    # Clear everything
    invalidate_cache()
    
    # Verify cache is empty
    assert cache.get_stats()["size"] == 0
    assert cache.get("stats:engine:container_123") is None
    assert cache.get("orchestrator:status") is None
    assert cache.get("vpn:status") is None


def test_cache_pattern_matching():
    """Test that pattern matching works for partial key matches."""
    cache = get_cache()
    
    # Add entries with different prefixes
    cache.set("stats:engine:abc", {"data": 1}, ttl=10.0)
    cache.set("stats:engine:def", {"data": 2}, ttl=10.0)
    cache.set("stats:total", {"data": 3}, ttl=10.0)
    cache.set("orchestrator:status", {"data": 4}, ttl=10.0)
    
    # Invalidate only stats:engine entries
    invalidate_cache("stats:engine")
    
    # Check results
    assert cache.get("stats:engine:abc") is None
    assert cache.get("stats:engine:def") is None
    assert cache.get("stats:total") is not None  # stats:total should remain
    assert cache.get("orchestrator:status") is not None


def test_cache_ttl_vs_invalidation():
    """Test that invalidation works regardless of TTL."""
    cache = get_cache()
    
    # Set an entry with long TTL
    cache.set("long_lived_key", {"data": "value"}, ttl=100.0)
    
    # Verify it's there
    assert cache.get("long_lived_key") is not None
    
    # Invalidate it immediately
    invalidate_cache("long_lived_key")
    
    # Should be gone despite long TTL
    assert cache.get("long_lived_key") is None


def test_stats_cache_lifecycle():
    """
    Test realistic cache lifecycle for stats:
    1. Stats are fetched and cached
    2. UI polls multiple times (cache hits)
    3. Engine changes occur
    4. Cache is invalidated
    5. Next fetch gets fresh data
    """
    cache = get_cache()
    
    # Step 1: Initial stats fetch
    initial_stats = {"cpu_percent": 25.0, "memory_usage": 100000000}
    cache.set("stats:engine:container_123", initial_stats, ttl=3.0)
    
    # Step 2: Multiple UI polls hit cache
    for _ in range(5):
        cached = cache.get("stats:engine:container_123")
        assert cached == initial_stats
    
    stats = cache.get_stats()
    assert stats["hits"] >= 5
    
    # Step 3: Engine changes (e.g., container restart)
    # In real code, this would be triggered by provisioner/autoscaler
    invalidate_cache("stats:engine:container_123")
    
    # Step 4: Next fetch should be a miss
    cached = cache.get("stats:engine:container_123")
    assert cached is None
    
    # Step 5: New stats are fetched and cached
    new_stats = {"cpu_percent": 30.0, "memory_usage": 150000000}
    cache.set("stats:engine:container_123", new_stats, ttl=3.0)
    
    # Verify new stats are returned
    cached = cache.get("stats:engine:container_123")
    assert cached == new_stats
    assert cached["cpu_percent"] == 30.0  # Updated value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
