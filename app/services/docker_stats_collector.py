"""
Background service for continuously collecting Docker container statistics.
Similar to the collector service for stream stats, but for Docker engine stats.
"""

import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime, timezone
from .state import state
from .docker_stats import get_multiple_container_stats
from ..core.config import cfg

logger = logging.getLogger(__name__)


class DockerStatsCollector:
    """
    Continuously collects Docker stats for all engines in the background.
    This makes stats instantly available for UI without on-demand API waits.
    """
    
    def __init__(self):
        self._task = None
        self._stop = asyncio.Event()
        self._stats_cache: Dict[str, Dict] = {}  # container_id -> stats
        self._total_stats_cache: Optional[Dict] = None
        self._last_update: Optional[datetime] = None
        
        # Dynamic collection interval based on engine count
        # UI typically polls every 5s, so we adapt:
        # - 0 engines: 10s (low priority when idle)
        # - 1-5 engines: 3s (responsive for small deployments)
        # - 6+ engines: 2s (keep up with high load)
        self._min_collection_interval = 2.0
        self._max_collection_interval = 10.0
        self._default_collection_interval = 3.0
    
    def _get_dynamic_interval(self, engine_count: int) -> float:
        """Calculate collection interval based on engine count
        
        Args:
            engine_count: Number of engines currently running
            
        Returns:
            Collection interval in seconds
        """
        if engine_count == 0:
            # No engines - use max interval to reduce overhead
            return self._max_collection_interval
        elif engine_count <= 5:
            # Few engines - use default interval
            return self._default_collection_interval
        else:
            # Many engines - use min interval for responsiveness
            return self._min_collection_interval
    
    async def start(self):
        """Start the background stats collection task."""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(f"Docker stats collector started with dynamic interval ({self._min_collection_interval}s-{self._max_collection_interval}s)")
    
    async def stop(self):
        """Stop the background stats collection task."""
        self._stop.set()
        if self._task:
            await self._task
        logger.info("Docker stats collector stopped")
    
    async def _run(self):
        """Main collection loop with dynamic interval."""
        while not self._stop.is_set():
            try:
                # Run stats collection in thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._collect_stats)
                
                # Calculate next interval based on current engine count
                engines = state.list_engines()
                interval = self._get_dynamic_interval(len(engines))
                
                logger.debug(f"Next stats collection in {interval}s ({len(engines)} engines)")
            except Exception as e:
                logger.error(f"Error collecting Docker stats: {e}")
                interval = self._default_collection_interval
            
            # Wait for next collection interval
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    
    def _collect_stats(self):
        """
        Collect stats for all engines and cache them.
        This runs in a thread pool to avoid blocking the event loop.
        """
        try:
            # Get all current engines
            engines = state.list_engines()
            if not engines:
                # No engines, clear caches
                self._stats_cache = {}
                self._total_stats_cache = {
                    'total_cpu_percent': 0.0,
                    'total_memory_usage': 0,
                    'total_network_rx_bytes': 0,
                    'total_network_tx_bytes': 0,
                    'total_block_read_bytes': 0,
                    'total_block_write_bytes': 0,
                    'container_count': 0
                }
                self._last_update = datetime.now(timezone.utc)
                logger.debug("No engines to collect stats for")
                return
            
            container_ids = [e.container_id for e in engines]
            
            # Collect stats for all containers in batch (efficient)
            stats_dict = get_multiple_container_stats(container_ids)
            
            # Update cache with new stats
            self._stats_cache = stats_dict
            
            # Calculate total stats from cached individual stats
            # This is more efficient than calling get_total_stats separately
            total = {
                'total_cpu_percent': 0.0,
                'total_memory_usage': 0,
                'total_network_rx_bytes': 0,
                'total_network_tx_bytes': 0,
                'total_block_read_bytes': 0,
                'total_block_write_bytes': 0,
                'container_count': 0
            }
            
            for stats in stats_dict.values():
                total['total_cpu_percent'] += stats.get('cpu_percent', 0)
                total['total_memory_usage'] += stats.get('memory_usage', 0)
                total['total_network_rx_bytes'] += stats.get('network_rx_bytes', 0)
                total['total_network_tx_bytes'] += stats.get('network_tx_bytes', 0)
                total['total_block_read_bytes'] += stats.get('block_read_bytes', 0)
                total['total_block_write_bytes'] += stats.get('block_write_bytes', 0)
                total['container_count'] += 1
            
            # Round CPU percent
            total['total_cpu_percent'] = round(total['total_cpu_percent'], 2)
            
            self._total_stats_cache = total
            self._last_update = datetime.now(timezone.utc)
            
            logger.debug(f"Collected stats for {len(stats_dict)} engines")
            
        except Exception as e:
            logger.error(f"Error in stats collection: {e}")
    
    def get_engine_stats(self, container_id: str) -> Optional[Dict]:
        """
        Get cached stats for a specific engine.
        
        Args:
            container_id: Docker container ID
            
        Returns:
            Stats dictionary or None if not available
        """
        return self._stats_cache.get(container_id)
    
    def get_total_stats(self) -> Dict:
        """
        Get cached total stats across all engines.
        
        Returns:
            Total stats dictionary
        """
        if self._total_stats_cache is None:
            # No stats collected yet, return zeros
            return {
                'total_cpu_percent': 0.0,
                'total_memory_usage': 0,
                'total_network_rx_bytes': 0,
                'total_network_tx_bytes': 0,
                'total_block_read_bytes': 0,
                'total_block_write_bytes': 0,
                'container_count': 0
            }
        return self._total_stats_cache
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """
        Get cached stats for all engines.
        
        Returns:
            Dictionary mapping container_id to stats
        """
        return self._stats_cache.copy()
    
    def get_last_update(self) -> Optional[datetime]:
        """
        Get timestamp of last successful stats collection.
        
        Returns:
            Datetime of last update or None
        """
        return self._last_update


# Global collector instance
docker_stats_collector = DockerStatsCollector()
