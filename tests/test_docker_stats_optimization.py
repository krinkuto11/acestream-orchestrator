"""
Tests for optimized Docker stats batch collection.
"""

import pytest
from unittest.mock import patch, MagicMock
from app.services.docker_stats import (
    _parse_size_value,
    _parse_io_value,
    _parse_memory_usage,
    _parse_percent,
    get_all_container_stats_batch,
    get_multiple_container_stats,
    get_total_stats
)


# Sample docker stats output for testing (matches the format from problem statement)
SAMPLE_DOCKER_STATS_OUTPUT = """6d2f498cbeff\tacestream-7\t0.28%\t111.8MiB / 16.02GiB\t0.68%\t3.17GB / 6.29GB\t0B / 74.8MB
73329bff2d10\tacestream-6\t0.28%\t110.5MiB / 16.02GiB\t0.67%\t425MB / 388MB\t0B / 71.7MB
34f96e9c1768\tacestream-5\t0.62%\t110.4MiB / 16.02GiB\t0.67%\t3.17GB / 6.29GB\t0B / 71.7MB"""


class TestParsers:
    """Test parsing functions for docker stats output."""
    
    def test_parse_size_value_bytes(self):
        assert _parse_size_value("0B") == 0
        assert _parse_size_value("100B") == 100
    
    def test_parse_size_value_kb(self):
        assert _parse_size_value("1KB") == 1000
        assert _parse_size_value("1KiB") == 1024
        assert _parse_size_value("100KB") == 100000
    
    def test_parse_size_value_mb(self):
        assert _parse_size_value("1MB") == 1000000
        assert _parse_size_value("1MiB") == 1048576
        assert _parse_size_value("111.8MiB") == int(111.8 * 1024 * 1024)
        assert _parse_size_value("425MB") == 425000000
    
    def test_parse_size_value_gb(self):
        assert _parse_size_value("1GB") == 1000000000
        assert _parse_size_value("1GiB") == 1073741824
        assert _parse_size_value("3.17GB") == int(3.17 * 1000 * 1000 * 1000)
        assert _parse_size_value("16.02GiB") == int(16.02 * 1024 * 1024 * 1024)
    
    def test_parse_size_value_tb(self):
        assert _parse_size_value("1TB") == 1000000000000
        assert _parse_size_value("1TiB") == 1099511627776
    
    def test_parse_size_value_invalid(self):
        assert _parse_size_value("") == 0
        assert _parse_size_value("invalid") == 0
        assert _parse_size_value("   ") == 0
    
    def test_parse_io_value(self):
        rx, tx = _parse_io_value("3.17GB / 6.29GB")
        assert rx == int(3.17 * 1000 * 1000 * 1000)
        assert tx == int(6.29 * 1000 * 1000 * 1000)
        
        rx, tx = _parse_io_value("425MB / 388MB")
        assert rx == 425000000
        assert tx == 388000000
        
        rx, tx = _parse_io_value("0B / 74.8MB")
        assert rx == 0
        assert tx == int(74.8 * 1000 * 1000)
    
    def test_parse_io_value_invalid(self):
        rx, tx = _parse_io_value("invalid")
        assert rx == 0
        assert tx == 0
        
        rx, tx = _parse_io_value("")
        assert rx == 0
        assert tx == 0
    
    def test_parse_memory_usage(self):
        usage, limit = _parse_memory_usage("111.8MiB / 16.02GiB")
        assert usage == int(111.8 * 1024 * 1024)
        assert limit == int(16.02 * 1024 * 1024 * 1024)
        
        usage, limit = _parse_memory_usage("418.5MiB / 16.02GiB")
        assert usage == int(418.5 * 1024 * 1024)
        assert limit == int(16.02 * 1024 * 1024 * 1024)
    
    def test_parse_memory_usage_invalid(self):
        usage, limit = _parse_memory_usage("invalid")
        assert usage == 0
        assert limit == 0
    
    def test_parse_percent(self):
        assert _parse_percent("0.28%") == 0.28
        assert _parse_percent("2.83%") == 2.83
        assert _parse_percent("24.43%") == 24.43
        assert _parse_percent("100%") == 100.0
    
    def test_parse_percent_invalid(self):
        assert _parse_percent("invalid") == 0.0
        assert _parse_percent("") == 0.0


