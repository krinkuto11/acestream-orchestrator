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
from typing import Optional, Dict
from .docker_client import get_client
from ..core.config import cfg
from ..utils.debug_logger import get_debug_logger
from docker.errors import NotFound

logger = logging.getLogger(__name__)


class VpnContainerMonitor:
    """Monitors a single VPN container's health and manages operations."""
    
    def __init__(self, container_name: str):
        self.container_name = container_name
        self._last_health_status: Optional[bool] = None
        
        # Port forwarding cache to reduce API calls
        self._cached_port: Optional[int] = None
        self._port_cache_time: Optional[datetime] = None
        self._port_cache_ttl_seconds: int = cfg.GLUETUN_PORT_CACHE_TTL_S
        self._last_logged_port: Optional[int] = None
        
        # Track the last known stable forwarded port for detecting changes
        self._last_stable_forwarded_port: Optional[int] = None
        self._last_port_check_time: Optional[datetime] = None
        self._port_check_interval_s: int = 30  # Check for port changes every 30 seconds
        
        # Track health stability to prevent engine restarts during initial startup
        self._startup_grace_period_s = 60
        self._first_healthy_time: Optional[datetime] = None
        self._consecutive_healthy_count = 0
        
        # Track when container became unhealthy for forced restart timeout
        self._unhealthy_since: Optional[datetime] = None
        self._force_restart_attempted: bool = False

    async def check_health(self) -> bool:
        """Check if VPN container is healthy."""
        debug_log = get_debug_logger()
        check_start = time.time()
        
        try:
            # Use increased timeout for better resilience during VPN lifecycle events
            cli = get_client(timeout=30)
            container = cli.containers.get(self.container_name)
            container.reload()
            
            # Check container status
            if container.status != "running":
                duration = time.time() - check_start
                logger.warning(f"VPN container '{self.container_name}' is not running (status: {container.status})")
                debug_log.log_vpn("health_check",
                                 status="not_running",
                                 duration=duration,
                                 container_name=self.container_name,
                                 container_status=container.status)
                return False
            
            # Check Docker health status if available
            health = container.attrs.get("State", {}).get("Health", {})
            if health:
                health_status = health.get("Status")
                duration = time.time() - check_start
                
                if health_status == "unhealthy":
                    logger.warning(f"VPN container '{self.container_name}' is unhealthy")
                    debug_log.log_vpn("health_check",
                                     status="unhealthy",
                                     duration=duration,
                                     container_name=self.container_name,
                                     health_status=health_status)
                    return False
                elif health_status == "healthy":
                    logger.debug(f"VPN container '{self.container_name}' is healthy")
                    debug_log.log_vpn("health_check",
                                     status="healthy",
                                     duration=duration,
                                     container_name=self.container_name,
                                     health_status=health_status)
                    return True
                else:
                    # Health status might be "starting" or "none"
                    logger.debug(f"VPN container '{self.container_name}' health status: {health_status}")
                    debug_log.log_vpn("health_check",
                                     status=health_status,
                                     duration=duration,
                                     container_name=self.container_name,
                                     health_status=health_status)
                    return True
            else:
                duration = time.time() - check_start
                logger.debug(f"VPN container '{self.container_name}' has no health check, considering healthy")
                debug_log.log_vpn("health_check",
                                 status="healthy_no_healthcheck",
                                 duration=duration,
                                 container_name=self.container_name)
                return True
                
        except NotFound:
            duration = time.time() - check_start
            logger.error(f"VPN container '{self.container_name}' not found")
            debug_log.log_vpn("health_check",
                             status="not_found",
                             duration=duration,
                             container_name=self.container_name,
                             error="Container not found")
            return False
        except Exception as e:
            duration = time.time() - check_start
            logger.error(f"Error checking VPN health for '{self.container_name}': {e}")
            debug_log.log_vpn("health_check",
                             status="error",
                             duration=duration,
                             container_name=self.container_name,
                             error=str(e))
            return False

    def is_healthy(self) -> Optional[bool]:
        """Get the current health status."""
        return self._last_health_status

    def update_health_status(self, current_health: bool, now: datetime):
        """Update health status and tracking."""
        # Track first healthy status and consecutive healthy checks
        if current_health:
            if self._first_healthy_time is None:
                self._first_healthy_time = now
                logger.info(f"VPN '{self.container_name}' first became healthy at {now}")
            self._consecutive_healthy_count += 1
            # Reset unhealthy tracking when healthy
            self._unhealthy_since = None
            self._force_restart_attempted = False
        else:
            self._consecutive_healthy_count = 0
            # Track when became unhealthy
            if self._unhealthy_since is None:
                self._unhealthy_since = now
        
        self._last_health_status = current_health

    def should_restart_engines_on_reconnection(self, now: datetime) -> bool:
        """Determine if engines should be restarted on VPN reconnection."""
        if self._first_healthy_time is None:
            return False
        
        time_since_first_healthy = (now - self._first_healthy_time).total_seconds()
        if time_since_first_healthy < self._startup_grace_period_s:
            logger.debug(f"VPN '{self.container_name}' still in startup grace period")
            return False
        
        min_stable_checks = 5
        if self._consecutive_healthy_count < min_stable_checks:
            logger.debug(f"VPN '{self.container_name}' insufficient stability before reconnection")
            return False
        
        logger.info(f"VPN '{self.container_name}' reconnection detected - will restart engines")
        return True

    def should_force_restart(self) -> bool:
        """Check if VPN container should be forcefully restarted due to prolonged unhealthy state."""
        if not self._unhealthy_since or self._force_restart_attempted:
            return False
        
        unhealthy_duration = (datetime.now(timezone.utc) - self._unhealthy_since).total_seconds()
        return unhealthy_duration >= cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S

    async def force_restart_container(self):
        """Force restart the VPN container using Docker socket."""
        if self._force_restart_attempted:
            return
        
        try:
            logger.warning(f"Force restarting VPN container '{self.container_name}' after {cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S}s timeout")
            cli = get_client(timeout=30)
            container = cli.containers.get(self.container_name)
            container.restart()
            self._force_restart_attempted = True
            logger.info(f"VPN container '{self.container_name}' restart initiated")
        except Exception as e:
            logger.error(f"Failed to force restart VPN container '{self.container_name}': {e}")

    async def get_forwarded_port(self) -> Optional[int]:
        """Get the VPN forwarded port from Gluetun API with caching."""
        if self._is_port_cache_valid():
            logger.debug(f"Using cached forwarded port for '{self.container_name}': {self._cached_port}")
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
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://{self.container_name}:{cfg.GLUETUN_API_PORT}/v1/openvpn/portforwarded", timeout=10)
                response.raise_for_status()
                data = response.json()
                port = data.get("port")
                if port:
                    port = int(port)
                    self._cached_port = port
                    self._port_cache_time = datetime.now(timezone.utc)
                    if self._last_logged_port != port:
                        logger.info(f"Retrieved VPN forwarded port for '{self.container_name}': {port}")
                        self._last_logged_port = port
                    return port
                else:
                    logger.warning(f"No port forwarding info from '{self.container_name}'")
                    return None
        except Exception as e:
            logger.error(f"Failed to get forwarded port from '{self.container_name}': {e}")
            return None

    def get_cached_forwarded_port(self) -> Optional[int]:
        """Get cached forwarded port without API calls."""
        if self._is_port_cache_valid():
            return self._cached_port
        return None

    def invalidate_port_cache(self):
        """Invalidate the port cache."""
        self._cached_port = None
        self._port_cache_time = None
        logger.debug(f"Port cache invalidated for '{self.container_name}'")

    async def check_port_change(self) -> Optional[tuple[int, int]]:
        """
        Check if the forwarded port has changed since the last check.
        
        This check is throttled to avoid excessive API calls. It only runs if:
        - VPN is healthy
        - Sufficient time has passed since last check (based on _port_check_interval_s)
        
        Returns:
            Optional[tuple[int, int]]: (old_port, new_port) if port changed, None otherwise
        """
        # Only check for port changes if VPN is healthy
        if not self._last_health_status:
            return None
        
        # Throttle port change checks to avoid excessive API calls
        now = datetime.now(timezone.utc)
        if self._last_port_check_time is not None:
            time_since_last_check = (now - self._last_port_check_time).total_seconds()
            if time_since_last_check < self._port_check_interval_s:
                return None
        
        self._last_port_check_time = now
        
        # Fetch the current port
        current_port = await self._fetch_and_cache_port()
        
        # If we have no current port, we can't detect a change
        if current_port is None:
            return None
        
        # If we have no previous stable port, set the current one as stable
        if self._last_stable_forwarded_port is None:
            self._last_stable_forwarded_port = current_port
            return None
        
        # Check if port has changed
        if current_port != self._last_stable_forwarded_port:
            old_port = self._last_stable_forwarded_port
            logger.warning(f"VPN '{self.container_name}' forwarded port changed from {old_port} to {current_port}")
            self._last_stable_forwarded_port = current_port
            return (old_port, current_port)
        
        return None


