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
        self._last_change_time = None
        self._debounce_interval_s = 3.0  # Increased from 1.0 to 3.0 seconds to reduce noise

    async def start(self):
        """Start the monitoring tasks."""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        
        # Start both monitoring and autoscaling tasks
        self._task = asyncio.create_task(self._monitor_docker())
        self._autoscale_task = asyncio.create_task(self._periodic_autoscale())
        
        # Gluetun monitoring is now started earlier in main.py to avoid race condition
        # with ensure_minimum() during startup
        
        logger.info(f"Docker monitor started with {cfg.MONITOR_INTERVAL_S}s interval")

    async def stop(self):
        """Stop the monitoring tasks."""
        self._stop.set()
        if self._task:
            await self._task
        if self._autoscale_task:
            await self._autoscale_task
        
        # Gluetun monitoring is now stopped in main.py

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
                await loop.run_in_executor(None, ensure_minimum)
                
                # Periodic cache cleanup for idle engines (0 streams)
                await loop.run_in_executor(None, self._periodic_cache_cleanup)
                
                # Also check for engines that can be cleaned up after grace period
                if cfg.AUTO_DELETE:
                    await loop.run_in_executor(None, self._cleanup_empty_engines)
                
            except Exception as e:
                logger.error(f"Error in periodic autoscaling: {e}")

    def _periodic_cache_cleanup(self):
        """Periodically clean cache for engines with 0 active streams."""
        try:
            from .provisioner import clear_acestream_cache
            from ..services.db import SessionLocal
            from ..models.db_models import EngineRow
            
            # Get all engines
            all_engines = state.list_engines()
            active_streams = state.list_streams(status="started")
            used_container_ids = {stream.container_id for stream in active_streams}
            
            # Find idle engines (0 streams) and clean their cache
            for engine in all_engines:
                if engine.container_id not in used_container_ids:
                    try:
                        logger.debug(f"Running periodic cache cleanup for idle engine {engine.container_id[:12]}")
                        success, cache_size = clear_acestream_cache(engine.container_id)
                        
                        # Update engine state with cleanup info
                        if success:
                            with state._lock:
                                eng = state.engines.get(engine.container_id)
                                if eng:
                                    eng.last_cache_cleanup = state.now()
                                    eng.cache_size_bytes = cache_size
                            
                            # Update database as well
                            try:
                                with SessionLocal() as s:
                                    engine_row = s.get(EngineRow, engine.container_id)
                                    if engine_row:
                                        engine_row.last_cache_cleanup = state.now()
                                        engine_row.cache_size_bytes = cache_size
                                        s.commit()
                            except Exception:
                                pass
                                
                    except Exception as e:
                        logger.error(f"Failed to cleanup cache for engine {engine.container_id[:12]}: {e}")
                            
        except Exception as e:
            logger.error(f"Error in periodic cache cleanup: {e}")
    
    def _cleanup_empty_engines(self):
        """Clean up engines that have been empty past their grace period."""
        try:
            # In redundant VPN mode, don't clean up engines if:
            # 1. One VPN is unhealthy (system is in degraded mode)
            # 2. A VPN recently recovered (system is stabilizing)
            # This prevents premature cleanup during VPN recovery
            if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
                vpn1_healthy = gluetun_monitor.is_healthy(cfg.GLUETUN_CONTAINER_NAME)
                vpn2_healthy = gluetun_monitor.is_healthy(cfg.GLUETUN_CONTAINER_NAME_2)
                
                # If one VPN is unhealthy, don't clean up engines
                if vpn1_healthy != vpn2_healthy:
                    logger.debug("Skipping empty engine cleanup - VPN in degraded mode")
                    return
                
                # Check if any VPN recently recovered
                vpn1_monitor = gluetun_monitor.get_vpn_monitor(cfg.GLUETUN_CONTAINER_NAME)
                vpn2_monitor = gluetun_monitor.get_vpn_monitor(cfg.GLUETUN_CONTAINER_NAME_2)
                
                if vpn1_monitor and vpn1_monitor.is_in_recovery_stabilization_period():
                    logger.debug(f"Skipping empty engine cleanup - VPN '{cfg.GLUETUN_CONTAINER_NAME}' recently recovered")
                    return
                if vpn2_monitor and vpn2_monitor.is_in_recovery_stabilization_period():
                    logger.debug(f"Skipping empty engine cleanup - VPN '{cfg.GLUETUN_CONTAINER_NAME_2}' recently recovered")
                    return
            
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
            from .replica_validator import replica_validator
            
            # Get current container IDs from Docker with error handling
            try:
                current_containers = list_managed()
                current_container_ids = {c.id for c in current_containers if c.status == 'running'}
            except Exception as e:
                # If Docker socket is temporarily unavailable, skip this sync iteration
                logger.warning(f"Docker socket temporarily unavailable during sync, will retry next iteration: {e}")
                return
            
            # Detect changes
            added = current_container_ids - self._last_container_ids
            removed = self._last_container_ids - current_container_ids
            
            if added or removed:
                now = datetime.now(timezone.utc)
                
                # Debounce rapid changes to prevent excessive operations
                if (self._last_change_time and 
                    (now - self._last_change_time).total_seconds() < self._debounce_interval_s):
                    logger.info(f"Debouncing rapid Docker state changes - skipping (last change: {self._last_change_time})")
                    return
                
                self._last_change_time = now
                logger.info(f"Docker state change detected: +{len(added)}, -{len(removed)} containers")
                
                # Remove engines that no longer exist in Docker
                for container_id in removed:
                    engine = state.get_engine(container_id)
                    if engine:
                        logger.info(f"Removing engine {container_id[:12]} - container no longer exists")
                        state.remove_engine(container_id)
                
                # Update tracking before reindex to prevent race conditions
                self._last_container_ids = current_container_ids
                
                # Re-index to pick up new containers (only if there are new ones)
                if added:
                    logger.info("Re-indexing to pick up new containers")
                    # Run reindex in a thread to avoid blocking
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, reindex_existing)
                
                # Do a lightweight validation check instead of full reindex
                # Let replica_validator handle the heavy lifting with its own coordination
                if replica_validator.request_sync_coordination("monitor"):
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: replica_validator.validate_and_sync_state(force_reindex=False))
                else:
                    logger.info("Monitor sync request denied by coordination - another sync in progress")
            else:
                # Even if no changes, update last_seen timestamps for existing engines
                now = state.now()
                with state._lock:
                    for container_id in current_container_ids:
                        if container_id in state.engines:
                            state.engines[container_id].last_seen = now
                
                # Periodic consistency check (less frequent)
                if not replica_validator.is_state_consistent():
                    logger.warning("State inconsistency detected during periodic check")
                    # Use coordination to prevent conflicts
                    if replica_validator.request_sync_coordination("monitor_periodic"):
                        # Use async execution to prevent blocking
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, lambda: replica_validator.validate_and_sync_state(force_reindex=True))
                    else:
                        logger.info("Monitor periodic sync request denied by coordination - another sync in progress")
                            
        except Exception as e:
            logger.error(f"Error syncing with Docker: {e}")

# Global monitor instance
docker_monitor = DockerMonitor()