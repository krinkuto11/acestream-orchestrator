"""
Gluetun VPN integration and health monitoring service.

This module provides:
- Gluetun container health monitoring
- VPN connection status tracking  
- Integration with AceStream engine lifecycle
- VPN port forwarding support
"""

import asyncio
import logging
import httpx
import os
import time
from datetime import datetime, timezone
from typing import Optional
from .docker_client import get_client
from ..core.config import cfg
from ..utils.debug_logger import get_debug_logger
from docker.errors import NotFound

logger = logging.getLogger(__name__)


class GluetunMonitor:
    """Monitors Gluetun VPN container health and manages VPN-dependent operations."""
    
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_health_status: Optional[bool] = None
        self._health_transition_callbacks = []
        
        # Port forwarding cache to reduce API calls
        self._cached_port: Optional[int] = None
        self._port_cache_time: Optional[datetime] = None
        self._port_cache_ttl_seconds: int = cfg.GLUETUN_PORT_CACHE_TTL_S  # Use config value
        
        # Track health stability to prevent engine restarts during initial startup
        self._startup_grace_period_s = 60  # 60 second grace period after first healthy status
        self._first_healthy_time: Optional[datetime] = None
        self._consecutive_healthy_count = 0
        
    async def start(self):
        """Start the Gluetun monitoring task."""
        if not cfg.GLUETUN_CONTAINER_NAME:
            logger.info("Gluetun monitoring disabled - no container name configured")
            return
            
        if self._task and not self._task.done():
            return
            
        self._stop.clear()
        self._task = asyncio.create_task(self._monitor_gluetun())
        logger.info(f"Gluetun monitor started for container '{cfg.GLUETUN_CONTAINER_NAME}' with {cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S}s interval")
    
    async def stop(self):
        """Stop the Gluetun monitoring task."""
        self._stop.set()
        if self._task:
            await self._task
            
    def add_health_transition_callback(self, callback):
        """Add a callback to be called when Gluetun health status changes."""
        self._health_transition_callbacks.append(callback)
    
    async def _monitor_gluetun(self):
        """Main monitoring loop for Gluetun health status and port caching."""
        while not self._stop.is_set():
            try:
                current_health = await self._check_gluetun_health()
                now = datetime.now(timezone.utc)
                
                # Track first healthy status and consecutive healthy checks
                if current_health:
                    if self._first_healthy_time is None:
                        self._first_healthy_time = now
                        logger.info(f"Gluetun first became healthy at {now} - starting startup grace period")
                    self._consecutive_healthy_count += 1
                else:
                    self._consecutive_healthy_count = 0
                
                # Detect health status transitions
                if self._last_health_status is not None and current_health != self._last_health_status:
                    await self._handle_health_transition(self._last_health_status, current_health)
                
                self._last_health_status = current_health
                
                # Background refresh of port cache if it's getting stale
                if current_health and not self._is_port_cache_valid():
                    try:
                        await self._fetch_and_cache_port()
                    except Exception as e:
                        logger.debug(f"Background port cache refresh failed: {e}")
                
            except Exception as e:
                logger.error(f"Error monitoring Gluetun health: {e}")
            
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    
    async def _check_gluetun_health(self) -> bool:
        """Check if Gluetun container is healthy."""
        debug_log = get_debug_logger()
        check_start = time.time()
        
        try:
            cli = get_client()
            container = cli.containers.get(cfg.GLUETUN_CONTAINER_NAME)
            container.reload()
            
            # Check container status
            if container.status != "running":
                duration = time.time() - check_start
                logger.warning(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' is not running (status: {container.status})")
                debug_log.log_vpn("health_check",
                                 status="not_running",
                                 duration=duration,
                                 container_status=container.status)
                return False
            
            # Check Docker health status if available
            health = container.attrs.get("State", {}).get("Health", {})
            if health:
                health_status = health.get("Status")
                duration = time.time() - check_start
                
                if health_status == "unhealthy":
                    logger.warning(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' is unhealthy")
                    debug_log.log_vpn("health_check",
                                     status="unhealthy",
                                     duration=duration,
                                     health_status=health_status)
                    return False
                elif health_status == "healthy":
                    logger.debug(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' is healthy")
                    debug_log.log_vpn("health_check",
                                     status="healthy",
                                     duration=duration,
                                     health_status=health_status)
                    return True
                else:
                    # Health status might be "starting" or "none"
                    logger.debug(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' health status: {health_status}")
                    debug_log.log_vpn("health_check",
                                     status=health_status,
                                     duration=duration,
                                     health_status=health_status)
                    # Consider container healthy if running but health status is starting/none
                    return True
            else:
                duration = time.time() - check_start
                # No health check configured, consider healthy if running
                logger.debug(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' has no health check, considering healthy")
                debug_log.log_vpn("health_check",
                                 status="healthy_no_healthcheck",
                                 duration=duration)
                return True
                
        except NotFound:
            duration = time.time() - check_start
            logger.error(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' not found")
            debug_log.log_vpn("health_check",
                             status="not_found",
                             duration=duration,
                             error="Container not found")
            return False
        except Exception as e:
            duration = time.time() - check_start
            logger.error(f"Error checking Gluetun health: {e}")
            debug_log.log_vpn("health_check",
                             status="error",
                             duration=duration,
                             error=str(e))
            return False
    
    async def _handle_health_transition(self, old_status: bool, new_status: bool):
        """Handle Gluetun health status transitions."""
        debug_log = get_debug_logger()
        now = datetime.now(timezone.utc)
        
        if old_status and not new_status:
            logger.warning("Gluetun VPN became unhealthy")
            debug_log.log_vpn("transition",
                             status="unhealthy",
                             old_status=old_status,
                             new_status=new_status)
            debug_log.log_stress_event("vpn_disconnection",
                                      severity="critical",
                                      description="Gluetun VPN became unhealthy")
            # Invalidate port cache when VPN becomes unhealthy
            self.invalidate_port_cache()
        elif not old_status and new_status:
            logger.info("Gluetun VPN recovered and is now healthy")
            debug_log.log_vpn("transition",
                             status="healthy",
                             old_status=old_status,
                             new_status=new_status)
            # Invalidate port cache to force fresh port check on VPN reconnection
            self.invalidate_port_cache()
            
            # Only restart engines if this is a real reconnection, not initial startup
            should_restart_engines = self._should_restart_engines_on_reconnection(now)
            
            if cfg.VPN_RESTART_ENGINES_ON_RECONNECT and should_restart_engines:
                logger.info("VPN reconnected after stable period - triggering AceStream engine restart")
                debug_log.log_vpn("restart_engines",
                                 status="triggered",
                                 reason="vpn_reconnection")
                await self._restart_acestream_engines()
            elif cfg.VPN_RESTART_ENGINES_ON_RECONNECT and not should_restart_engines:
                logger.info("VPN became healthy but skipping engine restart (startup grace period or insufficient stability)")
                debug_log.log_vpn("restart_engines",
                                 status="skipped",
                                 reason="grace_period_or_instability")
        
        # Call registered callbacks
        for callback in self._health_transition_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(old_status, new_status)
                else:
                    callback(old_status, new_status)
            except Exception as e:
                logger.error(f"Error in health transition callback: {e}")
    
    def _should_restart_engines_on_reconnection(self, now: datetime) -> bool:
        """
        Determine if engines should be restarted on VPN reconnection.
        
        This prevents unnecessary engine restarts during initial startup by requiring:
        1. A minimum grace period since first healthy status
        2. Sufficient consecutive healthy checks before the unhealthy period
        """
        # If we've never been healthy before, this is initial startup
        if self._first_healthy_time is None:
            return False
        
        # Check if we're still in the startup grace period
        time_since_first_healthy = (now - self._first_healthy_time).total_seconds()
        if time_since_first_healthy < self._startup_grace_period_s:
            logger.debug(f"Still in startup grace period ({time_since_first_healthy:.1f}s < {self._startup_grace_period_s}s)")
            return False
        
        # Additional check: only restart if we had sufficient stability before going unhealthy
        # This prevents restarts from brief network blips during startup
        min_stable_checks = 5  # At least 5 consecutive healthy checks (25s at 5s intervals)
        if self._consecutive_healthy_count < min_stable_checks:
            logger.debug(f"Insufficient stability before reconnection ({self._consecutive_healthy_count} < {min_stable_checks} checks)")
            return False
        
        logger.info(f"VPN reconnection detected: stable for {time_since_first_healthy:.1f}s with {self._consecutive_healthy_count} healthy checks")
        return True
    
    async def _restart_acestream_engines(self):
        """Restart all managed AceStream engines after VPN reconnection."""
        try:
            from .health import list_managed
            from .provisioner import stop_container
            from .state import state
            
            managed_containers = list_managed()
            running_engines = [c for c in managed_containers if c.status == "running"]
            
            if not running_engines:
                logger.info("No running AceStream engines to restart")
                return
            
            logger.info(f"Restarting {len(running_engines)} AceStream engines due to VPN reconnection")
            
            # Stop all running engines
            for container in running_engines:
                try:
                    logger.info(f"Stopping AceStream engine {container.id[:12]} for VPN restart")
                    stop_container(container.id)
                    # Remove from state
                    state.remove_engine(container.id)
                except Exception as e:
                    logger.error(f"Error stopping engine {container.id[:12]}: {e}")
            
            # The autoscaler will automatically start new engines to maintain MIN_REPLICAS
            logger.info("Engine restart completed - autoscaler will provision new engines")
            
        except Exception as e:
            logger.error(f"Error restarting AceStream engines: {e}")
    
    def is_healthy(self) -> Optional[bool]:
        """Get the current Gluetun health status."""
        return self._last_health_status
    
    async def wait_for_healthy(self, timeout: float = 30.0) -> bool:
        """Wait for Gluetun to become healthy, with timeout."""
        if not cfg.GLUETUN_CONTAINER_NAME:
            return True  # No Gluetun configured, consider healthy
            
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if await self._check_gluetun_health():
                return True
            await asyncio.sleep(1)
        
        return False

    async def get_forwarded_port(self) -> Optional[int]:
        """Get the VPN forwarded port from Gluetun API with caching."""
        if not cfg.GLUETUN_CONTAINER_NAME:
            return None
        
        # Check if we have a valid cached port
        if self._is_port_cache_valid():
            logger.debug(f"Using cached forwarded port: {self._cached_port}")
            return self._cached_port
            
        return await self._fetch_and_cache_port()
    
    def _is_port_cache_valid(self) -> bool:
        """Check if the cached port is still valid based on TTL."""
        if self._cached_port is None or self._port_cache_time is None:
            return False
        
        cache_age = (datetime.now(timezone.utc) - self._port_cache_time).total_seconds()
        return cache_age < self._port_cache_ttl_seconds
    
    async def _fetch_and_cache_port(self) -> Optional[int]:
        """Fetch the forwarded port from Gluetun API and cache it."""
        try:
            # Gluetun API endpoint for port forwarding
            # Connect to Gluetun container by name since we're in the same Docker network
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://{cfg.GLUETUN_CONTAINER_NAME}:{cfg.GLUETUN_API_PORT}/v1/openvpn/portforwarded", timeout=10)
                response.raise_for_status()
                data = response.json()
                port = data.get("port")
                if port:
                    port = int(port)
                    # Update cache
                    self._cached_port = port
                    self._port_cache_time = datetime.now(timezone.utc)
                    logger.info(f"Retrieved and cached VPN forwarded port: {port}")
                    return port
                else:
                    logger.warning("No port forwarding information available from Gluetun")
                    # Don't cache None values to allow retries
                    return None
        except Exception as e:
            logger.error(f"Failed to get forwarded port from Gluetun: {e}")
            # Don't cache errors to allow retries
            return None
    
    def get_cached_forwarded_port(self) -> Optional[int]:
        """Get the cached forwarded port without making API calls (synchronous)."""
        if self._is_port_cache_valid():
            return self._cached_port
        return None
    
    def invalidate_port_cache(self):
        """Invalidate the port cache to force a fresh API call on next request."""
        self._cached_port = None
        self._port_cache_time = None
        logger.debug("Port cache invalidated")

def get_forwarded_port_sync() -> Optional[int]:
    """Synchronous version of get_forwarded_port with caching support."""
    if not cfg.GLUETUN_CONTAINER_NAME:
        return None
    
    # First try to get from cache if available
    cached_port = gluetun_monitor.get_cached_forwarded_port()
    if cached_port is not None:
        logger.debug(f"Using cached forwarded port (sync): {cached_port}")
        return cached_port
        
    # If no cached port available, make API call
    try:
        import httpx
        with httpx.Client() as client:
            response = client.get(f"http://{cfg.GLUETUN_CONTAINER_NAME}:{cfg.GLUETUN_API_PORT}/v1/openvpn/portforwarded", timeout=10)
            response.raise_for_status()
            data = response.json()
            port = data.get("port")
            if port:
                port = int(port)
                # Update the monitor's cache
                gluetun_monitor._cached_port = port
                gluetun_monitor._port_cache_time = datetime.now(timezone.utc)
                logger.info(f"Retrieved and cached VPN forwarded port (sync): {port}")
                return port
            else:
                logger.warning("No port forwarding information available from Gluetun")
                return None
    except Exception as e:
        logger.error(f"Failed to get forwarded port from Gluetun: {e}")
        return None

def _double_check_connectivity_via_engines() -> str:
    """
    Double-check VPN connectivity by testing engine network connection status.
    This is used when Gluetun container health appears unhealthy but the issue
    might be unrelated to actual network connectivity.
    
    Returns "healthy" if any engine reports connected=true, "unhealthy" otherwise.
    """
    try:
        from .health import list_managed, check_engine_network_connection
        
        # Get all managed containers (engines)
        managed_containers = list_managed()
        running_engines = [c for c in managed_containers if c.status == "running"]
        
        if not running_engines:
            logger.debug("No running engines to test network connectivity")
            return "unhealthy"
        
        # Test connectivity on a few engines (max 3 to avoid excessive load)
        test_engines = running_engines[:3]
        connected_count = 0
        
        for container in test_engines:
            try:
                # Extract host and port from container
                # Engines typically run on localhost with different ports
                host = "127.0.0.1"  # Engines run locally
                port_env = container.attrs.get("Config", {}).get("Env", [])
                port = None
                
                # Look for port in environment variables
                for env_var in port_env:
                    if env_var.startswith("ACE_PORT="):
                        port = int(env_var.split("=")[1])
                        break
                
                if not port:
                    # Try to get from port mappings
                    ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
                    for port_spec in ports.keys():
                        if "/tcp" in port_spec:
                            port = int(port_spec.split("/")[0])
                            break
                
                if port and check_engine_network_connection(host, port):
                    connected_count += 1
                    logger.debug(f"Engine {container.id[:12]} reports network connected")
                
            except Exception as e:
                logger.debug(f"Error checking network connectivity for engine {container.id[:12]}: {e}")
                continue
        
        # If any engine reports connectivity, consider VPN healthy
        if connected_count > 0:
            logger.info(f"VPN double-check: {connected_count}/{len(test_engines)} engines report network connectivity - considering VPN healthy")
            return "healthy"
        else:
            logger.warning(f"VPN double-check: No engines report network connectivity")
            return "unhealthy"
            
    except Exception as e:
        logger.error(f"Error during VPN connectivity double-check: {e}")
        return "unhealthy"

def get_vpn_status() -> dict:
    """Get comprehensive VPN status information."""
    if not cfg.GLUETUN_CONTAINER_NAME:
        return {
            "enabled": False,
            "status": "disabled",
            "container_name": None,
            "container": None,  # Add for frontend compatibility
            "health": "unknown",
            "connected": False,  # Add boolean connected field
            "forwarded_port": None,
            "last_check": None,
            "last_check_at": None  # Add for frontend compatibility
        }
    
    try:
        from .docker_client import get_client
        from docker.errors import NotFound
        
        cli = get_client()
        container = cli.containers.get(cfg.GLUETUN_CONTAINER_NAME)
        container.reload()
        
        # Get container health
        container_running = container.status == "running"
        health_info = container.attrs.get("State", {}).get("Health", {})
        
        if health_info:
            health_status = health_info.get("Status", "unknown")
            if health_status == "unhealthy":
                # Double-check with engine network connectivity if container is unhealthy
                health = _double_check_connectivity_via_engines() 
            elif health_status == "healthy":
                health = "healthy"
            else:
                health = "starting" if container_running else "unknown"
        else:
            health = "healthy" if container_running else "unhealthy"
        
        # Get forwarded port (try cache first, fallback to API call)
        forwarded_port = None
        if container_running:
            forwarded_port = gluetun_monitor.get_cached_forwarded_port()
            if forwarded_port is None:
                # Only make API call if cache is empty
                forwarded_port = get_forwarded_port_sync()
        
        return {
            "enabled": True,
            "status": container.status,
            "container_name": cfg.GLUETUN_CONTAINER_NAME,
            "container": cfg.GLUETUN_CONTAINER_NAME,  # Add for frontend compatibility
            "health": health,
            "connected": health == "healthy",  # Add boolean connected field based on health
            "forwarded_port": forwarded_port,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat()  # Add for frontend compatibility
        }
        
    except NotFound:
        return {
            "enabled": True,
            "status": "not_found",
            "container_name": cfg.GLUETUN_CONTAINER_NAME,
            "container": cfg.GLUETUN_CONTAINER_NAME,  # Add for frontend compatibility
            "health": "unhealthy",
            "connected": False,  # Add boolean connected field
            "forwarded_port": None,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat()  # Add for frontend compatibility
        }
    except Exception as e:
        logger.error(f"Error getting VPN status: {e}")
        return {
            "enabled": True,
            "status": "error",
            "container_name": cfg.GLUETUN_CONTAINER_NAME,
            "container": cfg.GLUETUN_CONTAINER_NAME,  # Add for frontend compatibility
            "health": "unknown",
            "connected": False,  # Add boolean connected field
            "forwarded_port": None,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat(),  # Add for frontend compatibility
            "error": str(e)
        }


# Global Gluetun monitor instance
gluetun_monitor = GluetunMonitor()