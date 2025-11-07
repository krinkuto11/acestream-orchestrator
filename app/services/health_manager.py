"""
Health Manager Service

This service ensures service availability by:
1. Continuously monitoring engine health
2. Automatically replacing unhealthy engines while keeping healthy ones running
3. Maintaining minimum healthy engine count at all times
4. Implementing gradual replacement to avoid service interruption
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Set, Dict, Optional
from .state import state
from .health import check_acestream_health, list_managed
from .provisioner import AceProvisionRequest, start_acestream, stop_container
from .autoscaler import ensure_minimum
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
    Manages engine health and ensures service availability by maintaining healthy engines.
    
    Key principles:
    - Always maintain minimum healthy engines
    - Replace unhealthy engines gradually
    - Never interrupt service during replacements
    - Prioritize availability over fixing individual engines
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
            
            # Update state with health status
            state.update_engine_health(engine.container_id, health_status)
            
            if health_status == "healthy":
                engine_health.consecutive_failures = 0
                engine_health.last_healthy_time = datetime.now(timezone.utc)
                engine_health.first_failure_time = None
                healthy_engines.append(engine)
            else:
                engine_health.consecutive_failures += 1
                if engine_health.first_failure_time is None:
                    engine_health.first_failure_time = datetime.now(timezone.utc)
                
                if engine_health.is_considered_unhealthy():
                    unhealthy_engines.append(engine)
                else:
                    # Still in grace period, consider as potentially healthy
                    healthy_engines.append(engine)
        
        logger.debug(f"Health check: {len(healthy_engines)} healthy, {len(unhealthy_engines)} unhealthy engines")
        
        # Ensure we have minimum healthy engines
        await self._ensure_healthy_engines(healthy_engines, unhealthy_engines)
        
        # Replace unhealthy engines if we have enough healthy ones
        await self._replace_unhealthy_engines(healthy_engines, unhealthy_engines)
    
    async def _ensure_healthy_engines(self, healthy_engines: List, unhealthy_engines: List):
        """Ensure we have at least MIN_REPLICAS healthy engines."""
        healthy_count = len(healthy_engines)
        total_needed = cfg.MIN_REPLICAS
        
        if healthy_count < total_needed:
            deficit = total_needed - healthy_count
            
            # In redundant VPN mode, check if unhealthy engines are due to VPN failure
            # If so, don't provision new engines - wait for VPN recovery
            if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
                from .gluetun import gluetun_monitor
                
                vpn1_healthy = gluetun_monitor.is_healthy(cfg.GLUETUN_CONTAINER_NAME)
                vpn2_healthy = gluetun_monitor.is_healthy(cfg.GLUETUN_CONTAINER_NAME_2)
                
                # If one VPN is unhealthy, check if we have engines on it
                if vpn1_healthy and not vpn2_healthy:
                    engines_on_failed_vpn = len(state.get_engines_by_vpn(cfg.GLUETUN_CONTAINER_NAME_2))
                    if engines_on_failed_vpn > 0:
                        logger.info(f"VPN '{cfg.GLUETUN_CONTAINER_NAME_2}' is unhealthy with {engines_on_failed_vpn} engines. "
                                   f"Not provisioning new engines - waiting for VPN recovery. "
                                   f"Running with {healthy_count}/{total_needed} engines.")
                        return
                elif vpn2_healthy and not vpn1_healthy:
                    engines_on_failed_vpn = len(state.get_engines_by_vpn(cfg.GLUETUN_CONTAINER_NAME))
                    if engines_on_failed_vpn > 0:
                        logger.info(f"VPN '{cfg.GLUETUN_CONTAINER_NAME}' is unhealthy with {engines_on_failed_vpn} engines. "
                                   f"Not provisioning new engines - waiting for VPN recovery. "
                                   f"Running with {healthy_count}/{total_needed} engines.")
                        return
            
            logger.warning(f"Only {healthy_count} healthy engines, need {total_needed}. Starting {deficit} new engines.")
            
            # Start new engines to ensure service availability
            await self._start_replacement_engines(deficit)
    
    async def _replace_unhealthy_engines(self, healthy_engines: List, unhealthy_engines: List):
        """Replace unhealthy engines gradually while maintaining service availability."""
        if not unhealthy_engines:
            return
        
        # Check cooldown period to avoid rapid replacements
        now = datetime.now(timezone.utc)
        time_since_last_replacement = (now - self._last_replacement_time).total_seconds()
        if time_since_last_replacement < self._replacement_cooldown_s:
            return
        
        # Only replace engines that should be replaced and have enough healthy engines
        replaceable_engines = [
            engine for engine in unhealthy_engines 
            if self._engine_health[engine.container_id].should_be_replaced()
            and not self._engine_health[engine.container_id].marked_for_replacement
        ]
        
        if not replaceable_engines:
            return
        
        # Ensure we have enough healthy engines to replace unhealthy ones
        healthy_count = len(healthy_engines)
        if healthy_count < cfg.MIN_REPLICAS:
            logger.info(f"Not enough healthy engines ({healthy_count}) to replace unhealthy ones. Ensuring minimum first.")
            return
        
        # Replace one engine at a time to maintain service availability
        engine_to_replace = replaceable_engines[0]
        engine_health = self._engine_health[engine_to_replace.container_id]
        
        if not engine_health.marked_for_replacement:
            logger.info(f"Marking unhealthy engine {engine_to_replace.container_id[:12]} for replacement")
            engine_health.marked_for_replacement = True
            
            # Start replacement process
            await self._replace_engine(engine_to_replace)
    
    async def _start_replacement_engines(self, count: int):
        """Start new engines to replace unhealthy ones or meet minimum requirements."""
        loop = asyncio.get_event_loop()
        
        # Check circuit breaker before attempting replacement
        if not circuit_breaker_manager.can_provision("replacement"):
            logger.warning("Replacement circuit breaker is OPEN - skipping replacement attempt")
            return
        
        success_count = 0
        for i in range(count):
            try:
                logger.info(f"Starting replacement engine {i+1}/{count}")
                
                # Run provisioning in executor to avoid blocking
                response = await loop.run_in_executor(
                    None, 
                    start_acestream, 
                    AceProvisionRequest()
                )
                
                if response and response.container_id:
                    success_count += 1
                    circuit_breaker_manager.record_provisioning_success("replacement")
                    logger.info(f"Successfully started replacement engine {response.container_id[:12]}")
                    # Initialize health tracking for new engine
                    self._engine_health[response.container_id] = EngineHealthStatus(response.container_id)
                else:
                    circuit_breaker_manager.record_provisioning_failure("replacement")
                    logger.error(f"Failed to start replacement engine {i+1}/{count}")
                    
            except Exception as e:
                circuit_breaker_manager.record_provisioning_failure("replacement")
                logger.error(f"Error starting replacement engine {i+1}/{count}: {e}")
                
        # Trigger reindexing to pick up new containers
        if success_count > 0:
            try:
                from .reindex import reindex_existing
                await loop.run_in_executor(None, reindex_existing)
            except Exception as e:
                logger.error(f"Failed to reindex after starting replacement engines: {e}")
        
        # Log circuit breaker status
        if success_count < count:
            breaker_status = circuit_breaker_manager.get_status()
            logger.debug(f"Replacement circuit breaker status: {breaker_status['replacement']['state']}")
    
    async def _replace_engine(self, engine_to_replace):
        """Replace a specific unhealthy engine."""
        engine_health = self._engine_health[engine_to_replace.container_id]
        
        if engine_health.replacement_started:
            return
        
        engine_health.replacement_started = True
        logger.info(f"Starting replacement process for engine {engine_to_replace.container_id[:12]}")
        
        try:
            # First, start a replacement engine
            await self._start_replacement_engines(1)
            
            # Wait a bit for the new engine to become healthy
            await asyncio.sleep(10)
            
            # Check if we now have enough healthy engines to safely remove the unhealthy one
            engines = state.list_engines()
            healthy_count = sum(
                1 for engine in engines 
                if engine.container_id != engine_to_replace.container_id 
                and check_acestream_health(engine.host, engine.port) == "healthy"
            )
            
            if healthy_count >= cfg.MIN_REPLICAS:
                # Safe to remove the unhealthy engine
                logger.info(f"Removing unhealthy engine {engine_to_replace.container_id[:12]}")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, stop_container, engine_to_replace.container_id)
                state.remove_engine(engine_to_replace.container_id)
                
                # Remove from health tracking
                if engine_to_replace.container_id in self._engine_health:
                    del self._engine_health[engine_to_replace.container_id]
                
                self._last_replacement_time = datetime.now(timezone.utc)
                logger.info(f"Successfully replaced unhealthy engine {engine_to_replace.container_id[:12]}")
            else:
                logger.warning(f"Not enough healthy engines to safely remove {engine_to_replace.container_id[:12]}")
                # Reset replacement flags to try again later
                engine_health.marked_for_replacement = False
                engine_health.replacement_started = False
                
        except Exception as e:
            logger.error(f"Error replacing engine {engine_to_replace.container_id[:12]}: {e}")
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