class TestBatchStatsCollection:
    """Test batch stats collection functionality."""
    
    def _create_mock_container(self, container_id, name, cpu_percent, memory_usage_mib, 
                               memory_limit_gib, memory_percent, network_rx_gb, 
                               network_tx_gb, block_write_mb):
        """Helper to create a mock container with stats."""
        mock_container = MagicMock()
        mock_container.id = container_id
        mock_container.name = name
        
        # Create stats data matching Docker API format
        memory_usage = int(memory_usage_mib * 1024 * 1024)
        memory_limit = int(memory_limit_gib * 1024 * 1024 * 1024)
        network_rx = int(network_rx_gb * 1000 * 1000 * 1000)
        network_tx = int(network_tx_gb * 1000 * 1000 * 1000)
        block_write = int(block_write_mb * 1000 * 1000)
        
        # Calculate CPU stats to achieve target percentage
        # cpu_percent = (cpu_delta / system_delta) * online_cpus * 100
        # For simplicity, set deltas that give us the target percentage
        online_cpus = 4
        target_ratio = cpu_percent / (online_cpus * 100.0)
        cpu_delta = int(1000000 * target_ratio)
        system_delta = 1000000
        
        stats_data = {
            'cpu_stats': {
                'cpu_usage': {'total_usage': 2000000 + cpu_delta, 'percpu_usage': [0] * online_cpus},
                'system_cpu_usage': 10000000 + system_delta,
                'online_cpus': online_cpus
            },
            'precpu_stats': {
                'cpu_usage': {'total_usage': 2000000},
                'system_cpu_usage': 10000000
            },
            'memory_stats': {
                'usage': memory_usage,
                'limit': memory_limit
            },
            'networks': {
                'eth0': {
                    'rx_bytes': network_rx,
                    'tx_bytes': network_tx
                }
            },
            'blkio_stats': {
                'io_service_bytes_recursive': [
                    {'op': 'Write', 'value': block_write}
                ]
            }
        }
        
        mock_container.stats.return_value = stats_data
        return mock_container
    
    @patch('app.services.docker_stats.get_client')
    def test_get_all_container_stats_batch_success(self, mock_get_client):
        """Test successful batch stats collection."""
        # Create mock containers matching the sample data
        mock_containers = [
            self._create_mock_container(
                '6d2f498cbeff', 'acestream-7', 0.28, 111.8, 16.02, 0.68, 3.17, 6.29, 74.8
            ),
            self._create_mock_container(
                '73329bff2d10', 'acestream-6', 0.28, 110.5, 16.02, 0.67, 0.425, 0.388, 71.7
            ),
            self._create_mock_container(
                '34f96e9c1768', 'acestream-5', 0.62, 110.4, 16.02, 0.67, 3.17, 6.29, 71.7
            )
        ]
        
        mock_client = MagicMock()
        mock_client.containers.list.return_value = mock_containers
        mock_get_client.return_value = mock_client
        
        result = get_all_container_stats_batch()
        
        # Verify the client was used
        mock_get_client.assert_called_once()
        mock_client.containers.list.assert_called_once()
        
        # Verify results
        assert len(result) == 3
        assert '6d2f498cbeff' in result
        assert '73329bff2d10' in result
        assert '34f96e9c1768' in result
        
        # Check first container stats
        stats = result['6d2f498cbeff']
        assert stats['container_id'] == '6d2f498cbeff'
        assert stats['cpu_percent'] == 0.28
        assert stats['memory_usage'] == int(111.8 * 1024 * 1024)
        assert stats['memory_limit'] == int(16.02 * 1024 * 1024 * 1024)
        assert stats['memory_percent'] == 0.68
        assert stats['network_rx_bytes'] == int(3.17 * 1000 * 1000 * 1000)
        assert stats['network_tx_bytes'] == int(6.29 * 1000 * 1000 * 1000)
        assert stats['block_read_bytes'] == 0
        assert stats['block_write_bytes'] == int(74.8 * 1000 * 1000)
    
    @patch('app.services.docker_stats.get_client')
    def test_get_all_container_stats_batch_empty_output(self, mock_get_client):
        """Test batch stats with no containers."""
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []
        mock_get_client.return_value = mock_client
        
        result = get_all_container_stats_batch()
        assert result == {}
    
    @patch('app.services.docker_stats.get_client')
    def test_get_all_container_stats_batch_command_failure(self, mock_get_client):
        """Test batch stats when docker API fails."""
        mock_get_client.side_effect = Exception("Docker daemon not running")
        
        result = get_all_container_stats_batch()
        assert result == {}
    
    @patch('app.services.docker_stats.get_client')
    def test_get_all_container_stats_batch_timeout(self, mock_get_client):
        """Test batch stats when API times out."""
        from docker.errors import APIError
        mock_get_client.side_effect = APIError("Timeout")
        
        result = get_all_container_stats_batch()
        assert result == {}
    
    @patch('app.services.docker_stats.get_client')
    def test_get_all_container_stats_batch_docker_not_found(self, mock_get_client):
        """Test batch stats when docker client fails to initialize."""
        from docker.errors import DockerException
        mock_get_client.side_effect = DockerException("Docker not found")
        
        result = get_all_container_stats_batch()
        assert result == {}


