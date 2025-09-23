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
from datetime import datetime, timezone
from typing import Optional
from .docker_client import get_client
from ..core.config import cfg
from docker.errors import NotFound

logger = logging.getLogger(__name__)


class GluetunMonitor:
    """Monitors Gluetun VPN container health and manages VPN-dependent operations."""
    
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_health_status: Optional[bool] = None
        self._health_transition_callbacks = []
        
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
        """Main monitoring loop for Gluetun health status."""
        while not self._stop.is_set():
            try:
                current_health = await self._check_gluetun_health()
                
                # Detect health status transitions
                if self._last_health_status is not None and current_health != self._last_health_status:
                    await self._handle_health_transition(self._last_health_status, current_health)
                
                self._last_health_status = current_health
                
            except Exception as e:
                logger.error(f"Error monitoring Gluetun health: {e}")
            
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    
    async def _check_gluetun_health(self) -> bool:
        """Check if Gluetun container is healthy."""
        try:
            cli = get_client()
            container = cli.containers.get(cfg.GLUETUN_CONTAINER_NAME)
            container.reload()
            
            # Check container status
            if container.status != "running":
                logger.warning(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' is not running (status: {container.status})")
                return False
            
            # Check Docker health status if available
            health = container.attrs.get("State", {}).get("Health", {})
            if health:
                health_status = health.get("Status")
                if health_status == "unhealthy":
                    logger.warning(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' is unhealthy")
                    return False
                elif health_status == "healthy":
                    logger.debug(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' is healthy")
                    return True
                else:
                    # Health status might be "starting" or "none"
                    logger.debug(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' health status: {health_status}")
                    # Consider container healthy if running but health status is starting/none
                    return True
            else:
                # No health check configured, consider healthy if running
                logger.debug(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' has no health check, considering healthy")
                return True
                
        except NotFound:
            logger.error(f"Gluetun container '{cfg.GLUETUN_CONTAINER_NAME}' not found")
            return False
        except Exception as e:
            logger.error(f"Error checking Gluetun health: {e}")
            return False
    
    async def _handle_health_transition(self, old_status: bool, new_status: bool):
        """Handle Gluetun health status transitions."""
        if old_status and not new_status:
            logger.warning("Gluetun VPN became unhealthy")
        elif not old_status and new_status:
            logger.info("Gluetun VPN recovered and is now healthy")
            
            # If configured, trigger engine restart on VPN reconnection
            if cfg.VPN_RESTART_ENGINES_ON_RECONNECT:
                logger.info("VPN reconnected - triggering AceStream engine restart")
                await self._restart_acestream_engines()
        
        # Call registered callbacks
        for callback in self._health_transition_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(old_status, new_status)
                else:
                    callback(old_status, new_status)
            except Exception as e:
                logger.error(f"Error in health transition callback: {e}")
    
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
        """Get the VPN forwarded port from Gluetun API."""
        if not cfg.GLUETUN_CONTAINER_NAME:
            return None
            
        try:
            # Gluetun API endpoint for port forwarding
            # Connect to Gluetun container by name since we're in the same Docker network
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://{cfg.GLUETUN_CONTAINER_NAME}:{cfg.GLUETUN_API_PORT}/v1/openvpn/portforwarded", timeout=10)
                response.raise_for_status()
                data = response.json()
                port = data.get("port")
                if port:
                    logger.info(f"Retrieved VPN forwarded port: {port}")
                    return int(port)
                else:
                    logger.warning("No port forwarding information available from Gluetun")
                    return None
        except Exception as e:
            logger.error(f"Failed to get forwarded port from Gluetun: {e}")
            return None

def get_forwarded_port_sync() -> Optional[int]:
    """Synchronous version of get_forwarded_port for use in non-async contexts."""
    if not cfg.GLUETUN_CONTAINER_NAME:
        return None
        
    try:
        import httpx
        with httpx.Client() as client:
            response = client.get(f"http://{cfg.GLUETUN_CONTAINER_NAME}:{cfg.GLUETUN_API_PORT}/v1/openvpn/portforwarded", timeout=10)
            response.raise_for_status()
            data = response.json()
            port = data.get("port")
            if port:
                logger.info(f"Retrieved VPN forwarded port: {port}")
                return int(port)
            else:
                logger.warning("No port forwarding information available from Gluetun")
                return None
    except Exception as e:
        logger.error(f"Failed to get forwarded port from Gluetun: {e}")
        return None

def get_vpn_status() -> dict:
    """Get comprehensive VPN status information."""
    if not cfg.GLUETUN_CONTAINER_NAME:
        return {
            "enabled": False,
            "status": "disabled",
            "container_name": None,
            "health": "unknown",
            "forwarded_port": None,
            "last_check": None
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
                health = "unhealthy"
            elif health_status == "healthy":
                health = "healthy"
            else:
                health = "starting" if container_running else "unknown"
        else:
            health = "healthy" if container_running else "unhealthy"
        
        # Get forwarded port
        forwarded_port = get_forwarded_port_sync() if container_running else None
        
        return {
            "enabled": True,
            "status": container.status,
            "container_name": cfg.GLUETUN_CONTAINER_NAME,
            "health": health,
            "forwarded_port": forwarded_port,
            "last_check": datetime.now(timezone.utc).isoformat()
        }
        
    except NotFound:
        return {
            "enabled": True,
            "status": "not_found",
            "container_name": cfg.GLUETUN_CONTAINER_NAME,
            "health": "unhealthy",
            "forwarded_port": None,
            "last_check": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting VPN status: {e}")
        return {
            "enabled": True,
            "status": "error",
            "container_name": cfg.GLUETUN_CONTAINER_NAME,
            "health": "unknown",
            "forwarded_port": None,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }


# Global Gluetun monitor instance
gluetun_monitor = GluetunMonitor()