class GluetunMonitor:
    """Monitors Gluetun VPN container(s) health and manages VPN-dependent operations."""
    
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._health_transition_callbacks = []
        
        # VPN container monitors
        self._vpn_monitors: Dict[str, VpnContainerMonitor] = {}
        
        # Initialize monitors based on configuration
        if cfg.GLUETUN_CONTAINER_NAME:
            self._vpn_monitors[cfg.GLUETUN_CONTAINER_NAME] = VpnContainerMonitor(cfg.GLUETUN_CONTAINER_NAME)
        
        if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
            self._vpn_monitors[cfg.GLUETUN_CONTAINER_NAME_2] = VpnContainerMonitor(cfg.GLUETUN_CONTAINER_NAME_2)
        
    async def start(self):
        """Start the Gluetun monitoring task."""
        if not self._vpn_monitors:
            logger.info("VPN monitoring disabled - no VPN containers configured")
            return
            
        if self._task and not self._task.done():
            return
            
        self._stop.clear()
        self._task = asyncio.create_task(self._monitor_gluetun())
        
        vpn_names = list(self._vpn_monitors.keys())
        if cfg.VPN_MODE == 'redundant':
            logger.info(f"VPN monitor started in REDUNDANT mode for containers: {', '.join(vpn_names)}")
        else:
            logger.info(f"VPN monitor started in SINGLE mode for container: {vpn_names[0]}")
    
    async def stop(self):
        """Stop the Gluetun monitoring task."""
        self._stop.set()
        if self._task:
            await self._task
            
    def add_health_transition_callback(self, callback):
        """Add a callback to be called when Gluetun health status changes."""
        self._health_transition_callbacks.append(callback)

    def get_vpn_monitor(self, container_name: str) -> Optional[VpnContainerMonitor]:
        """Get the monitor for a specific VPN container."""
        return self._vpn_monitors.get(container_name)

    def get_all_vpn_monitors(self) -> Dict[str, VpnContainerMonitor]:
        """Get all VPN monitors."""
        return self._vpn_monitors
    
    async def _monitor_gluetun(self):
        """Main monitoring loop for VPN container health status and port caching."""
        while not self._stop.is_set():
            try:
                now = datetime.now(timezone.utc)
                
                # Check health for each VPN container
                for container_name, monitor in self._vpn_monitors.items():
                    old_health = monitor.is_healthy()
                    current_health = await monitor.check_health()
                    monitor.update_health_status(current_health, now)
                    
                    # Detect health status transitions
                    if old_health is not None and current_health != old_health:
                        await self._handle_health_transition(container_name, old_health, current_health)
                    
                    # Check for forwarded port changes when VPN is healthy
                    if current_health:
                        try:
                            port_change = await monitor.check_port_change()
                            if port_change:
                                old_port, new_port = port_change
                                await self._handle_port_change(container_name, old_port, new_port)
                        except Exception as e:
                            logger.error(f"Error checking port change for '{container_name}': {e}")
                    
                    # Check if VPN needs forced restart
                    if not current_health and monitor.should_force_restart():
                        await monitor.force_restart_container()
                
            except Exception as e:
                logger.error(f"Error monitoring VPN health: {e}")
            
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    
    async def _handle_health_transition(self, container_name: str, old_status: bool, new_status: bool):
        """Handle VPN health status transitions for a specific container."""
        debug_log = get_debug_logger()
        now = datetime.now(timezone.utc)
        monitor = self._vpn_monitors.get(container_name)
        
        if not monitor:
            return
        
        if old_status and not new_status:
            logger.warning(f"VPN '{container_name}' became unhealthy")
            debug_log.log_vpn("transition",
                             status="unhealthy",
                             container_name=container_name,
                             old_status=old_status,
                             new_status=new_status)
            debug_log.log_stress_event("vpn_disconnection",
                                      severity="critical",
                                      container_name=container_name,
                                      description=f"VPN '{container_name}' became unhealthy")
            monitor.invalidate_port_cache()
            
        elif not old_status and new_status:
            logger.info(f"VPN '{container_name}' recovered and is now healthy")
            debug_log.log_vpn("transition",
                             status="healthy",
                             container_name=container_name,
                             old_status=old_status,
                             new_status=new_status)
            monitor.invalidate_port_cache()
            
            # Only restart engines if this is a real reconnection, not initial startup
            should_restart_engines = monitor.should_restart_engines_on_reconnection(now)
            
            if cfg.VPN_RESTART_ENGINES_ON_RECONNECT and should_restart_engines:
                logger.info(f"VPN '{container_name}' reconnected - triggering engine restart")
                debug_log.log_vpn("restart_engines",
                                 status="triggered",
                                 container_name=container_name,
                                 reason="vpn_reconnection")
                await self._restart_engines_for_vpn(container_name)
            elif cfg.VPN_RESTART_ENGINES_ON_RECONNECT and not should_restart_engines:
                logger.info(f"VPN '{container_name}' became healthy but skipping engine restart (grace period)")
                debug_log.log_vpn("restart_engines",
                                 status="skipped",
                                 container_name=container_name,
                                 reason="grace_period_or_instability")
            
            # In redundant mode, trigger provisioning to restore capacity after VPN recovery
            if cfg.VPN_MODE == 'redundant' and should_restart_engines:
                logger.info(f"VPN '{container_name}' recovered - triggering provisioning to restore full capacity")
                await self._provision_engines_after_vpn_recovery(container_name)
        
        # Call registered callbacks
        for callback in self._health_transition_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(container_name, old_status, new_status)
                else:
                    callback(container_name, old_status, new_status)
            except Exception as e:
                logger.error(f"Error in health transition callback: {e}")

    async def _handle_port_change(self, container_name: str, old_port: int, new_port: int):
        """
        Handle VPN forwarded port change by replacing the forwarded engine.
        
        When the VPN restarts internally and the forwarded port changes, the existing
        forwarded engine becomes invalid. This method:
        1. Identifies and stops the old forwarded engine
        2. Removes it from state so it's not exposed via /engines endpoint
        3. Allows the autoscaler to provision a new forwarded engine with the new port
        """
        debug_log = get_debug_logger()
        
        try:
            from .state import state
            from .provisioner import stop_container
            
            logger.warning(f"VPN '{container_name}' port changed from {old_port} to {new_port} - replacing forwarded engine")
            debug_log.log_vpn("port_change_detected",
                             status="replacing_forwarded_engine",
                             container_name=container_name,
                             old_port=old_port,
                             new_port=new_port)
            
            # Find the forwarded engine for this VPN
            forwarded_engine = None
            if cfg.VPN_MODE == 'redundant':
                forwarded_engine = state.get_forwarded_engine_for_vpn(container_name)
            else:
                forwarded_engine = state.get_forwarded_engine()
            
            if not forwarded_engine:
                logger.info(f"No forwarded engine found for VPN '{container_name}' - port change handled gracefully")
                return
            
            logger.info(f"Stopping forwarded engine {forwarded_engine.container_id[:12]} due to port change")
            
            # Remove from state first to hide from /engines endpoint immediately
            state.remove_engine(forwarded_engine.container_id)
            
            # Then stop the container
            try:
                stop_container(forwarded_engine.container_id)
                logger.info(f"Successfully stopped forwarded engine {forwarded_engine.container_id[:12]}")
            except Exception as e:
                logger.error(f"Error stopping forwarded engine {forwarded_engine.container_id[:12]}: {e}")
            
            debug_log.log_vpn("port_change_handled",
                             status="forwarded_engine_replaced",
                             container_name=container_name,
                             old_port=old_port,
                             new_port=new_port,
                             engine_id=forwarded_engine.container_id[:12])
            
            # The autoscaler will automatically provision a new forwarded engine
            # to maintain MIN_REPLICAS, and it will use the new forwarded port
            logger.info(f"Forwarded engine replacement triggered - autoscaler will provision new engine with port {new_port}")
            
        except Exception as e:
            logger.error(f"Error handling port change for VPN '{container_name}': {e}")
            debug_log.log_vpn("port_change_error",
                             status="error",
                             container_name=container_name,
                             old_port=old_port,
                             new_port=new_port,
                             error=str(e))

    async def _restart_engines_for_vpn(self, container_name: str):
        """Restart all engines assigned to a specific VPN container."""
        try:
            from .health import list_managed
            from .provisioner import stop_container
            from .state import state
            
            # Get engines assigned to this VPN
            engines_for_vpn = state.get_engines_by_vpn(container_name)
            
            if not engines_for_vpn:
                logger.info(f"No engines assigned to VPN '{container_name}' to restart")
                return
            
            logger.info(f"Restarting {len(engines_for_vpn)} engines assigned to VPN '{container_name}'")
            
            # Stop all engines for this VPN
            for engine in engines_for_vpn:
                try:
                    logger.info(f"Stopping engine {engine.container_id[:12]} for VPN restart")
                    stop_container(engine.container_id)
                    state.remove_engine(engine.container_id)
                except Exception as e:
                    logger.error(f"Error stopping engine {engine.container_id[:12]}: {e}")
            
            # The autoscaler will automatically start new engines to maintain MIN_REPLICAS
            logger.info("Engine restart completed - autoscaler will provision new engines")
            
        except Exception as e:
            logger.error(f"Error restarting engines for VPN '{container_name}': {e}")

    async def _provision_engines_after_vpn_recovery(self, recovered_vpn: str):
        """
        Provision engines after VPN recovery to restore full capacity.
        
        When a VPN fails, engines on it are removed and we run with reduced capacity.
        When it recovers, this method provisions new engines to restore MIN_REPLICAS.
        """
        try:
            from .state import state
            from .provisioner import start_acestream, AceProvisionRequest
            
            # Count current engines
            all_engines = state.list_engines()
            current_count = len(all_engines)
            target_count = cfg.MIN_REPLICAS
            
            if current_count >= target_count:
                logger.info(f"VPN '{recovered_vpn}' recovered - already at target capacity ({current_count}/{target_count})")
                return
            
            deficit = target_count - current_count
            logger.info(f"VPN '{recovered_vpn}' recovered - provisioning {deficit} engines to restore capacity ({current_count}/{target_count})")
            
            # Provision engines - they will be assigned to recovered VPN via round-robin
            # Note: Empty labels/env is intentional - provisioner will handle VPN assignment
            provisioned = 0
            failed = 0
            for i in range(deficit):
                try:
                    logger.info(f"Provisioning recovery engine {i+1}/{deficit}")
                    req = AceProvisionRequest(labels={}, env={})
                    response = start_acestream(req)
                    logger.info(f"Successfully provisioned recovery engine {response.container_id[:12]}")
                    provisioned += 1
                except Exception as e:
                    logger.error(f"Failed to provision recovery engine {i+1}/{deficit}: {e}")
                    failed += 1
                    # Continue with remaining engines even if one fails
            
            logger.info(f"VPN recovery provisioning complete - successfully provisioned {provisioned}/{deficit} engines (failed: {failed})")
            
        except Exception as e:
            logger.error(f"Error provisioning engines after VPN '{recovered_vpn}' recovery: {e}")

    def is_healthy(self, container_name: Optional[str] = None) -> Optional[bool]:
        """Get the current VPN health status. If container_name is None, returns primary VPN status."""
        if container_name:
            monitor = self._vpn_monitors.get(container_name)
            return monitor.is_healthy() if monitor else None
        
        # If no container specified, return primary VPN status
        if cfg.GLUETUN_CONTAINER_NAME:
            monitor = self._vpn_monitors.get(cfg.GLUETUN_CONTAINER_NAME)
            return monitor.is_healthy() if monitor else None
        return None
    
    async def wait_for_healthy(self, timeout: float = 30.0, container_name: Optional[str] = None) -> bool:
        """Wait for VPN to become healthy, with timeout."""
        if not self._vpn_monitors:
            return True  # No VPN configured
        
        # If specific container requested, wait for it
        if container_name:
            monitor = self._vpn_monitors.get(container_name)
            if not monitor:
                return False
            
            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                if await monitor.check_health():
                    return True
                await asyncio.sleep(1)
            return False
        
        # Otherwise wait for primary VPN
        if cfg.GLUETUN_CONTAINER_NAME:
            monitor = self._vpn_monitors.get(cfg.GLUETUN_CONTAINER_NAME)
            if monitor:
                start_time = asyncio.get_event_loop().time()
                while (asyncio.get_event_loop().time() - start_time) < timeout:
                    if await monitor.check_health():
                        return True
                    await asyncio.sleep(1)
        return False

    async def get_forwarded_port(self, container_name: Optional[str] = None) -> Optional[int]:
        """Get VPN forwarded port. If container_name is None, returns primary VPN port."""
        target_container = container_name or cfg.GLUETUN_CONTAINER_NAME
        if not target_container:
            return None
        
        monitor = self._vpn_monitors.get(target_container)
        if monitor:
            return await monitor.get_forwarded_port()
        return None
    
    def get_cached_forwarded_port(self, container_name: Optional[str] = None) -> Optional[int]:
        """Get cached forwarded port without making API calls."""
        target_container = container_name or cfg.GLUETUN_CONTAINER_NAME
        if not target_container:
            return None
        
        monitor = self._vpn_monitors.get(target_container)
        if monitor:
            return monitor.get_cached_forwarded_port()
        return None
    
    def invalidate_port_cache(self, container_name: Optional[str] = None):
        """Invalidate the port cache."""
        if container_name:
            monitor = self._vpn_monitors.get(container_name)
            if monitor:
                monitor.invalidate_port_cache()
        else:
            # Invalidate all caches
            for monitor in self._vpn_monitors.values():
                monitor.invalidate_port_cache()