class TestMultipleContainerStats:
    """Test get_multiple_container_stats with batch optimization."""
    
    @patch('app.services.docker_stats.get_all_container_stats_batch')
    def test_get_multiple_container_stats_with_batch(self, mock_batch):
        """Test that get_multiple_container_stats uses batch collection."""
        # Mock batch stats
        mock_batch.return_value = {
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
        
        result = get_multiple_container_stats(['container1', 'container2'])
        
        # Verify batch was called
        mock_batch.assert_called_once()
        
        # Verify results
        assert len(result) == 2
        assert 'container1' in result
        assert 'container2' in result
        assert result['container1']['cpu_percent'] == 1.0
        assert result['container2']['cpu_percent'] == 2.0
    
    @patch('app.services.docker_stats.get_all_container_stats_batch')
    @patch('app.services.docker_stats.get_container_stats')
    def test_get_multiple_container_stats_fallback(self, mock_single, mock_batch):
        """Test fallback to individual queries when batch fails."""
        # Mock batch failure
        mock_batch.return_value = {}
        
        # Mock individual stats
        mock_single.side_effect = [
            {
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
            {
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
        ]
        
        result = get_multiple_container_stats(['container1', 'container2'])
        
        # Verify fallback was used
        mock_batch.assert_called_once()
        assert mock_single.call_count == 2
        
        # Verify results
        assert len(result) == 2
    
    @patch('app.services.docker_stats.get_all_container_stats_batch')
    def test_get_multiple_container_stats_empty_list(self, mock_batch):
        """Test with empty container list."""
        result = get_multiple_container_stats([])
        
        # Should not call batch for empty list
        mock_batch.assert_not_called()
        assert result == {}


class TestTotalStats:
    """Test get_total_stats with batch optimization."""
    
    @patch('app.services.docker_stats.get_multiple_container_stats')
    def test_get_total_stats_aggregation(self, mock_multi):
        """Test that get_total_stats correctly aggregates stats."""
        # Mock multiple container stats
        mock_multi.return_value = {
            'container1': {
                'cpu_percent': 1.5,
                'memory_usage': 1000000,
                'network_rx_bytes': 1000,
                'network_tx_bytes': 2000,
                'block_read_bytes': 500,
                'block_write_bytes': 800
            },
            'container2': {
                'cpu_percent': 2.5,
                'memory_usage': 2000000,
                'network_rx_bytes': 3000,
                'network_tx_bytes': 4000,
                'block_read_bytes': 1500,
                'block_write_bytes': 1800
            },
            'container3': {
                'cpu_percent': 3.0,
                'memory_usage': 3000000,
                'network_rx_bytes': 5000,
                'network_tx_bytes': 6000,
                'block_read_bytes': 2500,
                'block_write_bytes': 2800
            }
        }
        
        result = get_total_stats(['container1', 'container2', 'container3'])
        
        # Verify aggregation
        assert result['total_cpu_percent'] == 7.0
        assert result['total_memory_usage'] == 6000000
        assert result['total_network_rx_bytes'] == 9000
        assert result['total_network_tx_bytes'] == 12000
        assert result['total_block_read_bytes'] == 4500
        assert result['total_block_write_bytes'] == 5400
        assert result['container_count'] == 3
    
    @patch('app.services.docker_stats.get_multiple_container_stats')
    def test_get_total_stats_empty_list(self, mock_multi):
        """Test get_total_stats with empty container list."""
        result = get_total_stats([])
        
        # Should return zero totals
        assert result['total_cpu_percent'] == 0.0
        assert result['total_memory_usage'] == 0
        assert result['total_network_rx_bytes'] == 0
        assert result['total_network_tx_bytes'] == 0
        assert result['total_block_read_bytes'] == 0
        assert result['total_block_write_bytes'] == 0
        assert result['container_count'] == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
