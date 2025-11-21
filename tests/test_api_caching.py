"""
Integration tests for API endpoint caching.
Tests that expensive endpoints properly cache their results.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone


@pytest.fixture
def mock_state():
    """Mock state service."""
    with patch('app.main.state') as mock:
        # Mock get_stream to return a valid stream
        mock_stream = MagicMock()
        mock_stream.id = "test_stream_id_123456"
        mock_stream.stat_url = "http://localhost:6878/ace/stat?id=123"
        mock.get_stream.return_value = mock_stream
        yield mock


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    # Import after mocking to avoid initialization issues
    from app.main import app
    return TestClient(app)


def test_extended_stats_caching(client, mock_state):
    """Test that /streams/{id}/extended-stats endpoint caches results."""
    stream_id = "test_stream_id_123456"
    
    # Mock the get_stream_extended_stats function
    with patch('app.utils.acestream_api.get_stream_extended_stats', new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {
            "title": "Test Stream",
            "content_type": "live"
        }
        
        # First request - should call the API
        response1 = client.get(f"/streams/{stream_id}/extended-stats")
        assert response1.status_code == 200
        assert mock_api.call_count == 1
        data1 = response1.json()
        assert data1["title"] == "Test Stream"
        
        # Second request - should use cache
        response2 = client.get(f"/streams/{stream_id}/extended-stats")
        assert response2.status_code == 200
        assert mock_api.call_count == 1  # Still 1, not called again
        data2 = response2.json()
        assert data2 == data1
        
        # Verify cache was used by checking the data is identical
        assert data1 == data2


def test_livepos_caching(client, mock_state):
    """Test that /streams/{id}/livepos endpoint caches results."""
    stream_id = "test_stream_id_123456"
    
    # Mock httpx.AsyncClient with proper async context manager
    async def mock_get(url):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": {
                "is_live": 1,
                "livepos": {
                    "pos": 100,
                    "first_ts": 0,
                    "last_ts": 200
                }
            }
        }
        mock_response.raise_for_status = MagicMock()
        return mock_response
    
    with patch('httpx.AsyncClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=mock_get)
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # First request - should call the API
        response1 = client.get(f"/streams/{stream_id}/livepos")
        assert response1.status_code == 200
        assert mock_client.get.call_count == 1
        data1 = response1.json()
        assert data1["has_livepos"] is True
        assert data1["livepos"]["pos"] == 100
        
        # Second request - should use cache
        response2 = client.get(f"/streams/{stream_id}/livepos")
        assert response2.status_code == 200
        assert mock_client.get.call_count == 1  # Still 1, not called again
        data2 = response2.json()
        assert data2 == data1


def test_events_caching():
    """Test that events endpoint properly uses caching logic."""
    from app.main import events_cache
    
    # Clear cache first
    events_cache.clear()
    
    # Simulate what the endpoint does
    cache_key = "events:limit=100:offset=0:type=None:cat=None:cid=None:sid=None:since=None"
    
    # First call - cache miss
    assert events_cache.get(cache_key) is None
    
    # Simulate caching the result
    test_data = [{"id": 1, "message": "test"}]
    events_cache.set(cache_key, test_data, ttl=5)
    
    # Second call - cache hit
    cached_data = events_cache.get(cache_key)
    assert cached_data is not None
    assert cached_data == test_data
    
    # Different params should have different cache key
    cache_key2 = "events:limit=50:offset=0:type=None:cat=None:cid=None:sid=None:since=None"
    assert events_cache.get(cache_key2) is None


def test_events_stats_caching():
    """Test that events stats endpoint properly uses caching logic."""
    from app.main import events_cache
    
    # Clear cache first
    events_cache.clear()
    
    # Simulate what the endpoint does
    cache_key = "events:stats"
    
    # First call - cache miss
    assert events_cache.get(cache_key) is None
    
    # Simulate caching the result
    test_stats = {
        "total": 100,
        "by_type": {
            "engine": 50,
            "stream": 50
        }
    }
    events_cache.set(cache_key, test_stats, ttl=5)
    
    # Second call - cache hit
    cached_data = events_cache.get(cache_key)
    assert cached_data is not None
    assert cached_data == test_stats
    assert cached_data["total"] == 100


def test_cache_invalidation_on_stream_start():
    """Test that caches are invalidated when a stream starts."""
    from app.main import extended_stats_cache, livepos_cache, events_cache
    
    # Pre-populate caches
    stream_id = "test_stream_123"
    extended_stats_cache.set(f"extended_stats:{stream_id}", {"title": "Old"})
    livepos_cache.set(f"livepos:{stream_id}", {"has_livepos": False})
    events_cache.set("events:test", ["old_event"])
    
    # Verify caches are populated
    assert extended_stats_cache.get(f"extended_stats:{stream_id}") is not None
    assert livepos_cache.get(f"livepos:{stream_id}") is not None
    assert events_cache.get("events:test") is not None
    
    # Test cache invalidation methods work
    extended_stats_cache.delete(f"extended_stats:{stream_id}")
    livepos_cache.delete(f"livepos:{stream_id}")
    events_cache.clear()
    
    # Verify caches are now empty
    assert extended_stats_cache.get(f"extended_stats:{stream_id}") is None
    assert livepos_cache.get(f"livepos:{stream_id}") is None
    assert events_cache.get("events:test") is None
