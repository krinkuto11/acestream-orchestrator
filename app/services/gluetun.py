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
from .event_logger import event_logger
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
        
        # Track when VPN was restarted to add grace period before API calls
        self._last_restart_time: Optional[datetime] = None
        self._restart_grace_period_s: int = 15  # Wait 15 seconds after restart before API calls
        
        # Track when VPN recovered to add grace period before cleanup
        self._last_recovery_time: Optional[datetime] = None
        self._recovery_stabilization_period_s: int = 120  # Wait 2 minutes after recovery before cleanup
        
        # Track last logged container status to prevent spam logging
        self._last_logged_status: Optional[str] = None

    async def check_health(self) -> bool:
        """Check if VPN container is healthy."""
        check_start = time.time()
        
        try:
            # Use increased timeout for better resilience during VPN lifecycle events
            cli = get_client(timeout=30)
            container = cli.containers.get(self.container_name)
            container.reload()
            
            # Check container status
            if container.status != "running":
                duration = time.time() - check_start
                # Only log warning if status has changed to avoid spam
                if self._last_logged_status != container.status:
                    logger.warning(f"VPN container '{self.container_name}' is not running (status: {container.status})")
                    self._last_logged_status = container.status
                logger.debug("VPN operation")
                return False
            
            # Check Docker health status if available
            health = container.attrs.get("State", {}).get("Health", {})
            if health:
                health_status = health.get("Status")
                duration = time.time() - check_start
                
                if health_status == "unhealthy":
                    logger.warning(f"VPN container '{self.container_name}' is unhealthy")
                    logger.debug("VPN operation")
                    return False
                elif health_status == "healthy":
                    logger.debug(f"VPN container '{self.container_name}' is healthy")
                    # Reset logged status when container becomes healthy again
                    self._last_logged_status = None
                    logger.debug("VPN operation")
                    return True
                else:
                    # Health status might be "starting" or "none"
                    logger.debug(f"VPN container '{self.container_name}' health status: {health_status}")
                    # Reset logged status when container is running
                    self._last_logged_status = None
                    logger.debug("VPN operation")
                    return True
            else:
                duration = time.time() - check_start
                logger.debug(f"VPN container '{self.container_name}' has no health check, considering healthy")
                # Reset logged status when container is running
                self._last_logged_status = None
                logger.debug("VPN operation")
                return True
                
        except NotFound:
            duration = time.time() - check_start
            logger.error(f"VPN container '{self.container_name}' not found")
            logger.debug("VPN operation")
            return False
        except Exception as e:
            duration = time.time() - check_start
            logger.error(f"Error checking VPN health for '{self.container_name}': {e}")
            logger.debug("VPN operation")
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
                # Log VPN connection event
                event_logger.log_event(
                    event_type="vpn",
                    category="connected",
                    message=f"VPN '{self.container_name}' established connection",
                    details={"container": self.container_name}
                )
            self._consecutive_healthy_count += 1
            # Reset unhealthy tracking when healthy
            if self._unhealthy_since is not None:
                # Log recovery event
                event_logger.log_event(
                    event_type="vpn",
                    category="recovered",
                    message=f"VPN '{self.container_name}' recovered from unhealthy state",
                    details={"container": self.container_name}
                )
            self._unhealthy_since = None
            self._force_restart_attempted = False
        else:
            self._consecutive_healthy_count = 0
            # Track when became unhealthy
            if self._unhealthy_since is None:
                self._unhealthy_since = now
                # Log VPN disconnection event
                event_logger.log_event(
                    event_type="vpn",
                    category="disconnected",
                    message=f"VPN '{self.container_name}' became unhealthy",
                    details={"container": self.container_name}
                )
        
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
            self._last_restart_time = datetime.now(timezone.utc)  # Track restart time
            logger.info(f"VPN container '{self.container_name}' restart initiated")
        except Exception as e:
            logger.error(f"Failed to force restart VPN container '{self.container_name}': {e}")

    def _is_in_restart_grace_period(self) -> bool:
        """Check if we're still in the grace period after a restart."""
        if self._last_restart_time is None:
            return False
        
        time_since_restart = (datetime.now(timezone.utc) - self._last_restart_time).total_seconds()
        return time_since_restart < self._restart_grace_period_s

    def is_in_recovery_stabilization_period(self) -> bool:
        """Check if we're still in the stabilization period after recovery."""
        if self._last_recovery_time is None:
            return False
        
        time_since_recovery = (datetime.now(timezone.utc) - self._last_recovery_time).total_seconds()
        return time_since_recovery < self._recovery_stabilization_period_s
    
    def reset_port_tracking(self):
        """
        Reset port tracking state.
        
        This should be called when entering emergency mode to prevent false
        port change detection after recovery. When a VPN fails and recovers,
        the forwarded port typically changes, and we don't want to treat
        the new port as a "change" from the old pre-failure port.
        """
        self._last_stable_forwarded_port = None
        self._last_port_check_time = None
        logger.debug(f"Port tracking reset for '{self.container_name}'")

    async def get_forwarded_port(self) -> Optional[int]:
        """Get the VPN forwarded port from Gluetun API with caching."""
        # Don't try to get port if we're in restart grace period
        if self._is_in_restart_grace_period():
            logger.debug(f"VPN '{self.container_name}' is in restart grace period, skipping port fetch")
            return None
        
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
                response = await client.get(f"http://{self.container_name}:{cfg.GLUETUN_API_PORT}/v1/portforward", timeout=10)
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
        except httpx.HTTPStatusError as e:
            # 401 error means port forwarding is not supported by this VPN config
            # This is normal and expected for some VPN providers - don't log as error
            if e.response.status_code == 401:
                logger.info(f"Port forwarding not supported by VPN config for '{self.container_name}' (401 Unauthorized)")
                return None
            logger.error(f"Failed to get forwarded port from '{self.container_name}': {e}")
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
        - Not in recovery stabilization period (to avoid false detection after VPN recovery)
        
        Returns:
            Optional[tuple[int, int]]: (old_port, new_port) if port changed, None otherwise
        """
        # Only check for port changes if VPN is healthy
        if not self._last_health_status:
            return None
        
        # Don't check for port changes during recovery stabilization period
        # The port is expected to be different after recovery
        if self.is_in_recovery_stabilization_period():
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
        """
        Handle VPN health status transitions for a specific container.
        """
        now = datetime.now(timezone.utc)
        monitor = self._vpn_monitors.get(container_name)
        
        if not monitor:
            return
        
        if old_status and not new_status:
            logger.warning(f"VPN '{container_name}' became unhealthy")
            logger.debug("VPN operation")
            logger.warning("VPN stress event")
            monitor.invalidate_port_cache()
            
            # In redundant mode, enter emergency mode if one VPN fails
            if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
                await self._handle_vpn_failure(container_name)
            
        elif not old_status and new_status:
            logger.info(f"VPN '{container_name}' recovered and is now healthy")
            logger.debug("VPN operation")
            monitor.invalidate_port_cache()
            
            # Mark recovery time to prevent premature cleanup
            monitor._last_recovery_time = now
            
            # In redundant mode, handle VPN recovery (may exit emergency mode)
            if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
                await self._handle_vpn_recovery(container_name)
            
            # Only restart engines if this is a real reconnection, not initial startup
            should_restart_engines = monitor.should_restart_engines_on_reconnection(now)
            
            # In single VPN mode, restart engines on reconnection
            if cfg.VPN_MODE == 'single' and cfg.VPN_RESTART_ENGINES_ON_RECONNECT and should_restart_engines:
                logger.info(f"VPN '{container_name}' reconnected - triggering engine restart")
                logger.debug("VPN operation")
                await self._restart_engines_for_vpn(container_name)
            elif cfg.VPN_MODE == 'single' and cfg.VPN_RESTART_ENGINES_ON_RECONNECT and not should_restart_engines:
                logger.info(f"VPN '{container_name}' became healthy but skipping engine restart (grace period)")
                logger.debug("VPN operation")
        
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
        3. Sets recovery stabilization period to prevent premature cleanup
        4. Allows the autoscaler to provision a new forwarded engine with the new port
        """
        now = datetime.now(timezone.utc)
        
        try:
            from .state import state
            from .provisioner import stop_container
            
            logger.warning(f"VPN '{container_name}' port changed from {old_port} to {new_port} - replacing forwarded engine")
            logger.debug("VPN operation")
            
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
            
            # Set recovery stabilization period to prevent premature cleanup during recovery
            # This prevents the monitor from cleaning up engines that may be temporarily
            # unhealthy during the port change and subsequent reprovisioning
            monitor = self._vpn_monitors.get(container_name)
            if monitor:
                monitor._last_recovery_time = now
                logger.info(f"Recovery stabilization period set for VPN '{container_name}' after port change "
                           f"({monitor._recovery_stabilization_period_s}s)")
            
            logger.debug("VPN operation")
            
            # The autoscaler will automatically provision a new forwarded engine
            # to maintain MIN_REPLICAS, and it will use the new forwarded port
            logger.info(f"Forwarded engine replacement triggered - autoscaler will provision new engine with port {new_port}")
            
        except Exception as e:
            logger.error(f"Error handling port change for VPN '{container_name}': {e}")
            logger.debug("VPN operation")

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
    
    async def _handle_vpn_failure(self, failed_vpn: str):
        """
        Handle VPN failure in redundant mode by entering emergency mode.
        
        Emergency mode immediately:
        - Takes over all operations for the failed VPN's engines
        - Deletes engines from the failed VPN
        - Only manages the healthy VPN's engines
        """
        try:
            from .state import state
            
            # Determine which VPN is still healthy
            vpn1_healthy = self.is_healthy(cfg.GLUETUN_CONTAINER_NAME)
            vpn2_healthy = self.is_healthy(cfg.GLUETUN_CONTAINER_NAME_2)
            
            # Don't enter emergency mode if both VPNs are unhealthy or both are healthy
            if vpn1_healthy == vpn2_healthy:
                logger.debug(f"Not entering emergency mode: both VPNs have same health status")
                return
            
            # Determine healthy VPN
            if failed_vpn == cfg.GLUETUN_CONTAINER_NAME:
                healthy_vpn = cfg.GLUETUN_CONTAINER_NAME_2
            else:
                healthy_vpn = cfg.GLUETUN_CONTAINER_NAME
            
            # Verify the healthy VPN is actually healthy
            if not self.is_healthy(healthy_vpn):
                logger.warning(f"Cannot enter emergency mode: healthy VPN '{healthy_vpn}' is not actually healthy")
                return
            
            # Enter emergency mode
            entered = state.enter_emergency_mode(failed_vpn, healthy_vpn)
            
            if entered:
                # Reset port tracking for the failed VPN to prevent false port change detection after recovery
                failed_vpn_monitor = self._vpn_monitors.get(failed_vpn)
                if failed_vpn_monitor:
                    failed_vpn_monitor.reset_port_tracking()
                
                logger.info(f"Emergency mode activated - system operating on single VPN '{healthy_vpn}'")
                
        except Exception as e:
            logger.error(f"Error handling VPN failure for '{failed_vpn}': {e}")
    
    async def _handle_vpn_recovery(self, recovered_vpn: str):
        """
        Handle VPN recovery in redundant mode.
        
        If in emergency mode, exits emergency mode and provisions engines to restore capacity.
        """
        try:
            from .state import state
            
            # Check if we're in emergency mode
            if not state.is_emergency_mode():
                logger.debug(f"VPN '{recovered_vpn}' recovered but not in emergency mode")
                return
            
            emergency_info = state.get_emergency_mode_info()
            
            # Only exit emergency mode if the recovered VPN is the one that failed
            if recovered_vpn != emergency_info.get("failed_vpn"):
                logger.debug(f"VPN '{recovered_vpn}' recovered but it's not the failed VPN in emergency mode")
                return
            
            # Exit emergency mode
            exited = state.exit_emergency_mode()
            
            if exited:
                logger.info(f"Emergency mode deactivated - restoring full capacity")
                
                # Wait for VPN to stabilize and get forwarded port before provisioning
                # This is important because the first engine provisioned should be the forwarded engine
                await asyncio.sleep(5)
                
                # Wait for forwarded port to become available (max 30 seconds)
                monitor = self._vpn_monitors.get(recovered_vpn)
                if monitor:
                    logger.info(f"Waiting for VPN '{recovered_vpn}' to establish port forwarding...")
                    port_wait_start = asyncio.get_event_loop().time()
                    port_wait_timeout = 30
                    forwarded_port = None
                    
                    while (asyncio.get_event_loop().time() - port_wait_start) < port_wait_timeout:
                        forwarded_port = await monitor.get_forwarded_port()
                        if forwarded_port:
                            logger.info(f"VPN '{recovered_vpn}' forwarded port {forwarded_port} is now available")
                            break
                        await asyncio.sleep(2)
                    
                    if not forwarded_port:
                        logger.warning(f"VPN '{recovered_vpn}' forwarded port not available after {port_wait_timeout}s, provisioning without it")
                
                # Provision engines to restore full capacity
                await self._provision_engines_after_vpn_recovery(recovered_vpn)
                
        except Exception as e:
            logger.error(f"Error handling VPN recovery for '{recovered_vpn}': {e}")

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
    
    # Check if we're in restart grace period
    monitor = gluetun_monitor.get_vpn_monitor(target_container)
    if monitor and monitor._is_in_restart_grace_period():
        logger.debug(f"VPN '{target_container}' is in restart grace period, skipping port fetch (sync)")
        return None
    
    # First try to get from cache if available
    cached_port = gluetun_monitor.get_cached_forwarded_port(target_container)
    if cached_port is not None:
        logger.debug(f"Using cached forwarded port (sync) for '{target_container}': {cached_port}")
        return cached_port
        
    # If no cached port available, make API call
    try:
        with httpx.Client() as client:
            response = client.get(f"http://{target_container}:{cfg.GLUETUN_API_PORT}/v1/portforward", timeout=10)
            response.raise_for_status()
            data = response.json()
            port = data.get("port")
            if port:
                port = int(port)
                # Update the monitor's cache
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
    except httpx.HTTPStatusError as e:
        # 401 error means port forwarding is not supported by this VPN config
        # This is normal and expected for some VPN providers - don't log as error
        if e.response.status_code == 401:
            logger.info(f"Port forwarding not supported by VPN config for '{target_container}' (401 Unauthorized)")
            return None
        logger.error(f"Failed to get forwarded port from '{target_container}': {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to get forwarded port from '{target_container}': {e}")
        return None

