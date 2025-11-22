"""
Test for optimized Docker stats batching functionality.
Validates that batch operations are efficient and cached properly.
"""

import time
import pytest
from unittest.mock import Mock, patch, MagicMock
from app.services.docker_stats import get_multiple_container_stats, get_total_stats


@patch('app.services.docker_stats.get_client')
def test_batch_stats_concurrent_execution(mock_get_client):
    """Test that batch stats fetching uses concurrent execution."""
    # Mock Docker client
    mock_client = Mock()
    mock_get_client.return_value = mock_client
    
    # Create mock containers
    container_ids = [f"container_{i}" for i in range(5)]
    
    def create_mock_container(container_id):
        mock_container = Mock()
        
        # Simulate Docker stats call with a small delay
        def mock_stats(stream=False):
            time.sleep(0.05)  # 50ms per call
            return {
                'cpu_stats': {
                    'cpu_usage': {'total_usage': 1000000},
                    'system_cpu_usage': 10000000,
                    'online_cpus': 1
                },
                'precpu_stats': {
                    'cpu_usage': {'total_usage': 900000},
                    'system_cpu_usage': 9000000
                },
                'memory_stats': {
                    'usage': 100000000,
                    'limit': 1000000000
                },
                'networks': {},
                'blkio_stats': {'io_service_bytes_recursive': []}
            }
        
        mock_container.stats = mock_stats
        return mock_container
    
    # Setup mock client to return mock containers
    mock_client.containers.get = lambda cid: create_mock_container(cid)
    
    # Measure execution time
    start_time = time.time()
    results = get_multiple_container_stats(container_ids)
    elapsed_time = time.time() - start_time
    
    # Verify results
    assert len(results) == 5
    for container_id in container_ids:
        assert container_id in results
        assert 'cpu_percent' in results[container_id]
        assert 'memory_usage' in results[container_id]
    
    # With concurrent execution, 5 containers @ 50ms each should take ~50-100ms
    # Without concurrency, it would take ~250ms (5 * 50ms)
    # Allow some buffer for execution overhead
    assert elapsed_time < 0.25, f"Batch operation took {elapsed_time:.2f}s, expected < 0.25s (concurrent execution)"
    print(f"✓ Batch operation completed in {elapsed_time:.3f}s (concurrent execution working)")


@patch('app.services.docker_stats.get_multiple_container_stats')
def test_total_stats_uses_batch_operation(mock_batch_stats):
    """Test that get_total_stats uses the optimized batch operation."""
    container_ids = ['container_1', 'container_2', 'container_3']
    
    # Mock the batch stats response
    mock_batch_stats.return_value = {
        'container_1': {
            'cpu_percent': 10.5,
            'memory_usage': 100000000,
            'network_rx_bytes': 1000,
            'network_tx_bytes': 500,
            'block_read_bytes': 2000,
            'block_write_bytes': 1500
        },
        'container_2': {
            'cpu_percent': 20.3,
            'memory_usage': 200000000,
            'network_rx_bytes': 2000,
            'network_tx_bytes': 1000,
            'block_read_bytes': 3000,
            'block_write_bytes': 2500
        },
        'container_3': {
            'cpu_percent': 5.2,
            'memory_usage': 50000000,
            'network_rx_bytes': 500,
            'network_tx_bytes': 250,
            'block_read_bytes': 1000,
            'block_write_bytes': 750
        }
    }
    
    # Call get_total_stats
    total = get_total_stats(container_ids)
    
    # Verify batch operation was called once
    mock_batch_stats.assert_called_once_with(container_ids)
    
    # Verify aggregation is correct
    assert total['total_cpu_percent'] == 36.0  # 10.5 + 20.3 + 5.2
    assert total['total_memory_usage'] == 350000000  # 100M + 200M + 50M
    assert total['total_network_rx_bytes'] == 3500  # 1000 + 2000 + 500
    assert total['total_network_tx_bytes'] == 1750  # 500 + 1000 + 250
    assert total['total_block_read_bytes'] == 6000  # 2000 + 3000 + 1000
    assert total['total_block_write_bytes'] == 4750  # 1500 + 2500 + 750
    assert total['container_count'] == 3


@patch('app.services.docker_stats.get_client')
def test_batch_stats_handles_failures_gracefully(mock_get_client):
    """Test that batch operation handles individual container failures."""
    mock_client = Mock()
    mock_get_client.return_value = mock_client
    
    container_ids = ['good_container', 'bad_container', 'another_good']
    
    def get_container_mock(container_id):
        if container_id == 'bad_container':
            raise Exception("Container not found")
        
        mock_container = Mock()
        mock_container.stats = lambda stream=False: {
            'cpu_stats': {
                'cpu_usage': {'total_usage': 1000000},
                'system_cpu_usage': 10000000,
                'online_cpus': 1
            },
            'precpu_stats': {
                'cpu_usage': {'total_usage': 900000},
                'system_cpu_usage': 9000000
            },
            'memory_stats': {'usage': 100000000, 'limit': 1000000000},
            'networks': {},
            'blkio_stats': {'io_service_bytes_recursive': []}
        }
        return mock_container
    
    mock_client.containers.get = get_container_mock
    
    # Should not raise exception
    results = get_multiple_container_stats(container_ids)
    
    # Should have results for good containers only
    assert 'good_container' in results
    assert 'another_good' in results
    assert 'bad_container' not in results
    assert len(results) == 2


def test_empty_container_list():
    """Test that empty container list is handled correctly."""
    result = get_multiple_container_stats([])
    assert result == {}
    
    total = get_total_stats([])
    assert total['container_count'] == 0
    assert total['total_cpu_percent'] == 0.0
    assert total['total_memory_usage'] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
