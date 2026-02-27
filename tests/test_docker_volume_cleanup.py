
"""
Test Docker volume cleanup for AceStream engines.

This test verifies that Docker volumes are properly created and cleaned up
when engines are provisioned and deleted.
"""

import pytest
import docker
from unittest.mock import Mock, patch, MagicMock
from app.services.engine_cache_manager import EngineCacheManager


class TestDockerVolumeCleanup:
    """Test suite for Docker volume management in engine cache."""

    def test_volume_name_generation(self):
        """Test that volume names are correctly generated from container names."""
        manager = EngineCacheManager()
        container_name = "acestream-engine-abc123def"
        # Truncation length is 12
        expected_name = "acestream-cache-acestream-en"
        
        assert manager._get_volume_name(container_name) == expected_name

    @patch('app.services.engine_cache_manager.get_client')
    def test_setup_cache_creates_docker_volume(self, mock_get_client):
        """Test that setup_cache creates a Docker volume when enabled."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock volume operations
        mock_client.volumes.get.side_effect = docker.errors.NotFound("Volume not found")
        mock_volume = MagicMock()
        mock_client.volumes.create.return_value = mock_volume
        
        # Create manager
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_get_config.return_value = mock_custom_config
            
            # Test setup
            container_name = "test-engine"
            result = manager.setup_cache(container_name)
            
            # Verify volume was created
            assert result is True
            mock_client.volumes.create.assert_called_once_with(name="acestream-cache-test-engine")

    @patch('app.services.engine_cache_manager.get_client')
    def test_cleanup_cache_removes_docker_volume(self, mock_get_client):
        """Test that cleanup_cache removes the Docker volume."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock volume operations
        mock_volume = MagicMock()
        mock_client.volumes.get.return_value = mock_volume
        
        # Create manager
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_get_config.return_value = mock_custom_config
            
            # Test cleanup
            container_name = "test-engine"
            result = manager.cleanup_cache(container_name)
            
            # Verify volume was removed
            assert result is True
            mock_client.volumes.get.assert_called_once_with("acestream-cache-test-engine")
            mock_volume.remove.assert_called_once_with(force=True)

    @patch('app.services.engine_cache_manager.get_client')
    def test_get_mount_config_returns_volume_config(self, mock_get_client):
        """Test that get_mount_config returns correct volume configuration."""
        # Create manager
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_custom_config.parameters = []
            mock_get_config.return_value = mock_custom_config
            
            # Test mount config
            container_name = "test-engine"
            config = manager.get_mount_config(container_name)
            
            # Verify mount configuration
            assert config is not None
            expected_volume_name = "acestream-cache-test-engine"
            assert expected_volume_name in config
            assert config[expected_volume_name]['bind'] == "/root/.ACEStream/.acestream_cache"
            assert config[expected_volume_name]['mode'] == "rw"

    @patch('app.services.engine_cache_manager.get_client')
    async def test_prune_orphaned_caches_keeps_active_volumes(self, mock_get_client):
        """Test that prune_orphaned_caches does NOT remove volumes for active engines."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock volumes - some active, some orphaned
        mock_volume_active_1 = MagicMock()
        mock_volume_active_1.name = "acestream-cache-acestream-1"
        
        mock_volume_active_2 = MagicMock()
        mock_volume_active_2.name = "acestream-cache-acestream-2"
        
        mock_volume_orphaned = MagicMock()
        mock_volume_orphaned.name = "acestream-cache-acestream-9"
        
        mock_client.volumes.list.return_value = [
            mock_volume_active_1,
            mock_volume_active_2,
            mock_volume_orphaned
        ]
        
        # Create manager
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_get_config.return_value = mock_custom_config
            
            # Mock state with active engines
            with patch('app.services.state.state') as mock_state:
                mock_engine1 = MagicMock()
                mock_engine1.container_name = "acestream-1"
                
                mock_engine2 = MagicMock()
                mock_engine2.container_name = "acestream-2"
                
                mock_state.list_engines.return_value = [mock_engine1, mock_engine2]
                
                # Run pruning
                await manager.prune_orphaned_caches()
                
                # Verify that only the orphaned volume was attempted to be removed
                assert not mock_volume_active_1.remove.called
                assert not mock_volume_active_2.remove.called
                mock_volume_orphaned.remove.assert_called_once_with(force=True)

    @patch('app.services.engine_cache_manager.get_client')
    async def test_purge_all_contents_runs_helper_container(self, mock_get_client):
        """Test that purge_all_contents runs a helper container for each volume."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock volumes
        mock_vol = MagicMock()
        mock_vol.name = "acestream-cache-test"
        mock_client.volumes.list.return_value = [mock_vol]
        
        # Create manager
        manager = EngineCacheManager()
        
        # Test purging
        await manager.purge_all_contents()
        
        # Verify helper container was run
        mock_client.containers.run.assert_called_once()
        args, kwargs = mock_client.containers.run.call_args
        assert args[0] == "alpine"
        assert args[1] == "rm -rf /cache/*"
        assert mock_vol.name in kwargs['volumes']


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