# Track when we last did a connectivity double-check per VPN to avoid spamming
_last_double_check_time: Dict[str, datetime] = {}
_double_check_interval_s = 30  # Only double-check every 30 seconds

def _double_check_connectivity_via_engines(container_name: Optional[str] = None) -> str:
    """
    Double-check VPN connectivity by checking if engines can connect to the internet.
    This is used when Gluetun container health appears unhealthy but the issue
    might be unrelated to actual network connectivity.
    
    This function is throttled to avoid excessive checks - it only runs once per
    _double_check_interval_s seconds per VPN to prevent log spam.
    
    Uses the engine's /server/api?api_version=3&method=get_network_connection_status
    endpoint which returns {"result": {"connected": true}} when the engine has
    internet connectivity through the VPN.
    
    Args:
        container_name: If provided, only check engines assigned to this VPN container
    
    Returns "healthy" if any running engine reports connected=true, "unhealthy" otherwise.
    """
    try:
        # Throttle double-checks to avoid spamming logs and API calls
        now = datetime.now(timezone.utc)
        check_key = container_name or "default"
        
        if check_key in _last_double_check_time:
            time_since_last_check = (now - _last_double_check_time[check_key]).total_seconds()
            if time_since_last_check < _double_check_interval_s:
                # Too soon since last check, return unhealthy to maintain cautious behavior
                return "unhealthy"
        
        _last_double_check_time[check_key] = now
        
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
        
        # Get provider from docker config
        provider = None
        if container_running:
            provider = get_vpn_provider(container_name)
        
        # Get public IP info (includes location data from Gluetun API)
        public_ip = None
        country = None
        city = None
        region = None
        if container_running and health == "healthy":
            ip_info = get_vpn_public_ip_info(container_name)
            if ip_info:
                public_ip = ip_info.get("public_ip")
                country = ip_info.get("country")
                city = ip_info.get("city")
                region = ip_info.get("region")
        
        result = {
            "enabled": True,
            "status": container.status,
            "container_name": container_name,
            "container": container_name,
            "health": health,
            "connected": health == "healthy",
            "forwarded_port": forwarded_port,
            "public_ip": public_ip,
            "provider": provider,
            "country": country,
            "city": city,
            "region": region,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat()
        }
        
        return result
        
    except NotFound:
        return {
            "enabled": True,
            "status": "not_found",
            "container_name": container_name,
            "container": container_name,
            "health": "unhealthy",
            "connected": False,
            "forwarded_port": None,
            "public_ip": None,
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
            "public_ip": None,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_check_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }


