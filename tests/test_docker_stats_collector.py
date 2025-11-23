"""
Tests for background Docker stats collector service.
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock
from app.services.docker_stats_collector import DockerStatsCollector


class TestDockerStatsCollector:
    """Test DockerStatsCollector background service."""
    
    @pytest.mark.asyncio
    async def test_start_stop(self):
        """Test starting and stopping the collector."""
        collector = DockerStatsCollector()
        
        # Start the collector
        await collector.start()
        assert collector._task is not None
        assert not collector._task.done()
        
        # Stop the collector
        await collector.stop()
        assert collector._task.done()
    
    @pytest.mark.asyncio
    async def test_get_stats_before_collection(self):
        """Test getting stats before any collection has occurred."""
        collector = DockerStatsCollector()
        
        # Should return empty/zero values
        assert collector.get_engine_stats('test_container') is None
        
        total = collector.get_total_stats()
        assert total['total_cpu_percent'] == 0.0
        assert total['total_memory_usage'] == 0
        assert total['container_count'] == 0
        
        all_stats = collector.get_all_stats()
        assert all_stats == {}
        
        assert collector.get_last_update() is None
    
    @patch('app.services.docker_stats_collector.state')
    @patch('app.services.docker_stats_collector.get_multiple_container_stats')
    def test_collect_stats_no_engines(self, mock_get_stats, mock_state):
        """Test stats collection when there are no engines."""
        collector = DockerStatsCollector()
        
        # Mock no engines
        mock_state.list_engines.return_value = []
        
        # Collect stats
        collector._collect_stats()
        
        # Should not call get_multiple_container_stats
        mock_get_stats.assert_not_called()
        
        # Should have zero totals
        total = collector.get_total_stats()
        assert total['total_cpu_percent'] == 0.0
        assert total['total_memory_usage'] == 0
        assert total['container_count'] == 0
    
    @patch('app.services.docker_stats_collector.state')
    @patch('app.services.docker_stats_collector.get_multiple_container_stats')
    def test_collect_stats_with_engines(self, mock_get_stats, mock_state):
        """Test stats collection with multiple engines."""
        collector = DockerStatsCollector()
        
        # Mock engines
        mock_engine1 = MagicMock()
        mock_engine1.container_id = 'container1'
        mock_engine2 = MagicMock()
        mock_engine2.container_id = 'container2'
        
        mock_state.list_engines.return_value = [mock_engine1, mock_engine2]
        
        # Mock stats
        mock_get_stats.return_value = {
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
            }
        }
        
        # Collect stats
        collector._collect_stats()
        
        # Verify get_multiple_container_stats was called with correct IDs
        mock_get_stats.assert_called_once_with(['container1', 'container2'])
        
        # Verify individual stats are cached
        stats1 = collector.get_engine_stats('container1')
        assert stats1 is not None
        assert stats1['cpu_percent'] == 1.5
        assert stats1['memory_usage'] == 1000000
        
        stats2 = collector.get_engine_stats('container2')
        assert stats2 is not None
        assert stats2['cpu_percent'] == 2.5
        assert stats2['memory_usage'] == 2000000
        
        # Verify total stats are correctly aggregated
        total = collector.get_total_stats()
        assert total['total_cpu_percent'] == 4.0
        assert total['total_memory_usage'] == 3000000
        assert total['total_network_rx_bytes'] == 4000
        assert total['total_network_tx_bytes'] == 6000
        assert total['total_block_read_bytes'] == 2000
        assert total['total_block_write_bytes'] == 2600
        assert total['container_count'] == 2
        
        # Verify all stats
        all_stats = collector.get_all_stats()
        assert len(all_stats) == 2
        assert 'container1' in all_stats
        assert 'container2' in all_stats
        
        # Verify last update timestamp
        assert collector.get_last_update() is not None
    
    @patch('app.services.docker_stats_collector.state')
    @patch('app.services.docker_stats_collector.get_multiple_container_stats')
    def test_collect_stats_handles_errors(self, mock_get_stats, mock_state):
        """Test that collector handles errors gracefully."""
        collector = DockerStatsCollector()
        
        # Mock engines
        mock_engine = MagicMock()
        mock_engine.container_id = 'container1'
        mock_state.list_engines.return_value = [mock_engine]
        
        # Mock stats collection failure
        mock_get_stats.side_effect = Exception("Docker API error")
        
        # Should not raise exception
        collector._collect_stats()
        
        # Stats should remain empty/unchanged
        assert collector.get_engine_stats('container1') is None
    
    @pytest.mark.asyncio
    async def test_collection_interval(self):
        """Test that collection happens at the configured interval."""
        collector = DockerStatsCollector()
        collector._collection_interval = 0.1  # Fast for testing
        
        with patch.object(collector, '_collect_stats') as mock_collect:
            # Start collector
            await collector.start()
            
            # Wait for a couple of collections
            await asyncio.sleep(0.25)
            
            # Stop collector
            await collector.stop()
            
            # Should have been called at least twice
            assert mock_collect.call_count >= 2
    
    @pytest.mark.asyncio
    async def test_multiple_start_calls(self):
        """Test that multiple start() calls don't create multiple tasks."""
        collector = DockerStatsCollector()
        
        # Start multiple times
        await collector.start()
        task1 = collector._task
        
        await collector.start()
        task2 = collector._task
        
        # Should be the same task
        assert task1 is task2
        
        # Clean up
        await collector.stop()
    
    @patch('app.services.docker_stats_collector.state')
    @patch('app.services.docker_stats_collector.get_multiple_container_stats')
    def test_get_engine_stats_nonexistent(self, mock_get_stats, mock_state):
        """Test getting stats for a container that doesn't exist."""
        collector = DockerStatsCollector()
        
        # Mock an engine
        mock_engine = MagicMock()
        mock_engine.container_id = 'container1'
        mock_state.list_engines.return_value = [mock_engine]
        
        mock_get_stats.return_value = {
            'container1': {'cpu_percent': 1.0, 'memory_usage': 1000}
        }
        
        collector._collect_stats()
        
        # Get stats for existing container
        assert collector.get_engine_stats('container1') is not None
        
        # Get stats for non-existent container
        assert collector.get_engine_stats('nonexistent') is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
