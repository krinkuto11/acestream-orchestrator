
"""
Engine Cache Manager

Manages host-mounted disk caches for AceStream engines.
Handles directory creation, mounting configuration, and cleanup of cache directories.
"""

import os
import shutil
import logging
import asyncio
from typing import Optional, Dict, List
from pathlib import Path

from ..core.config import cfg
from .docker_client import get_client

logger = logging.getLogger(__name__)

class EngineCacheManager:
    """
    Manages disk cache directories for engines.
    """
    
    def __init__(self):
        self.mount_path = Path(cfg.ACESTREAM_CACHE_MOUNT)
        self.host_root = cfg.ACESTREAM_CACHE_ROOT
        
        # Ensure mount path exists if we are running locally/dev without docker mount
        # In production docker, this should be a mount point
        if not self.mount_path.exists():
            try:
                self.mount_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Failed to create cache mount directory {self.mount_path}: {e}")

    def is_enabled(self) -> bool:
        """Check if disk cache mounting is configured and enabled."""
        if not self.host_root:
            return False
            
        # Check if enabled in custom variant config
        from .custom_variant_config import get_config
        custom_config = get_config()
        if not custom_config or not custom_config.enabled:
            return False
            
        return getattr(custom_config, 'disk_cache_mount_enabled', False)

    def setup_cache(self, container_id: str) -> bool:
        """
        Create a cache directory for the specific engine.
        
        Args:
            container_id: The container ID (or a unique name/UUID)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_enabled():
            return False
            
        try:
            # Create a subdirectory using the first 12 chars of ID (standard docker short ID)
            # or full name if it's cleaner
            dir_name = container_id[:12]
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
        Remove the cache directory for the specific engine.
        
        Args:
            container_id: The container ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_enabled():
            return False
            
        try:
            dir_name = container_id[:12]
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
            
        # Host path is configured in .env (ACESTREAM_CACHE_ROOT)
        # We append the container ID to it
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
        
        # Determine internal container path for cache
        # This depends on where the engine is configured to store cache
        # Default for AceStream is often .ACEStream or defined by --cache-dir
        
        # Check custom config for cache-dir
        from .custom_variant_config import get_config
        custom_config = get_config()
        
        container_cache_dir = "/root/.ACEStream" # Default
        
        if custom_config:
            for param in custom_config.parameters:
                if param.name == "--cache-dir" and param.enabled and param.value:
                    # Simplify path (handle ~)
                    val = str(param.value)
                    if val.startswith("~"):
                         container_cache_dir = val.replace("~", "/root", 1)
                    else:
                        container_cache_dir = val
                    break
                    
        return {
            host_path: {
                'bind': container_cache_dir,
                'mode': 'rw'
            }
        }

    async def prune_orphaned_caches(self):
        """
        Scan cache directory and remove folders that don't belong to active engines.
        This runs periodically.
        """
        if not self.is_enabled():
            return

        try:
            # List directories in mount path
            if not self.mount_path.exists():
                return
                
            entries = list(self.mount_path.iterdir())
            cache_dirs = {p.name for p in entries if p.is_dir()}
            
            if not cache_dirs:
                return

            # Get active container IDs
            cli = get_client()
            containers = cli.containers.list(filters={"label": cfg.CONTAINER_LABEL})
            active_ids = {c.id[:12] for c in containers}
            
            # Identify orphans
            orphans = cache_dirs - active_ids
            
            for orphan in orphans:
                # Security check: ensure it looks like a container ID (12 hex chars)
                # to avoid deleting random folders if user mapped root wrong
                if len(orphan) == 12 and all(c in '0123456789abcdef' for c in orphan):
                    logger.info(f"Removing orphaned cache directory: {orphan}")
                    try:
                        shutil.rmtree(self.mount_path / orphan)
                    except Exception as e:
                        logger.error(f"Failed to remove orphan {orphan}: {e}")
                else:
                    logger.warning(f"Found unknown directory in cache mount: {orphan}, skipping safety check")

        except Exception as e:
            logger.error(f"Error checking for orphaned caches: {e}")

    async def start_pruner(self, interval_s: int = 300):
        """Start the background pruner task."""
        while True:
            try:
                await asyncio.sleep(interval_s)
                await self.prune_orphaned_caches()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Pruner task error: {e}")
                await asyncio.sleep(60) # Wait a bit on error before retrying

# Global instance
engine_cache_manager = EngineCacheManager()