def get_vpn_status() -> dict:
    """Get comprehensive VPN status information."""
    from .state import state
    
    # Get emergency mode info
    emergency_info = state.get_emergency_mode_info()
    
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
            "vpn2": None,
            "emergency_mode": emergency_info
        }
    
    # Get status for VPN1
    vpn1_status = _get_single_vpn_status(cfg.GLUETUN_CONTAINER_NAME)
    
    # In single mode, return status with backwards compatibility
    if cfg.VPN_MODE == 'single':
        result = vpn1_status.copy()
        result["mode"] = "single"
        result["vpn1"] = vpn1_status
        result["vpn2"] = None
        result["emergency_mode"] = emergency_info
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
        "vpn2": vpn2_status,
        "emergency_mode": emergency_info
    }

def normalize_provider_name(provider: str) -> str:
    """
    Normalize VPN provider name from docker env variable to proper capitalization.
    
    The VPN_SERVICE_PROVIDER env variable is lowercase, but we need to match
    it against a list of properly capitalized provider names.
    
    Args:
        provider: Provider name from docker env (e.g., "protonvpn", "nordvpn")
    
    Returns:
        Properly capitalized provider name (e.g., "ProtonVPN", "NordVPN")
    """
    # Provider name mapping from lowercase to proper capitalization
    provider_map = {
        "airvpn": "AirVPN",
        "cyberghost": "Cyberghost",
        "expressvpn": "ExpressVPN",
        "fastestvpn": "FastestVPN",
        "giganews": "Giganews",
        "hidemyass": "HideMyAss",
        "ipvanish": "IPVanish",
        "ivpn": "IVPN",
        "mullvad": "Mullvad",
        "nordvpn": "NordVPN",
        "perfect privacy": "Perfect Privacy",
        "perfectprivacy": "Perfect Privacy",
        "privado": "Privado",
        "private internet access": "Private Internet Access",
        "pia": "Private Internet Access",
        "privatevpn": "PrivateVPN",
        "protonvpn": "ProtonVPN",
        "purevpn": "PureVPN",
        "slickvpn": "SlickVPN",
        "surfshark": "Surfshark",
        "torguard": "TorGuard",
        "vpnsecure.me": "VPNSecure.me",
        "vpnsecure": "VPNSecure.me",
        "vpnunlimited": "VPNUnlimited",
        "vyprvpn": "Vyprvpn",
        "wevpn": "WeVPN",
        "windscribe": "Windscribe",
    }
    
    # Normalize input to lowercase for lookup
    provider_lower = provider.lower().strip()
    
    # Return mapped name or title case as fallback
    return provider_map.get(provider_lower, provider.title())