def get_forwarded_port_sync(container_name: Optional[str] = None) -> Optional[int]:
    """Synchronous version of get_forwarded_port with caching support."""
    target_container = container_name or cfg.GLUETUN_CONTAINER_NAME
    if not target_container:
        return None
    
    # First try to get from cache if available
    cached_port = gluetun_monitor.get_cached_forwarded_port(target_container)
    if cached_port is not None:
        logger.debug(f"Using cached forwarded port (sync) for '{target_container}': {cached_port}")
        return cached_port
        
    # If no cached port available, make API call
    try:
        with httpx.Client() as client:
            response = client.get(f"http://{target_container}:{cfg.GLUETUN_API_PORT}/v1/openvpn/portforwarded", timeout=10)
            response.raise_for_status()
            data = response.json()
            port = data.get("port")
            if port:
                port = int(port)
                # Update the monitor's cache
                monitor = gluetun_monitor.get_vpn_monitor(target_container)
                if monitor:
                    monitor._cached_port = port
                    monitor._port_cache_time = datetime.now(timezone.utc)
                    if monitor._last_logged_port != port:
                        logger.info(f"Retrieved VPN forwarded port (sync) for '{target_container}': {port}")
                        monitor._last_logged_port = port
                return port
            else:
                logger.warning(f"No port forwarding information available from '{target_container}'")
                return None
    except Exception as e:
        logger.error(f"Failed to get forwarded port from '{target_container}': {e}")
        return None

