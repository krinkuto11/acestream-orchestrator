"""
Health Manager Service

This service ensures service health by:
1. Continuously monitoring engine health
2. Evicting fatally unhealthy engines
3. Preserving grace-period and threshold protections against premature eviction
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from .state import state
from .health import check_acestream_health
from .provisioner import stop_container
from .circuit_breaker import circuit_breaker_manager
from ..core.config import cfg

logger = logging.getLogger(__name__)

class EngineHealthStatus:
    """Track detailed health status for engines"""
    def __init__(self, container_id: str):
        self.container_id = container_id
        self.consecutive_failures = 0
        self.last_healthy_time: Optional[datetime] = None
        self.first_failure_time: Optional[datetime] = None
        self.marked_for_replacement = False
        self.replacement_started = False
        
    def is_considered_unhealthy(self) -> bool:
        """Engine is considered unhealthy after configured consecutive failures"""
        return self.consecutive_failures >= cfg.HEALTH_FAILURE_THRESHOLD
    
    def should_be_replaced(self) -> bool:
        """Engine should be replaced if unhealthy for more than grace period"""
        if not self.is_considered_unhealthy() or not self.first_failure_time:
            return False
        
        unhealthy_duration = (datetime.now(timezone.utc) - self.first_failure_time).total_seconds()
        return unhealthy_duration > cfg.HEALTH_UNHEALTHY_GRACE_PERIOD_S

class HealthManager:
    """
    Manages engine health as probe-and-evict control loop.
    
    Key principles:
    - Track health transitions and enforce grace periods
    - Evict only engines that are durably unhealthy
    - Do not perform provisioning; reconciliation controller handles replacement
    """
    
    def __init__(self, check_interval: int = None):
        self.check_interval = check_interval or cfg.HEALTH_CHECK_INTERVAL_S
        self._running = False
        self._task = None
        self._engine_health: Dict[str, EngineHealthStatus] = {}
        self._last_replacement_time = datetime.now(timezone.utc)
        self._replacement_cooldown_s = cfg.HEALTH_REPLACEMENT_COOLDOWN_S
        
    async def start(self):
        """Start the health management service."""
        if self._running:
            return
            
        self._running = True
        self._task = asyncio.create_task(self._health_management_loop())
        logger.info(f"Health manager started with {self.check_interval}s interval")
    
    async def stop(self):
        """Stop the health management service."""
        if not self._running:
            return
            
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health manager stopped")
    
    async def _health_management_loop(self):
        """Main health management loop."""
        while self._running:
            try:
                await self._check_and_manage_health()
            except Exception as e:
                logger.error(f"Error in health management loop: {e}")
            
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
    
    async def _check_and_manage_health(self):
        """Check health of all engines and take corrective actions."""
        # Skip health management if manual mode is enabled
        from .settings_persistence import SettingsPersistence
        engine_settings = SettingsPersistence.load_engine_settings() or {}
        is_manual_mode = engine_settings.get('manual_mode', False)
        
        # Get current engines from state
        engines = state.list_engines()
        current_engine_ids = {engine.container_id for engine in engines}
        
        # Clean up health tracking for removed engines
        removed_engines = set(self._engine_health.keys()) - current_engine_ids
        for engine_id in removed_engines:
            del self._engine_health[engine_id]
        
        # Initialize health tracking for new engines
        for engine in engines:
            if engine.container_id not in self._engine_health:
                self._engine_health[engine.container_id] = EngineHealthStatus(engine.container_id)
        
        # Check health of all engines
        healthy_engines = []
        unhealthy_engines = []
        
        for engine in engines:
            health_status = check_acestream_health(engine.host, engine.port)
            engine_health = self._engine_health[engine.container_id]

            if health_status == "healthy":
                engine_health.consecutive_failures = 0
                engine_health.last_healthy_time = datetime.now(timezone.utc)
                engine_health.first_failure_time = None
                state.update_engine_health(engine.container_id, "healthy")
                healthy_engines.append(engine)
            else:
                engine_health.consecutive_failures += 1
                if engine_health.first_failure_time is None:
                    engine_health.first_failure_time = datetime.now(timezone.utc)

                is_unhealthy = engine_health.is_considered_unhealthy()
                if is_unhealthy:
                    state.update_engine_health(engine.container_id, "unhealthy")

                # In manual mode, we track health but do NOT mark engines as unhealthy
                # for replacement logic (because we cannot replace them automatically).
                if is_manual_mode:
                    # Still consider them "healthy" for the sake of the lists so they don't trigger replacement
                    healthy_engines.append(engine)
                elif is_unhealthy:
                    unhealthy_engines.append(engine)
                else:
                    # Transient probe failure: keep prior reported status stable.
                    healthy_engines.append(engine)
        
        logger.debug(f"Health check: {len(healthy_engines)} healthy, {len(unhealthy_engines)} unhealthy engines")
        
        # Check if we should wait for VPN recovery before taking any actions
        if self._should_wait_for_vpn_recovery(healthy_engines):
            return
        
        # Evictions are disabled in manual mode.
        if not is_manual_mode:
            # Evict unhealthy engines; provisioning is handled by EngineController.
            await self._replace_unhealthy_engines(healthy_engines, unhealthy_engines)
    
    def _get_target_vpn_for_provisioning(self) -> Optional[str]:
        """
        Determine which VPN would receive a new engine if provisioned now.
        
        This replicates the VPN selection logic from provisioner.start_acestream()
        to allow per-VPN stabilization checks.
        
        Returns:
            VPN container name that would receive the next engine, or None if not using VPN
        """
        if not cfg.GLUETUN_CONTAINER_NAME:
            return None
        
        # Single VPN mode
        if cfg.VPN_MODE != 'redundant' or not cfg.GLUETUN_CONTAINER_NAME_2:
            return cfg.GLUETUN_CONTAINER_NAME
        
        # Redundant mode - replicate provisioner logic
        from .gluetun import gluetun_monitor
        vpn_nodes = {node.get("container_name"): node for node in state.list_vpn_nodes()}
        
        # Normal redundant mode: count engines per VPN
        vpn1_engines = len(state.get_engines_by_vpn(cfg.GLUETUN_CONTAINER_NAME))
        vpn2_engines = len(state.get_engines_by_vpn(cfg.GLUETUN_CONTAINER_NAME_2))
        
        # Check readiness from informer state first, fallback to monitor health.
        vpn1_node = vpn_nodes.get(cfg.GLUETUN_CONTAINER_NAME)
        vpn2_node = vpn_nodes.get(cfg.GLUETUN_CONTAINER_NAME_2)
        vpn1_healthy = bool(vpn1_node.get("healthy")) if vpn1_node is not None else bool(gluetun_monitor.is_healthy(cfg.GLUETUN_CONTAINER_NAME))
        vpn2_healthy = bool(vpn2_node.get("healthy")) if vpn2_node is not None else bool(gluetun_monitor.is_healthy(cfg.GLUETUN_CONTAINER_NAME_2))
        
        # Determine VPN assignment based on health and load
        if vpn1_healthy and vpn2_healthy:
            # Both healthy: use round-robin to balance load
            return cfg.GLUETUN_CONTAINER_NAME if vpn1_engines <= vpn2_engines else cfg.GLUETUN_CONTAINER_NAME_2
        elif vpn1_healthy and not vpn2_healthy:
            # Only VPN1 healthy: use it
            return cfg.GLUETUN_CONTAINER_NAME
        elif vpn2_healthy and not vpn1_healthy:
            # Only VPN2 healthy: use it
            return cfg.GLUETUN_CONTAINER_NAME_2
        else:
            # Both unhealthy: cannot provision
            return None
    
    def _should_wait_for_vpn_recovery(self, healthy_engines: List) -> bool:
        """
        Check if we should wait for VPN recovery instead of taking action.
        
        Returns True if:
        - In redundant VPN mode
        - The VPN that would receive new engines is in recovery stabilization period
        
        Per-VPN stabilization blocks only the specific target VPN when it is stabilizing,
        while still allowing scheduling on other Ready VPN nodes.
        """
        if cfg.VPN_MODE != 'redundant' or not cfg.GLUETUN_CONTAINER_NAME_2:
            return False
        
        from .gluetun import gluetun_monitor
        
        # Determine which VPN would receive a new engine
        target_vpn = self._get_target_vpn_for_provisioning()
        
        # Check if the target VPN is in recovery stabilization period
        # This is per-VPN: only blocks if the specific VPN we'd provision to is stabilizing
        if target_vpn:
            vpn_monitor = gluetun_monitor.get_vpn_monitor(target_vpn)
            if vpn_monitor and vpn_monitor.is_in_recovery_stabilization_period():
                healthy_count = len(healthy_engines)
                logger.info(f"Target VPN '{target_vpn}' is in recovery stabilization period. "
                           f"Not taking action - waiting for port forwarding to stabilize. "
                           f"Running with {healthy_count}/{cfg.MIN_REPLICAS} engines.")
                return True

        return False
    
    async def _replace_unhealthy_engines(self, healthy_engines: List, unhealthy_engines: List):
        """Evict unhealthy engines gradually to avoid mass churn."""
        if not unhealthy_engines:
            return
        
        # Check cooldown period to avoid rapid replacements
        now = datetime.now(timezone.utc)
        time_since_last_replacement = (now - self._last_replacement_time).total_seconds()
        if time_since_last_replacement < self._replacement_cooldown_s:
            return
        
        # Evict only engines that crossed threshold and grace-period protections.
        replaceable_engines = [
            engine for engine in unhealthy_engines 
            if self._engine_health[engine.container_id].should_be_replaced()
            and not self._engine_health[engine.container_id].marked_for_replacement
        ]
        
        if not replaceable_engines:
            return

        # Evict one engine at a time to avoid burst evictions.
        engine_to_replace = replaceable_engines[0]
        engine_health = self._engine_health[engine_to_replace.container_id]
        
        if not engine_health.marked_for_replacement:
            logger.info(f"Marking unhealthy engine {engine_to_replace.container_id[:12]} for replacement")
            engine_health.marked_for_replacement = True
            
            # Start replacement process
            await self._replace_engine(engine_to_replace)
    
    async def _replace_engine(self, engine_to_replace):
        """Evict a specific unhealthy engine immediately."""
        engine_health = self._engine_health[engine_to_replace.container_id]
        
        if engine_health.replacement_started:
            return
        
        engine_health.replacement_started = True
        logger.info(f"Evicting unhealthy engine {engine_to_replace.container_id[:12]}")
        
        try:
            await asyncio.to_thread(stop_container, engine_to_replace.container_id)
            state.remove_engine(engine_to_replace.container_id)

            if engine_to_replace.container_id in self._engine_health:
                del self._engine_health[engine_to_replace.container_id]

            self._last_replacement_time = datetime.now(timezone.utc)
            logger.info(f"Successfully evicted unhealthy engine {engine_to_replace.container_id[:12]}")
                
        except Exception as e:
            logger.error(f"Error evicting engine {engine_to_replace.container_id[:12]}: {e}")
            # Reset replacement flags to try again later
            engine_health.marked_for_replacement = False
            engine_health.replacement_started = False
    
    def get_health_summary(self) -> Dict:
        """Get a summary of engine health status."""
        engines = state.list_engines()
        healthy_count = 0
        unhealthy_count = 0
        marked_for_replacement = 0
        
        for engine in engines:
            if engine.container_id in self._engine_health:
                engine_health = self._engine_health[engine.container_id]
                if engine_health.is_considered_unhealthy():
                    unhealthy_count += 1
                    if engine_health.marked_for_replacement:
                        marked_for_replacement += 1
                else:
                    healthy_count += 1
        
        return {
            "total_engines": len(engines),
            "healthy_engines": healthy_count,
            "unhealthy_engines": unhealthy_count,
            "marked_for_replacement": marked_for_replacement,
            "minimum_required": cfg.MIN_REPLICAS,
            "health_check_interval": self.check_interval,
            "circuit_breakers": circuit_breaker_manager.get_status()
        }

# Global health manager instance
health_manager = HealthManager()