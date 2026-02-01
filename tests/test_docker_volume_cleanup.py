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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
