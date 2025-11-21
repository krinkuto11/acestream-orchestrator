"""
Tests for the SimpleCache utility.
"""
import time
import pytest
from app.utils.simple_cache import SimpleCache


def test_cache_set_and_get():
    """Test basic cache set and get operations."""
    cache = SimpleCache(default_ttl=60)
    
    # Set a value
    cache.set("key1", "value1")
    
    # Get the value
    assert cache.get("key1") == "value1"


def test_cache_get_nonexistent():
    """Test getting a non-existent key returns None."""
    cache = SimpleCache(default_ttl=60)
    
    assert cache.get("nonexistent") is None


def test_cache_ttl_expiration():
    """Test that cached items expire after TTL."""
    cache = SimpleCache(default_ttl=1)  # 1 second TTL
    
    # Set a value
    cache.set("key1", "value1")
    
    # Should be available immediately
    assert cache.get("key1") == "value1"
    
    # Wait for expiration
    time.sleep(1.1)
    
    # Should be None after expiration
    assert cache.get("key1") is None


def test_cache_custom_ttl():
    """Test using custom TTL when setting values."""
    cache = SimpleCache(default_ttl=60)
    
    # Set with custom short TTL
    cache.set("key1", "value1", ttl=1)
    
    # Should be available immediately
    assert cache.get("key1") == "value1"
    
    # Wait for expiration
    time.sleep(1.1)
    
    # Should be None after expiration
    assert cache.get("key1") is None


def test_cache_delete():
    """Test deleting a cached value."""
    cache = SimpleCache(default_ttl=60)
    
    cache.set("key1", "value1")
    assert cache.get("key1") == "value1"
    
    cache.delete("key1")
    assert cache.get("key1") is None


def test_cache_clear():
    """Test clearing all cached values."""
    cache = SimpleCache(default_ttl=60)
    
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    
    assert cache.size() == 2
    
    cache.clear()
    
    assert cache.size() == 0
    assert cache.get("key1") is None
    assert cache.get("key2") is None


def test_cache_cleanup_expired():
    """Test cleaning up expired entries."""
    cache = SimpleCache(default_ttl=1)
    
    # Set some values with different TTLs
    cache.set("key1", "value1", ttl=1)  # Will expire
    cache.set("key2", "value2", ttl=60)  # Won't expire
    
    assert cache.size() == 2
    
    # Wait for first to expire
    time.sleep(1.1)
    
    # Cleanup expired entries
    removed = cache.cleanup_expired()
    
    assert removed == 1
    assert cache.size() == 1
    assert cache.get("key1") is None
    assert cache.get("key2") == "value2"


def test_cache_stores_different_types():
    """Test that cache can store different types of values."""
    cache = SimpleCache(default_ttl=60)
    
    cache.set("string", "value")
    cache.set("number", 42)
    cache.set("list", [1, 2, 3])
    cache.set("dict", {"key": "value"})
    cache.set("none", None)
    
    assert cache.get("string") == "value"
    assert cache.get("number") == 42
    assert cache.get("list") == [1, 2, 3]
    assert cache.get("dict") == {"key": "value"}
    # Note: None values are stored but might be confused with "not found"
    # This is a design choice - we could use a sentinel value if needed
    assert cache.get("none") is None


def test_cache_thread_safety():
    """Test that cache operations are thread-safe."""
    import threading
    
    cache = SimpleCache(default_ttl=60)
    errors = []
    
    def worker(worker_id):
        try:
            for i in range(100):
                key = f"key{worker_id}_{i}"
                cache.set(key, f"value{worker_id}_{i}")
                value = cache.get(key)
                if value != f"value{worker_id}_{i}":
                    errors.append(f"Worker {worker_id} got wrong value for {key}")
        except Exception as e:
            errors.append(f"Worker {worker_id} error: {e}")
    
    # Create multiple threads
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    
    # Start all threads
    for t in threads:
        t.start()
    
    # Wait for all threads to complete
    for t in threads:
        t.join()
    
    # Check for errors
    assert len(errors) == 0, f"Thread safety errors: {errors}"