def get_vpn_provider(container_name: Optional[str] = None) -> Optional[str]:
    """
    Get VPN provider from container's VPN_SERVICE_PROVIDER environment variable.
    
    Args:
        container_name: Specific VPN container name, or None for primary VPN
    
    Returns:
        Normalized provider name (e.g., "ProtonVPN", "NordVPN") or None if not found
    """
    target_container = container_name or cfg.GLUETUN_CONTAINER_NAME
    if not target_container:
        return None
    
    try:
        from .docker_client import get_client
        from docker.errors import NotFound
        
        cli = get_client(timeout=30)
        container = cli.containers.get(target_container)
        container.reload()
        
        # Get VPN_SERVICE_PROVIDER from container environment
        env_vars = container.attrs.get("Config", {}).get("Env", [])
        for env_var in env_vars:
            if env_var.startswith("VPN_SERVICE_PROVIDER="):
                provider = env_var.split("=", 1)[1]
                normalized = normalize_provider_name(provider)
                logger.debug(f"Retrieved VPN provider for '{target_container}': {provider} -> {normalized}")
                return normalized
        
        logger.warning(f"VPN_SERVICE_PROVIDER not found in container '{target_container}' environment")
        return None
        
    except NotFound:
        logger.error(f"VPN container '{target_container}' not found")
        return None
    except Exception as e:
        logger.error(f"Failed to get VPN provider from '{target_container}': {e}")
        return None