def _double_check_connectivity_via_engines(container_name: Optional[str] = None) -> str:
    """
    Double-check VPN connectivity by checking if engines can connect to the internet.
    This is used when Gluetun container health appears unhealthy but the issue
    might be unrelated to actual network connectivity.
    
    Uses the engine's /server/api?api_version=3&method=get_network_connection_status
    endpoint which returns {"result": {"connected": true}} when the engine has
    internet connectivity through the VPN.
    
    Args:
        container_name: If provided, only check engines assigned to this VPN container
    
    Returns "healthy" if any running engine reports connected=true, "unhealthy" otherwise.
    """
    try:
        from .state import state
        from .health import check_engine_network_connection
        
        # Get engines to check
        if container_name:
            engines_to_check = state.get_engines_by_vpn(container_name)
        else:
            engines_to_check = state.list_engines()
        
        if not engines_to_check:
            logger.debug(f"VPN double-check: No engines available to verify connectivity for '{container_name or 'any VPN'}'")
            return "unhealthy"
        
        # Check network connectivity on each engine
        connected_engines = 0
        for engine in engines_to_check:
            try:
                if check_engine_network_connection(engine.host, engine.port):
                    connected_engines += 1
                    logger.info(f"VPN double-check: Engine {engine.container_id[:12]} reports internet connectivity")
            except Exception as e:
                logger.debug(f"VPN double-check: Failed to check engine {engine.container_id[:12]}: {e}")
                continue
        
        if connected_engines > 0:
            logger.info(f"VPN double-check: {connected_engines}/{len(engines_to_check)} engine(s) have internet connectivity - considering VPN healthy")
            return "healthy"
        else:
            logger.warning(f"VPN double-check: None of {len(engines_to_check)} engine(s) have internet connectivity")
            return "unhealthy"
            
    except Exception as e:
        logger.error(f"Error during VPN connectivity double-check: {e}")
        return "unhealthy"

