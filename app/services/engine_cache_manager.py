
"""
Engine Cache Manager

Manages Docker volumes for AceStream engine caches.
Handles volume creation, mounting configuration, and cleanup of volumes.
"""

import os
import shutil
import logging
import asyncio
import time
from typing import Optional, Dict, List
from pathlib import Path

from ..core.config import cfg
from .docker_client import get_client

logger = logging.getLogger(__name__)

class EngineCacheManager:
    """
    Manages Docker volumes for engine caches.
    Uses named Docker volumes instead of host-mounted directories for better cleanup.
    """
    
    def __init__(self):
        self.mount_path = Path(cfg.ACESTREAM_CACHE_MOUNT)
        self.host_root = cfg.ACESTREAM_CACHE_ROOT
        
        # For backwards compatibility with host mounts
        # Ensure mount path exists if we are running locally/dev without docker mount
        # In production docker, this should be a mount point
        if not self.mount_path.exists():
            try:
                self.mount_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Failed to create cache mount directory {self.mount_path}: {e}")

    def is_enabled(self) -> bool:
        """Check if disk cache mounting is configured and enabled."""
        # Check if enabled in custom variant config
        from .custom_variant_config import get_config
        custom_config = get_config()
        if not custom_config or not custom_config.enabled:
            return False
            
        return getattr(custom_config, 'disk_cache_mount_enabled', False)
    
    def _use_docker_volumes(self) -> bool:
        """
        Determine whether to use Docker volumes or host mounts.
        Returns True if Docker volumes should be used, False for host mounts.
        """
        # If ACESTREAM_CACHE_ROOT is not set, use Docker volumes
        # This is the new default behavior for better cleanup
        return not self.host_root

    # Maximum length for container name suffix used in volume/directory names
    _CONTAINER_NAME_TRUNCATE_LENGTH = 12

    def _get_volume_name(self, container_id: str) -> str:
        """Generate a volume name for the container."""
        # Use first N chars for consistency with previous implementation
        short_id = container_id[:self._CONTAINER_NAME_TRUNCATE_LENGTH]
        return f"acestream-cache-{short_id}"

    def setup_cache(self, container_id: str) -> bool:
        """
        Create a cache volume or directory for the specific engine.
        
        Args:
            container_id: The container ID (or a unique name/UUID)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_enabled():
            return False
        
        if self._use_docker_volumes():
            # Use Docker volumes (new approach)
            try:
                cli = get_client()
                volume_name = self._get_volume_name(container_id)
                
                # Check if volume already exists
                try:
                    existing_volume = cli.volumes.get(volume_name)
                    logger.warning(f"Volume {volume_name} already exists, removing it first")
                    existing_volume.remove(force=True)
                except Exception:
                    # Volume doesn't exist, which is fine
                    pass
                
                # Create the volume
                cli.volumes.create(name=volume_name)
                logger.info(f"Created Docker volume {volume_name} for engine {container_id[:12]}")
                return True
            except Exception as e:
                logger.error(f"Failed to setup Docker volume for {container_id}: {e}")
                return False
        else:
            # Use host mounts (legacy approach for backwards compatibility)
            try:
                # Create a subdirectory using the truncated container name
                dir_name = container_id[:self._CONTAINER_NAME_TRUNCATE_LENGTH]
                engine_cache_dir = self.mount_path / dir_name
                
                if engine_cache_dir.exists():
                    logger.warning(f"Cache directory for {dir_name} already exists, cleaning first")
                    shutil.rmtree(engine_cache_dir)
                    
                engine_cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created cache directory for engine {dir_name}")
                return True
            except Exception as e:
                logger.error(f"Failed to setup cache for {container_id}: {e}")
                return False

    def cleanup_cache(self, container_id: str) -> bool:
        """
        Remove the cache volume or directory for the specific engine.
        
        Args:
            container_id: The container ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_enabled():
            return False
        
        if self._use_docker_volumes():
            # Use Docker volumes (new approach)
            try:
                cli = get_client()
                volume_name = self._get_volume_name(container_id)
                
                try:
                    volume = cli.volumes.get(volume_name)
                    volume.remove(force=True)
                    logger.info(f"Removed Docker volume {volume_name} for engine {container_id[:12]}")
                    return True
                except Exception as e:
                    # Volume might not exist if it was never created or already removed
                    logger.debug(f"Volume {volume_name} not found or already removed: {e}")
                    return True
            except Exception as e:
                logger.error(f"Failed to cleanup Docker volume for {container_id}: {e}")
                return False
        else:
            # Use host mounts (legacy approach)
            try:
                dir_name = container_id[:self._CONTAINER_NAME_TRUNCATE_LENGTH]
                engine_cache_dir = self.mount_path / dir_name
                
                if engine_cache_dir.exists():
                    shutil.rmtree(engine_cache_dir)
                    logger.info(f"Cleaned up cache directory for engine {dir_name}")
                    return True
                else:
                    logger.debug(f"Cache directory for {dir_name} not found, nothing to clean")
                    return True
            except Exception as e:
                logger.error(f"Failed to cleanup cache for {container_id}: {e}")
                return False

    def get_mount_config(self, container_id: str) -> Optional[Dict]:
        """
        Get the volume mount configuration for provisioner.
        
        Returns:
            Dict with volume config or None if disabled/failed
        """
        if not self.is_enabled():
            return None
        
        # Determine internal container path for cache
        # The user clarified that --cache-dir IS important as it's the parent of .acestream_cache
        
        # Check custom config for cache-dir
        from .custom_variant_config import get_config
        custom_config = get_config()
        
        parent_cache_dir = "/root/.ACEStream" # Default parent
        
        if custom_config:
            for param in custom_config.parameters:
                if param.name == "--cache-dir" and param.enabled and param.value:
                    # Simplify path (handle ~)
                    val = str(param.value)
                    if val.startswith("~"):
                         parent_cache_dir = val.replace("~", "/root", 1)
                    else:
                        parent_cache_dir = val
                    break
        
        # The actual cache folder is inside the parent dir
        container_cache_dir = f"{parent_cache_dir.rstrip('/')}/.acestream_cache"
        
        if self._use_docker_volumes():
            # Use Docker volumes (new approach)
            volume_name = self._get_volume_name(container_id)
            return {
                volume_name: {
                    'bind': container_cache_dir,
                    'mode': 'rw'
                }
            }
        else:
            # Use host mounts (legacy approach)
            dir_name = container_id[:12]
            
            # Verify internally that the directory exists (it should have been created by setup_cache)
            internal_path = self.mount_path / dir_name
            if not internal_path.exists():
                logger.warning(f"Internal cache path {internal_path} does not exist, attempting to create")
                try:
                    internal_path.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    logger.error(f"Failed to create cache path during config gen: {e}")
                    return None

            host_path = f"{self.host_root}/{dir_name}"
            
            return {
                host_path: {
                    'bind': container_cache_dir,
                    'mode': 'rw'
                }
            }

    async def prune_orphaned_caches(self):
        """
        Scan cache volumes/directories and remove ones that don't belong to active engines.
        This runs periodically.
        """
        if not self.is_enabled():
            return

        try:
            if self._use_docker_volumes():
                # Prune orphaned Docker volumes
                cli = get_client()
                
                # Get all volumes and filter in Python (Docker API doesn't support prefix matching)
                all_volumes = cli.volumes.list()
                acestream_volumes = [v for v in all_volumes if v.name.startswith('acestream-cache-')]
                
                # Get active container names (volumes are keyed by container name, not container ID)
                from .state import state
                active_engines = state.list_engines()
                # Build set of volume names that should exist for active engines
                active_volume_names = set()
                for engine in active_engines:
                    if engine.container_name:
                        active_volume_names.add(self._get_volume_name(engine.container_name))
                
                # Remove volumes for non-active containers
                for volume in acestream_volumes:
                    volume_name = volume.name
                    # Check if this volume belongs to an active engine
                    is_active = volume_name in active_volume_names
                    
                    if not is_active:
                        try:
                            volume.remove(force=True)
                            logger.info(f"Removed orphaned volume: {volume_name}")
                        except Exception as e:
                            logger.warning(f"Failed to remove orphaned volume {volume_name}: {e}")
            else:
                # Prune orphaned host directories (legacy approach)
                if not self.mount_path.exists():
                    return
                    
                entries = list(self.mount_path.iterdir())
                cache_dirs = {p.name for p in entries if p.is_dir()}
                
                if not cache_dirs:
                    return

                # Get active container names (directories are keyed by container name, not container ID)
                from .state import state
                active_engines = state.list_engines()
                # Use truncated container_name to match directory names
                active_dir_names = {
                    engine.container_name[:self._CONTAINER_NAME_TRUNCATE_LENGTH] 
                    for engine in active_engines 
                    if engine.container_name
                }
                
                # Remove directories for non-active containers
                for dir_name in cache_dirs:
                    if dir_name not in active_dir_names:
                        try:
                            dir_path = self.mount_path / dir_name
                            shutil.rmtree(dir_path)
                            logger.info(f"Removed orphaned cache directory: {dir_name}")
                        except Exception as e:
                            logger.warning(f"Failed to remove orphaned directory {dir_name}: {e}")

        except Exception as e:
            logger.error(f"Error checking for orphaned caches: {e}")

    async def prune_aged_files(self, max_age_minutes: int):
        """
        Prune all files in the cache directory regardless of age.
        
        Note: As of the latest update, this method deletes ALL cache files on every pass,
        regardless of their age. The max_age_minutes parameter is retained for backward
        compatibility but is no longer used in the logic.
        
        Args:
            max_age_minutes: (DEPRECATED - not used) Previously controlled age threshold
        """
        if not self.mount_path.exists():
            return

        logger.info(f"Starting cache pruning (deleting all cache files)")
        
        try:
            entries = list(self.mount_path.iterdir())
            count = 0
            size_freed = 0
            
            for buffer_dir in entries:
                if not buffer_dir.is_dir():
                    continue
                    
                # Delete all files in the cache directory regardless of age
                # Walk through the directory
                for root, _, files in os.walk(buffer_dir):
                    for file in files:
                        file_path = Path(root) / file
                        try:
                            stat = file_path.stat()
                            size = stat.st_size
                            file_path.unlink()
                            count += 1
                            size_freed += size
                        except Exception as e:
                            logger.debug(f"Failed to prune file {file_path}: {e}")
                            
            if count > 0:
                logger.info(f"Pruned {count} cache files, freed {size_freed / 1024 / 1024:.2f} MB")
                
        except Exception as e:
            logger.error(f"Error during cache file pruning: {e}")

    async def start_pruner(self):
        """
        Start the background pruner task.
        """
        logger.info("Starting EngineCacheManager background pruner")
        while True:
            # Default sleep interval (will be updated from config)
            sleep_seconds = 300 
            
            try:
                # 1. Prune orphaned caches (always safe)
                await self.prune_orphaned_caches()
                
                # 2. Prune aged files
                # Import here to avoid circular dependency
                try:
                    from .custom_variant_config import get_config
                    config = get_config()
                    
                    if config and config.disk_cache_prune_enabled:
                        # Run the pruner
                        max_age = config.disk_cache_file_max_age
                        await self.prune_aged_files(max_age_minutes=max_age)
                        
                        # Update sleep interval
                        interval_mins = config.disk_cache_prune_interval
                        if interval_mins > 0:
                            sleep_seconds = interval_mins * 60
                        else:
                            sleep_seconds = 60 # Minimum 1 minute safety
                except Exception as e:
                    logger.error(f"Error reading active config for pruning: {e}")

            except asyncio.CancelledError:
                logger.info("Cache pruner task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in cache pruner loop: {e}")
            
            await asyncio.sleep(sleep_seconds)

# Global instance
engine_cache_manager = EngineCacheManager()