def get_vpn_public_ip_info(container_name: Optional[str] = None) -> Optional[Dict[str, str]]:
    """
    Get comprehensive public IP information from Gluetun's /v1/publicip/ip endpoint.
    
    The new API response format includes:
    {
        "public_ip": "217.138.216.131",
        "region": "Land Berlin",
        "country": "Germany",
        "city": "Berlin",
        "location": "52.519600,13.406900",
        "organization": "M247 Europe SRL",
        "postal_code": "10178",
        "timezone": "Europe/Berlin"
    }
    
    Args:
        container_name: Specific VPN container name, or None for primary VPN
    
    Returns:
        Dict with public IP and location information, or None if unavailable
    """
    target_container = container_name or cfg.GLUETUN_CONTAINER_NAME
    if not target_container:
        return None
    
    # Don't try to get public IP if VPN is unhealthy or in restart grace period
    monitor = gluetun_monitor.get_vpn_monitor(target_container)
    if monitor:
        if monitor.is_healthy() is False:
            return None
        if monitor._is_in_restart_grace_period():
            logger.debug(f"VPN '{target_container}' is in restart grace period, skipping public IP fetch")
            return None
    
    try:
        with httpx.Client() as client:
            response = client.get(f"http://{target_container}:{cfg.GLUETUN_API_PORT}/v1/publicip/ip", timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("public_ip"):
                logger.debug(f"Retrieved VPN public IP info for '{target_container}': {data.get('public_ip')}, {data.get('country')}, {data.get('city')}")
                return data
            else:
                logger.warning(f"No public IP information available from '{target_container}'")
                return None
    except Exception as e:
        logger.error(f"Failed to get public IP info from '{target_container}': {e}")
        return None


def get_vpn_public_ip(container_name: Optional[str] = None) -> Optional[str]:
    """
    Get the public IP address of the VPN connection from Gluetun.
    
    Args:
        container_name: Specific VPN container name, or None for primary VPN
    
    Returns:
        Public IP address as string, or None if unavailable
    
    This function should only be called when the VPN is healthy to avoid
    excessive error logging when VPN is down.
    """
    info = get_vpn_public_ip_info(container_name)
    return info.get("public_ip") if info else None

# Global Gluetun monitor instance
gluetun_monitor = GluetunMonitor()