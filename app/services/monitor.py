import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Set
from .state import state
from .health import list_managed
from .reindex import reindex_existing
from .autoscaler import ensure_minimum
from .gluetun import gluetun_monitor
from ..core.config import cfg

logger = logging.getLogger(__name__)

class DockerMonitor:
    """Continuously monitors Docker containers and keeps state in sync."""
    
    def __init__(self):
        self._task = None
        self._autoscale_task = None
        self._stop = asyncio.Event()
        self._last_container_ids: Set[str] = set()

    async def start(self):
        """Start the monitoring tasks."""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        
        # Start both monitoring and autoscaling tasks
        self._task = asyncio.create_task(self._monitor_docker())
        self._autoscale_task = asyncio.create_task(self._periodic_autoscale())
        
        # Start Gluetun monitoring if configured
        await gluetun_monitor.start()
        
        logger.info(f"Docker monitor started with {cfg.MONITOR_INTERVAL_S}s interval")

    async def stop(self):
        """Stop the monitoring tasks."""
        self._stop.set()
        if self._task:
            await self._task
        if self._autoscale_task:
            await self._autoscale_task
        
        # Stop Gluetun monitoring
        await gluetun_monitor.stop()

    async def _monitor_docker(self):
        """Main monitoring loop that syncs state with Docker."""
        while not self._stop.is_set():
            try:
                await self._sync_with_docker()
            except Exception as e:
                logger.error(f"Error in Docker monitoring: {e}")
            
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=cfg.MONITOR_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    async def _periodic_autoscale(self):
        """Periodically ensure minimum number of free engines and clean up empty engines."""
        while not self._stop.is_set():
            try:
                await asyncio.sleep(cfg.AUTOSCALE_INTERVAL_S)
                if self._stop.is_set():
                    break
                
                # Run autoscaling in a thread to avoid blocking
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._ensure_minimum_free_engines)
                
                # Also check for engines that can be cleaned up after grace period
                if cfg.AUTO_DELETE:
                    await loop.run_in_executor(None, self._cleanup_empty_engines)
                
            except Exception as e:
                logger.error(f"Error in periodic autoscaling: {e}")

    def _cleanup_empty_engines(self):
        """Clean up engines that have been empty past their grace period."""
        try:
            from .autoscaler import can_stop_engine
            from .provisioner import stop_container
            
            # Get all engines
            all_engines = state.list_engines()
            active_streams = state.list_streams(status="started")
            used_container_ids = {stream.container_id for stream in active_streams}
            
            # Find empty engines that can be stopped
            for engine in all_engines:
                if engine.container_id not in used_container_ids:
                    if can_stop_engine(engine.container_id, bypass_grace_period=False):
                        try:
                            logger.info(f"Cleaning up empty engine {engine.container_id[:12]} after grace period")
                            stop_container(engine.container_id)
                            state.remove_engine(engine.container_id)
                        except Exception as e:
                            logger.error(f"Failed to cleanup engine {engine.container_id[:12]}: {e}")
                            
        except Exception as e:
            logger.error(f"Error cleaning up empty engines: {e}")

    async def _sync_with_docker(self):
        """Synchronize state with actual Docker containers."""
        try:
            # Get current container IDs from Docker
            current_containers = list_managed()
            current_container_ids = {c.id for c in current_containers if c.status == 'running'}
            
            # Detect changes
            added = current_container_ids - self._last_container_ids
            removed = self._last_container_ids - current_container_ids
            
            if added or removed:
                logger.info(f"Docker state change detected: +{len(added)}, -{len(removed)} containers")
                
                # Remove engines that no longer exist in Docker
                for container_id in removed:
                    engine = state.get_engine(container_id)
                    if engine:
                        logger.info(f"Removing engine {container_id[:12]} - container no longer exists")
                        state.remove_engine(container_id)
                
                # Re-index to pick up new containers
                if added:
                    logger.info("Re-indexing to pick up new containers")
                    # Run reindex in a thread to avoid blocking
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, reindex_existing)
                
                self._last_container_ids = current_container_ids
            else:
                # Even if no changes, update last_seen timestamps for existing engines
                now = state.now()
                with state._lock:
                    for container_id in current_container_ids:
                        if container_id in state.engines:
                            state.engines[container_id].last_seen = now
                            
        except Exception as e:
            logger.error(f"Error syncing with Docker: {e}")

    def _ensure_minimum_free_engines(self):
        """Ensure minimum number of free (unused) engines are available."""
        try:
            # Get current state from both sources
            all_engines = state.list_engines()
            active_streams = state.list_streams(status="started")
            running_containers = [c for c in list_managed() if c.status == "running"]
            
            # Find engines that are currently in use
            used_container_ids = {stream.container_id for stream in active_streams}
            
            # Use Docker containers as the source of truth for total running count
            # This ensures we count all actual running containers, not just those tracked in state
            total_running = len(running_containers)
            used_engines = len(used_container_ids)
            free_count = total_running - used_engines
            
            # Check for state/Docker discrepancies and trigger reindex if needed
            state_engine_count = len(all_engines)
            if state_engine_count != total_running:
                logger.warning(f"State/Docker engine count mismatch: state={state_engine_count}, docker={total_running}. Triggering reindex...")
                # Trigger reindex to sync state with Docker
                from .reindex import reindex_existing
                reindex_existing()
                
                # Also check for orphaned engines in state that don't exist in Docker
                running_container_ids = {c.id for c in running_containers}
                state_container_ids = {engine.container_id for engine in all_engines}
                orphaned_engines = state_container_ids - running_container_ids
                
                if orphaned_engines:
                    logger.info(f"Removing {len(orphaned_engines)} orphaned engines from state")
                    for container_id in orphaned_engines:
                        state.remove_engine(container_id)
                
                # Recalculate after reindex
                all_engines = state.list_engines()
                total_running = len(running_containers)
                free_count = total_running - used_engines
            
            deficit = cfg.MIN_REPLICAS - free_count
            
            if deficit > 0:
                logger.info(f"Need {deficit} more free engines (total: {total_running}, used: {used_engines}, free: {free_count}, min_free: {cfg.MIN_REPLICAS})")
                ensure_minimum()
            else:
                logger.debug(f"Sufficient free engines: {free_count} free, {cfg.MIN_REPLICAS} required")
                
        except Exception as e:
            logger.error(f"Error ensuring minimum free engines: {e}")

# Global monitor instance
docker_monitor = DockerMonitor()