"""
End-to-end test for API endpoint caching with Docker stats.
Validates that the caching layer properly reduces Docker API calls.
"""

import time
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.cache import get_cache


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache = get_cache()
    cache.clear()
    yield
    cache.clear()


@patch('app.main.state.list_engines')
@patch('app.main.get_container_stats')
def test_individual_engine_stats_caching(mock_get_stats, mock_list_engines, client):
    """Test that individual engine stats endpoint uses caching."""
    container_id = "test_container_123"
    
    # Mock stats response
    mock_stats = {
        'container_id': container_id,
        'cpu_percent': 25.5,
        'memory_usage': 200000000,
        'memory_limit': 1000000000,
        'memory_percent': 20.0,
        'network_rx_bytes': 1000,
        'network_tx_bytes': 500,
        'block_read_bytes': 2000,
        'block_write_bytes': 1500
    }
    mock_get_stats.return_value = mock_stats
    
    # First call - cache miss
    response1 = client.get(f"/engines/{container_id}/stats")
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1['cpu_percent'] == 25.5
    
    # Verify Docker API was called once
    assert mock_get_stats.call_count == 1
    
    # Second call immediately after - should be from cache
    response2 = client.get(f"/engines/{container_id}/stats")
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2['cpu_percent'] == 25.5
    
    # Verify Docker API was NOT called again (still 1 call total)
    assert mock_get_stats.call_count == 1, "Cache should prevent additional Docker API calls"
    
    # Third call after cache expires (wait 3.5s)
    time.sleep(3.5)
    response3 = client.get(f"/engines/{container_id}/stats")
    assert response3.status_code == 200
    
    # Verify Docker API was called again after cache expiry
    assert mock_get_stats.call_count == 2, "Cache expiry should trigger new Docker API call"


@patch('app.main.state.list_engines')
@patch('app.main.get_multiple_container_stats')
def test_batch_engine_stats_caching(mock_batch_stats, mock_list_engines, client):
    """Test that batch engine stats endpoint uses caching."""
    # Mock engines
    mock_engine1 = Mock()
    mock_engine1.container_id = "container_1"
    mock_engine2 = Mock()
    mock_engine2.container_id = "container_2"
    mock_list_engines.return_value = [mock_engine1, mock_engine2]
    
    # Mock batch stats response
    mock_batch_stats.return_value = {
        'container_1': {
            'cpu_percent': 10.0,
            'memory_usage': 100000000,
            'memory_percent': 10.0,
            'network_rx_bytes': 1000,
            'network_tx_bytes': 500,
            'block_read_bytes': 2000,
            'block_write_bytes': 1500
        },
        'container_2': {
            'cpu_percent': 20.0,
            'memory_usage': 200000000,
            'memory_percent': 20.0,
            'network_rx_bytes': 2000,
            'network_tx_bytes': 1000,
            'block_read_bytes': 3000,
            'block_write_bytes': 2500
        }
    }
    
    # First call - cache miss
    response1 = client.get("/engines/stats/all")
    assert response1.status_code == 200
    data1 = response1.json()
    assert 'container_1' in data1
    assert 'container_2' in data1
    
    # Verify batch stats was called once
    assert mock_batch_stats.call_count == 1
    
    # Second call immediately after - should be from cache
    response2 = client.get("/engines/stats/all")
    assert response2.status_code == 200
    
    # Verify batch stats was NOT called again
    assert mock_batch_stats.call_count == 1, "Cache should prevent additional batch API calls"
    
    # Wait for cache to expire
    time.sleep(3.5)
    
    # Third call after expiry
    response3 = client.get("/engines/stats/all")
    assert response3.status_code == 200
    
    # Verify batch stats was called again
    assert mock_batch_stats.call_count == 2, "Cache expiry should trigger new batch API call"


