"""
Integration test to verify the docker stats optimization works with the main API endpoints.
"""

import pytest
from unittest.mock import patch, MagicMock


def test_stats_endpoints_use_optimized_functions():
    """Verify that the API endpoints use the optimized stats functions."""
    # Import the main module
    from app import main
    
    # Check that the optimized functions are imported
    assert hasattr(main, 'get_multiple_container_stats')
    assert hasattr(main, 'get_total_stats')
    
    # Verify the functions are from docker_stats module
    from app.services.docker_stats import get_multiple_container_stats, get_total_stats
    assert main.get_multiple_container_stats == get_multiple_container_stats
    assert main.get_total_stats == get_total_stats


@patch('app.main.get_multiple_container_stats')
@patch('app.services.state.state')
def test_get_all_engine_stats_endpoint(mock_state, mock_stats):
    """Test /engines/stats/all endpoint uses the optimized stats function."""
    from app.main import app
    from fastapi.testclient import TestClient
    
    # Mock state to return some engines
    mock_engine1 = MagicMock()
    mock_engine1.container_id = 'container1'
    mock_engine2 = MagicMock()
    mock_engine2.container_id = 'container2'
    
    mock_state.list_engines.return_value = [mock_engine1, mock_engine2]
    
    # Mock the stats function
    mock_stats.return_value = {
        'container1': {
            'container_id': 'container1',
            'cpu_percent': 1.0,
            'memory_usage': 1000000,
            'memory_limit': 10000000,
            'memory_percent': 10.0,
            'network_rx_bytes': 1000,
            'network_tx_bytes': 2000,
            'block_read_bytes': 500,
            'block_write_bytes': 800
        },
        'container2': {
            'container_id': 'container2',
            'cpu_percent': 2.0,
            'memory_usage': 2000000,
            'memory_limit': 10000000,
            'memory_percent': 20.0,
            'network_rx_bytes': 3000,
            'network_tx_bytes': 4000,
            'block_read_bytes': 1500,
            'block_write_bytes': 1800
        }
    }
    
    client = TestClient(app)
    response = client.get('/engines/stats/all')
    
    # Verify the function was called
    mock_stats.assert_called_once()
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert 'container1' in data
    assert 'container2' in data


@patch('app.main.get_total_stats')
@patch('app.services.state.state')
@patch('app.services.cache.get_cache')
def test_get_total_engine_stats_endpoint(mock_cache, mock_state, mock_stats):
    """Test /engines/stats/total endpoint uses the optimized stats function."""
    from app.main import app
    from fastapi.testclient import TestClient
    
    # Mock cache to always miss
    mock_cache_instance = MagicMock()
    mock_cache_instance.get.return_value = None
    mock_cache.return_value = mock_cache_instance
    
    # Mock state to return some engines
    mock_engine1 = MagicMock()
    mock_engine1.container_id = 'container1'
    mock_engine2 = MagicMock()
    mock_engine2.container_id = 'container2'
    
    mock_state.list_engines.return_value = [mock_engine1, mock_engine2]
    
    # Mock total stats
    mock_stats.return_value = {
        'total_cpu_percent': 4.0,
        'total_memory_usage': 3000000,
        'total_network_rx_bytes': 4000,
        'total_network_tx_bytes': 6000,
        'total_block_read_bytes': 2000,
        'total_block_write_bytes': 2600,
        'container_count': 2
    }
    
    client = TestClient(app)
    response = client.get('/engines/stats/total')
    
    # Verify the function was called
    mock_stats.assert_called_once()
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data['total_cpu_percent'] == 4.0
    assert data['total_memory_usage'] == 3000000
    assert data['container_count'] == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