def _get_single_vpn_status(container_name: str) -> dict:
    """Get status for a single VPN container."""
    try:
        from .docker_client import get_client
        from docker.errors import NotFound
        
        cli = get_client(timeout=30)
        container = cli.containers.get(container_name)
        container.reload()
        
        # Get container health
        container_running = container.status == "running"
        health_info = container.attrs.get("State", {}).get("Health", {})
        
        if health_info:
            health_status = health_info.get("Status", "unknown")
            if health_status == "unhealthy":
                # Double-check with engine network connectivity if container is unhealthy
                health = _double_check_connectivity_via_engines(container_name) 
            elif health_status == "healthy":
                health = "healthy"
            else:
                health = "starting" if container_running else "unknown"
        else:
            health = "healthy" if container_running else "unhealthy"
        
        # Get forwarded port (try cache first, fallback to API call)
        forwarded_port = None
        if container_running:
            forwarded_port = gluetun_monitor.get_cached_forwarded_port(container_name)
            if forwarded_port is None:
                forwarded_port = get_forwarded_port_sync(container_name)
        
        return {
            "enabled": True,
            "status": container.status,
            "container_name": container_name,
            "container": container_name,
            "health": health,
            "connected": health == "healthy",
            "forwarded_port": forwarded_port,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat()
        }
        
    except NotFound:
        return {
            "enabled": True,
            "status": "not_found",
            "container_name": container_name,
            "container": container_name,
            "health": "unhealthy",
            "connected": False,
            "forwarded_port": None,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting VPN status for '{container_name}': {e}")
        return {
            "enabled": True,
            "status": "error",
            "container_name": container_name,
            "container": container_name,
            "health": "unknown",
            "connected": False,
            "forwarded_port": None,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }


def get_vpn_status() -> dict:
    """Get comprehensive VPN status information."""
    if not cfg.GLUETUN_CONTAINER_NAME:
        return {
            "mode": "disabled",
            "enabled": False,
            "status": "disabled",
            "container_name": None,
            "container": None,
            "health": "unknown",
            "connected": False,
            "forwarded_port": None,
            "last_check": None,
            "last_check_at": None,
            "vpn1": None,
            "vpn2": None
        }
    
    # Get status for VPN1
    vpn1_status = _get_single_vpn_status(cfg.GLUETUN_CONTAINER_NAME)
    
    # In single mode, return status with backwards compatibility
    if cfg.VPN_MODE == 'single':
        result = vpn1_status.copy()
        result["mode"] = "single"
        result["vpn1"] = vpn1_status
        result["vpn2"] = None
        return result
    
    # In redundant mode, get both VPN statuses
    vpn2_status = None
    if cfg.GLUETUN_CONTAINER_NAME_2:
        vpn2_status = _get_single_vpn_status(cfg.GLUETUN_CONTAINER_NAME_2)
    
    # Determine overall health: healthy if at least one VPN is healthy
    any_healthy = vpn1_status["connected"] or (vpn2_status and vpn2_status["connected"])
    overall_health = "healthy" if any_healthy else "unhealthy"
    
    return {
        "mode": "redundant",
        "enabled": True,
        "status": "running" if any_healthy else "unhealthy",
        "container_name": cfg.GLUETUN_CONTAINER_NAME,  # Primary for backwards compatibility
        "container": cfg.GLUETUN_CONTAINER_NAME,  # For frontend compatibility
        "health": overall_health,
        "connected": any_healthy,
        "forwarded_port": vpn1_status.get("forwarded_port"),  # Primary port for backwards compatibility
        "last_check": datetime.now(timezone.utc).isoformat(),
        "last_check_at": datetime.now(timezone.utc).isoformat(),
        "vpn1": vpn1_status,
        "vpn2": vpn2_status
    }

def get_vpn_public_ip() -> Optional[str]:
    """Get the public IP address of the VPN connection from Gluetun."""
    if not cfg.GLUETUN_CONTAINER_NAME:
        return None
    
    try:
        with httpx.Client() as client:
            response = client.get(f"http://{cfg.GLUETUN_CONTAINER_NAME}:{cfg.GLUETUN_API_PORT}/v1/publicip/ip", timeout=10)
            response.raise_for_status()
            data = response.json()
            public_ip = data.get("public_ip")
            if public_ip:
                logger.debug(f"Retrieved VPN public IP: {public_ip}")
                return public_ip
            else:
                logger.warning("No public IP information available from Gluetun")
                return None
    except Exception as e:
        logger.error(f"Failed to get public IP from Gluetun: {e}")
        return None

# Global Gluetun monitor instance
gluetun_monitor = GluetunMonitor()