@patch('app.main.state.list_engines')
@patch('app.main.get_total_stats')
def test_total_stats_caching(mock_total_stats, mock_list_engines, client):
    """Test that total stats endpoint uses caching."""
    # Mock engines
    mock_engine1 = Mock()
    mock_engine1.container_id = "container_1"
    mock_engine2 = Mock()
    mock_engine2.container_id = "container_2"
    mock_list_engines.return_value = [mock_engine1, mock_engine2]
    
    # Mock total stats response
    mock_total_stats.return_value = {
        'total_cpu_percent': 30.0,
        'total_memory_usage': 300000000,
        'total_network_rx_bytes': 3000,
        'total_network_tx_bytes': 1500,
        'total_block_read_bytes': 5000,
        'total_block_write_bytes': 4000,
        'container_count': 2
    }
    
    # First call - cache miss
    response1 = client.get("/engines/stats/total")
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1['total_cpu_percent'] == 30.0
    assert data1['container_count'] == 2
    
    # Verify total stats was called once
    assert mock_total_stats.call_count == 1
    
    # Multiple calls within cache TTL
    for _ in range(5):
        response = client.get("/engines/stats/total")
        assert response.status_code == 200
    
    # Verify total stats was still only called once (all from cache)
    assert mock_total_stats.call_count == 1, "Multiple calls within TTL should use cache"
    
    # Wait for cache to expire
    time.sleep(3.5)
    
    # Call after expiry
    response_after = client.get("/engines/stats/total")
    assert response_after.status_code == 200
    
    # Verify total stats was called again
    assert mock_total_stats.call_count == 2, "Cache expiry should trigger new call"


def test_cache_stats_endpoint(client):
    """Test that we can retrieve cache statistics."""
    cache = get_cache()
    
    # Add some entries to cache
    cache.set("test_key_1", {"data": "value1"}, ttl=10.0)
    cache.set("test_key_2", {"data": "value2"}, ttl=10.0)
    
    # Get some entries to generate hits
    cache.get("test_key_1")
    cache.get("test_key_1")
    cache.get("missing_key")  # miss
    
    stats = cache.get_stats()
    
    assert stats['size'] == 2
    assert stats['hits'] >= 2
    assert stats['misses'] >= 1
    assert stats['hit_rate'] > 0


@patch('app.main.state.list_engines')
@patch('app.main.get_multiple_container_stats')
def test_cache_reduces_api_load_simulation(mock_batch_stats, mock_list_engines, client):
    """Simulate realistic UI polling behavior to verify cache effectiveness."""
    # Mock 3 engines
    mock_engines = []
    for i in range(3):
        mock_engine = Mock()
        mock_engine.container_id = f"container_{i}"
        mock_engines.append(mock_engine)
    mock_list_engines.return_value = mock_engines
    
    # Mock batch stats with realistic delay
    call_count = [0]
    
    def mock_batch_with_delay(container_ids):
        call_count[0] += 1
        time.sleep(0.1)  # Simulate 100ms Docker API call
        return {
            cid: {
                'cpu_percent': 10.0 + (i * 5),
                'memory_usage': 100000000 * (i + 1),
                'memory_percent': 10.0 * (i + 1),
                'network_rx_bytes': 1000 * (i + 1),
                'network_tx_bytes': 500 * (i + 1),
                'block_read_bytes': 2000 * (i + 1),
                'block_write_bytes': 1500 * (i + 1)
            }
            for i, cid in enumerate(container_ids)
        }
    
    mock_batch_stats.side_effect = mock_batch_with_delay
    
    # Simulate UI polling every 3 seconds, 5 times (15 seconds total)
    start_time = time.time()
    for poll_num in range(5):
        response = client.get("/engines/stats/all")
        assert response.status_code == 200
        time.sleep(0.1)  # Small delay between polls (simulating UI behavior)
    
    elapsed = time.time() - start_time
    
    # Without caching: 5 polls * 100ms = 500ms minimum
    # With caching (3s TTL): Most polls hit cache, only 1-2 actual calls
    # Since we're sleeping 0.1s between polls and cache TTL is 3s,
    # all 5 polls should hit the same cache entry
    assert call_count[0] <= 2, f"Expected 1-2 Docker API calls due to caching, got {call_count[0]}"
    print(f"✓ Cache effectiveness: {call_count[0]} Docker API calls for 5 UI polls (saved ~{5 - call_count[0]} calls)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
