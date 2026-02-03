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

    def test_use_docker_volumes_when_no_host_root(self):
        """Test that Docker volumes are used when ACESTREAM_CACHE_ROOT is not set."""
        with patch('app.services.engine_cache_manager.cfg') as mock_cfg:
            mock_cfg.ACESTREAM_CACHE_ROOT = None
            mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
            
            manager = EngineCacheManager()
            assert manager._use_docker_volumes() is True

    def test_use_host_mounts_when_host_root_set(self):
        """Test that host mounts are used when ACESTREAM_CACHE_ROOT is set."""
        with patch('app.services.engine_cache_manager.cfg') as mock_cfg:
            mock_cfg.ACESTREAM_CACHE_ROOT = "/var/acestream/cache"
            mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
            
            manager = EngineCacheManager()
            assert manager._use_docker_volumes() is False

    def test_volume_name_generation(self):
        """Test that volume names are correctly generated from container IDs."""
        with patch('app.services.engine_cache_manager.cfg') as mock_cfg:
            mock_cfg.ACESTREAM_CACHE_ROOT = None
            mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
            
            manager = EngineCacheManager()
            container_id = "abc123def456789"
            expected_name = "acestream-cache-abc123def456"
            
            assert manager._get_volume_name(container_id) == expected_name

    @patch('app.services.engine_cache_manager.get_client')
    @patch('app.services.engine_cache_manager.cfg')
    def test_setup_cache_creates_docker_volume(self, mock_cfg, mock_get_client):
        """Test that setup_cache creates a Docker volume when enabled."""
        # Setup mocks
        mock_cfg.ACESTREAM_CACHE_ROOT = None
        mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock volume operations
        mock_client.volumes.get.side_effect = docker.errors.NotFound("Volume not found")
        mock_volume = MagicMock()
        mock_client.volumes.create.return_value = mock_volume
        
        # Create manager and enable it
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_get_config.return_value = mock_custom_config
            
            # Test setup
            container_id = "test123456789"
            result = manager.setup_cache(container_id)
            
            # Verify volume was created
            assert result is True
            mock_client.volumes.create.assert_called_once_with(name="acestream-cache-test12345678")

    @patch('app.services.engine_cache_manager.get_client')
    @patch('app.services.engine_cache_manager.cfg')
    def test_cleanup_cache_removes_docker_volume(self, mock_cfg, mock_get_client):
        """Test that cleanup_cache removes the Docker volume."""
        # Setup mocks
        mock_cfg.ACESTREAM_CACHE_ROOT = None
        mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
        
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
            container_id = "test123456789"
            result = manager.cleanup_cache(container_id)
            
            # Verify volume was removed
            assert result is True
            mock_client.volumes.get.assert_called_once_with("acestream-cache-test12345678")
            mock_volume.remove.assert_called_once_with(force=True)

    @patch('app.services.engine_cache_manager.get_client')
    @patch('app.services.engine_cache_manager.cfg')
    def test_get_mount_config_returns_volume_config(self, mock_cfg, mock_get_client):
        """Test that get_mount_config returns correct volume configuration."""
        # Setup mocks
        mock_cfg.ACESTREAM_CACHE_ROOT = None
        mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
        
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
            container_id = "test123456789"
            config = manager.get_mount_config(container_id)
            
            # Verify mount configuration
            assert config is not None
            expected_volume_name = "acestream-cache-test12345678"
            assert expected_volume_name in config
            assert config[expected_volume_name]['bind'] == "/root/.ACEStream/.acestream_cache"
            assert config[expected_volume_name]['mode'] == "rw"

    @patch('app.services.engine_cache_manager.get_client')
    @patch('app.services.engine_cache_manager.cfg')
    def test_cleanup_handles_missing_volume(self, mock_cfg, mock_get_client):
        """Test that cleanup gracefully handles missing volumes."""
        # Setup mocks
        mock_cfg.ACESTREAM_CACHE_ROOT = None
        mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock volume not found
        mock_client.volumes.get.side_effect = docker.errors.NotFound("Volume not found")
        
        # Create manager
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_get_config.return_value = mock_custom_config
            
            # Test cleanup - should not raise exception
            container_id = "test123456789"
            result = manager.cleanup_cache(container_id)
            
            # Should return True even if volume doesn't exist
            assert result is True


    @patch('app.services.engine_cache_manager.get_client')
    @patch('app.services.engine_cache_manager.cfg')
    async def test_prune_orphaned_caches_keeps_active_volumes(self, mock_cfg, mock_get_client):
        """Test that prune_orphaned_caches does NOT remove volumes for active engines."""
        # Setup mocks
        mock_cfg.ACESTREAM_CACHE_ROOT = None
        mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
        
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
            
            # Mock state with active engines - patch it where it's imported
            with patch('app.services.state.state') as mock_state:
                # Create mock engines with container names matching volumes 1 and 2
                mock_engine1 = MagicMock()
                mock_engine1.container_id = "c9eb4dcd5bec"  # Docker hash
                mock_engine1.container_name = "acestream-1"  # Name used for volume
                
                mock_engine2 = MagicMock()
                mock_engine2.container_id = "1ed8384a4f43"  # Docker hash
                mock_engine2.container_name = "acestream-2"  # Name used for volume
                
                mock_state.list_engines.return_value = [mock_engine1, mock_engine2]
                
                # Run pruning
                await manager.prune_orphaned_caches()
                
                # Verify that only the orphaned volume was attempted to be removed
                assert not mock_volume_active_1.remove.called, "Active volume 1 should NOT be removed"
                assert not mock_volume_active_2.remove.called, "Active volume 2 should NOT be removed"
                mock_volume_orphaned.remove.assert_called_once_with(force=True)

    @patch('app.services.engine_cache_manager.get_client')
    @patch('app.services.engine_cache_manager.cfg')
    async def test_prune_orphaned_caches_removes_all_orphaned_volumes(self, mock_cfg, mock_get_client):
        """Test that prune_orphaned_caches removes volumes with no active engines."""
        # Setup mocks
        mock_cfg.ACESTREAM_CACHE_ROOT = None
        mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock orphaned volumes
        mock_volume_1 = MagicMock()
        mock_volume_1.name = "acestream-cache-acestream-5"
        
        mock_volume_2 = MagicMock()
        mock_volume_2.name = "acestream-cache-acestream-7"
        
        mock_client.volumes.list.return_value = [mock_volume_1, mock_volume_2]
        
        # Create manager
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_get_config.return_value = mock_custom_config
            
            # Mock state with no active engines
            with patch('app.services.state.state') as mock_state:
                mock_state.list_engines.return_value = []
                
                # Run pruning
                await manager.prune_orphaned_caches()
                
                # Verify both volumes were removed
                mock_volume_1.remove.assert_called_once_with(force=True)
                mock_volume_2.remove.assert_called_once_with(force=True)

    @patch('app.services.engine_cache_manager.get_client')
    @patch('app.services.engine_cache_manager.cfg')
    async def test_prune_orphaned_caches_ignores_non_acestream_volumes(self, mock_cfg, mock_get_client):
        """Test that prune_orphaned_caches only processes acestream-cache- volumes."""
        # Setup mocks
        mock_cfg.ACESTREAM_CACHE_ROOT = None
        mock_cfg.ACESTREAM_CACHE_MOUNT = "/app/data/engine_cache"
        
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock volumes - mix of acestream and other volumes
        mock_volume_acestream = MagicMock()
        mock_volume_acestream.name = "acestream-cache-acestream-1"
        
        mock_volume_other = MagicMock()
        mock_volume_other.name = "some-other-volume"
        
        mock_client.volumes.list.return_value = [mock_volume_acestream, mock_volume_other]
        
        # Create manager
        manager = EngineCacheManager()
        
        # Mock the custom variant config
        with patch('app.services.custom_variant_config.get_config') as mock_get_config:
            mock_custom_config = MagicMock()
            mock_custom_config.enabled = True
            mock_custom_config.disk_cache_mount_enabled = True
            mock_get_config.return_value = mock_custom_config
            
            # Mock state with no active engines
            with patch('app.services.state.state') as mock_state:
                mock_state.list_engines.return_value = []
                
                # Run pruning
                await manager.prune_orphaned_caches()
                
                # Verify only acestream volume was processed
                mock_volume_acestream.remove.assert_called_once_with(force=True)
                assert not mock_volume_other.remove.called, "Non-acestream volume should be ignored"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
