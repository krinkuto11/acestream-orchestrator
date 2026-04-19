
"""
Engine Cache Manager

Manages Docker volumes for AceStream engine caches.
Handles volume creation, mounting configuration, and cleanup of volumes.
"""

import logging
import asyncio
from typing import Optional, Dict, List
from pathlib import Path

from ..core.config import cfg
from .docker_client import get_client

logger = logging.getLogger(__name__)

class EngineCacheManager:
    """
    Manages Docker volumes for engine caches.
    Strictly uses named Docker volumes for better portability and management.
    """
    
    def __init__(self):
        self._last_stats = {
            "total_size_bytes": 0,
            "volume_count": 0,
            "timestamp": 0
        }

    @staticmethod
    def _cache_mounts_configured(config) -> bool:
        return bool(
            config and (
                getattr(config, "disk_cache_mount_enabled", False)
                or getattr(config, "torrent_folder_mount_enabled", False)
            )
        )

    def is_enabled(self) -> bool:
        """Check if disk cache mounting is configured and enabled."""
        from .engine_config import get_config

        engine_config = get_config()
        return self._cache_mounts_configured(engine_config)

    # Maximum length for container name suffix used in volume names
    _CONTAINER_NAME_TRUNCATE_LENGTH = 12

    def _get_volume_name(self, container_name: str) -> str:
        """Generate a volume name for the container."""
        # Use abbreviated name for consistency
        short_name = container_name[:self._CONTAINER_NAME_TRUNCATE_LENGTH]
        return f"acestream-cache-{short_name}"

    def setup_cache(self, container_name: str) -> bool:
        """
        Create a Docker cache volume for the specific engine.
        
        Args:
            container_name: The container name
            
        Returns:
            True if successful, False otherwise
        """
        if not self.is_enabled():
            return False
        
        try:
            cli = get_client()
            volume_name = self._get_volume_name(container_name)
            
            # Check if volume already exists and remove if it does (fresh start)
            try:
                existing_volume = cli.volumes.get(volume_name)
                logger.debug(f"Volume {volume_name} already exists, removing it first")
                existing_volume.remove(force=True)
            except Exception:
                pass
            
            # Create the volume
            cli.volumes.create(name=volume_name)
            logger.info(f"Created Docker volume {volume_name} for engine {container_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to setup Docker volume for {container_name}: {e}")
            return False

    def cleanup_cache(self, container_name: str) -> bool:
        """
        Remove the Docker cache volume for the specific engine.
        """
        if not self.is_enabled():
            return False
        
        try:
            cli = get_client()
            volume_name = self._get_volume_name(container_name)
            
            try:
                volume = cli.volumes.get(volume_name)
                volume.remove(force=True)
                logger.info(f"Removed Docker volume {volume_name} for engine {container_name}")
                return True
            except Exception as e:
                logger.debug(f"Volume {volume_name} not found or already removed: {e}")
                return True
        except Exception as e:
            logger.error(f"Failed to cleanup Docker volume for {container_name}: {e}")
            return False

    def get_mount_config(self, container_name: str) -> Optional[Dict]:
        """
        Get the volume mount configuration for provisioner.
        """
        if not self.is_enabled():
            return None
        
        from .engine_config import get_config

        engine_config = get_config()
        
        parent_cache_dir = "/dev/shm/.ACEStream" # Default parent
        
        if engine_config:
            for param in engine_config.parameters:
                if param.name == "--cache-dir" and param.enabled and param.value:
                    val = str(param.value)
                    if val.startswith("~"):
                         parent_cache_dir = val.replace("~", "/dev/shm", 1)
                    else:
                        parent_cache_dir = val
                    break
        
        # The user wants "internal" mounting that maps the whole .ACEStream folder
        # this ensures both .acestream_cache and collected_torrent_files are persisted 
        # in the same Docker volume if no host bind mount is specified.
        volume_name = self._get_volume_name(container_name)
        return {
            volume_name: {
                'bind': parent_cache_dir,
                'mode': 'rw'
            }
        }

    async def get_total_cache_size(self) -> Dict:
        """
        Calculate total size of all AceStream cache volumes.
        Since we can't easily get volume size from the host, we use 'du' via a helper container.
        """
        try:
            cli = get_client()
            all_volumes = cli.volumes.list()
            acestream_volumes = [v for v in all_volumes if v.name.startswith('acestream-cache-')]
            
            if not acestream_volumes:
                return {"total_bytes": 0, "volume_count": 0}

            total_size = 0
            # To get accurate size, we mount these volumes to a tiny container and run du
            # We do them one by one or in batches if there are many
            for vol in acestream_volumes:
                try:
                    # Run a tiny helper to get size
                    # Note: We use alpine/busybox which is very small
                    output = cli.containers.run(
                        "alpine",
                        "du -sb /cache",
                        volumes={vol.name: {'bind': '/cache', 'mode': 'ro'}},
                        remove=True,
                        stderr=False
                    ).decode('utf-8')
                    # Output is usually "1234\t/cache\n"
                    size_str = output.split()[0]
                    total_size += int(size_str)
                except Exception as e:
                    logger.warning(f"Failed to get size for volume {vol.name}: {e}")

            return {
                "total_bytes": total_size,
                "volume_count": len(acestream_volumes)
            }
        except Exception as e:
            logger.error(f"Error calculating total cache size: {e}")
            return {"total_bytes": 0, "volume_count": 0}

    async def purge_orphaned_caches(self):
        """
        Remove volumes that don't belong to active engines.
        """
        try:
            cli = get_client()
            all_volumes = cli.volumes.list()
            acestream_volumes = [v for v in all_volumes if v.name.startswith('acestream-cache-')]
            
            from .state import state
            active_engines = state.list_engines()
            active_volume_names = {self._get_volume_name(e.container_name) for e in active_engines if e.container_name}
            
            for volume in acestream_volumes:
                if volume.name not in active_volume_names:
                    try:
                        volume.remove(force=True)
                        logger.info(f"Removed orphaned volume: {volume.name}")
                    except Exception as e:
                        logger.warning(f"Failed to remove orphaned volume {volume.name}: {e}")
        except Exception as e:
            logger.error(f"Error purging orphaned caches: {e}")

    async def purge_all_contents(self):
        """
        Clear contents of all AceStream cache volumes using a helper container.
        """
        try:
            cli = get_client()
            all_volumes = cli.volumes.list()
            acestream_volumes = [v for v in all_volumes if v.name.startswith('acestream-cache-')]
            
            if not acestream_volumes:
                return
            
            logger.info(f"Purging contents of {len(acestream_volumes)} cache volumes")
            
            for vol in acestream_volumes:
                try:
                    cli.containers.run(
                        "alpine",
                        "rm -rf /cache/*",
                        volumes={vol.name: {'bind': '/cache', 'mode': 'rw'}},
                        remove=True,
                        stderr=False
                    )
                    logger.debug(f"Purged content of volume {vol.name}")
                except Exception as e:
                    logger.error(f"Failed to purge volume {vol.name}: {e}")
                    
            logger.info("Cache purge complete")
        except Exception as e:
            logger.error(f"Error purging cache contents: {e}")

    async def start_pruner(self):
        """
        Periodically prune orphaned caches and optionally purge content according to config.
        """
        logger.info("Starting EngineCacheManager background service")
        while True:
            sleep_seconds = 300 
            
            try:
                # 1. Prune orphaned caches (clean up deleted containers)
                await self.purge_orphaned_caches()
                
                # 2. Check for periodic purge if enabled
                from .engine_config import get_config

                config = get_config()
                
                if self._cache_mounts_configured(config) and config.disk_cache_mount_enabled:
                    if config.disk_cache_prune_enabled:
                        # Periodic full purge of content
                        await self.purge_all_contents()
                        
                        interval_mins = config.disk_cache_prune_interval
                        sleep_seconds = max(60, interval_mins * 60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cache manager loop: {e}")
            
            await asyncio.sleep(sleep_seconds)

# Global instance
engine_cache_manager = EngineCacheManager()
