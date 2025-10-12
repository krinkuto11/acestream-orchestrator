import os
import time
from ..core.config import cfg
from .provisioner import StartRequest, start_container, AceProvisionRequest, start_acestream
from .health import list_managed
from .state import state
from .circuit_breaker import circuit_breaker_manager
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Track when engines became empty for grace period implementation
_empty_engine_timestamps = {}

def _count_healthy_engines() -> int:
    """Count engines that are currently healthy."""
    try:
        from .health import check_acestream_health
        engines = state.list_engines()
        healthy_count = 0
        
        for engine in engines:
            if check_acestream_health(engine.host, engine.port) == "healthy":
                healthy_count += 1
        
        return healthy_count
    except Exception as e:
        logger.debug(f"Error counting healthy engines: {e}")
        # Fallback to total engine count if health check fails
        return len(state.list_engines())

def ensure_minimum():
    """Ensure minimum number of free/empty replicas are available with resilient synchronous provisioning."""
    try:
        from .replica_validator import replica_validator
        
        # Check circuit breaker before attempting provisioning
        if not circuit_breaker_manager.can_provision("general"):
            logger.warning("Circuit breaker is OPEN - skipping provisioning attempt")
            return
        
        # Use replica_validator to get accurate counts including free engines
        total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
        
        # Calculate deficit based on free engines (not total engines)
        # MIN_REPLICAS now represents minimum FREE replicas, not total replicas
        deficit = cfg.MIN_REPLICAS - free_count
        
        if deficit <= 0:
            logger.debug(f"Sufficient free engines available (free: {free_count}, min required: {cfg.MIN_REPLICAS}, total: {total_running}, used: {used_engines})")
            return  # Already have enough free engines
        
        logger.info(f"Starting {deficit} AceStream containers to maintain MIN_REPLICAS={cfg.MIN_REPLICAS} free engines (currently: total={total_running}, used={used_engines}, free={free_count})")
        
        if deficit > 0:
            # Use simple synchronous provisioning for reliability
            success_count = 0
            failure_count = 0
            
            for i in range(deficit):
                try:
                    logger.debug(f"Attempting to start container {i+1}/{deficit}")
                    response = start_acestream(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                    
                    if response and response.container_id:
                        success_count += 1
                        circuit_breaker_manager.record_provisioning_success("general")
                        logger.info(f"Successfully started AceStream container {response.container_id[:12]} ({success_count}/{deficit}) - HTTP port: {response.host_http_port}")
                        
                        # Immediately verify the container is running
                        time.sleep(1)  # Brief pause to let container start
                        
                        # Get updated Docker status to verify the container is actually running
                        updated_status = replica_validator.get_docker_container_status()
                        if updated_status['total_running'] > total_running + success_count - 1:
                            logger.debug(f"Container {response.container_id[:12]} verified as running")
                        else:
                            logger.warning(f"Container {response.container_id[:12]} may not be running yet")
                    else:
                        failure_count += 1
                        circuit_breaker_manager.record_provisioning_failure("general")
                        logger.error(f"Failed to start AceStream container {i+1}/{deficit}: No response or container ID")
                        
                except Exception as e:
                    failure_count += 1
                    circuit_breaker_manager.record_provisioning_failure("general")
                    logger.error(f"Failed to start AceStream container {i+1}/{deficit}: {e}")
                    # Continue with next container instead of failing completely
                    continue
            
            if success_count > 0:
                logger.info(f"Successfully started {success_count}/{deficit} containers")
                
                # Reindex after provisioning to ensure state consistency
                logger.info("Reindexing after provisioning to pick up new containers")
                try:
                    from .reindex import reindex_existing
                    reindex_existing()
                except Exception as e:
                    logger.error(f"Failed to reindex after provisioning: {e}")
            else:
                logger.error(f"Failed to start any containers out of {deficit} needed")
                
            # Log circuit breaker status if there were failures
            if failure_count > 0:
                breaker_status = circuit_breaker_manager.get_status()
                logger.debug(f"Circuit breaker status after {failure_count} failures: {breaker_status['general']['state']}")
                
    except Exception as e:
        logger.error(f"Error in ensure_minimum: {e}")
        import traceback
        logger.debug(f"Traceback: {traceback.format_exc()}")

def can_stop_engine(container_id: str, bypass_grace_period: bool = False) -> bool:
    """Check if an engine can be safely stopped based on grace period and minimum free replicas."""
    now = datetime.now()
    
    # Check if engine has any active streams
    active_streams = state.list_streams(status="started", container_id=container_id)
    if active_streams:
        # Engine has active streams, remove from empty tracking
        if container_id in _empty_engine_timestamps:
            del _empty_engine_timestamps[container_id]
        logger.debug(f"Engine {container_id[:12]} cannot be stopped - has {len(active_streams)} active streams")
        return False
    
    # Check if stopping this engine would violate MIN_REPLICAS constraint
    # MIN_REPLICAS now represents minimum FREE engines, not total engines
    if cfg.MIN_REPLICAS > 0:
        try:
            from .replica_validator import replica_validator
            # Get accurate counts including free engines
            total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
            
            # If stopping this empty engine would leave us with fewer than MIN_REPLICAS free engines, don't stop it
            # Since this engine is already empty (has no active streams), stopping it reduces free count by 1
            if free_count - 1 < cfg.MIN_REPLICAS:
                logger.debug(f"Engine {container_id[:12]} cannot be stopped - would violate MIN_REPLICAS={cfg.MIN_REPLICAS} (currently: {free_count} free, would become: {free_count - 1})")
                return False
        except Exception as e:
            logger.error(f"Error checking MIN_REPLICAS constraint: {e}")
            # On error, err on the side of caution and don't stop the engine
            return False
    
    # If bypassing grace period (for testing or immediate shutdown), allow stopping
    if bypass_grace_period or cfg.ENGINE_GRACE_PERIOD_S == 0:
        if container_id in _empty_engine_timestamps:
            del _empty_engine_timestamps[container_id]
        return True
    
    # Engine is empty, check grace period
    if container_id not in _empty_engine_timestamps:
        # First time we see this engine as empty, record timestamp
        _empty_engine_timestamps[container_id] = now
        logger.debug(f"Engine {container_id[:12]} became empty, starting grace period")
        return False
    
    # Check if grace period has elapsed
    empty_since = _empty_engine_timestamps[container_id]
    grace_period = timedelta(seconds=cfg.ENGINE_GRACE_PERIOD_S)
    
    if now - empty_since >= grace_period:
        logger.info(f"Engine {container_id[:12]} has been empty for {cfg.ENGINE_GRACE_PERIOD_S}s, can be stopped")
        del _empty_engine_timestamps[container_id]
        return True
    
    remaining = grace_period - (now - empty_since)
    logger.debug(f"Engine {container_id[:12]} in grace period, {remaining.total_seconds():.0f}s remaining")
    return False

def scale_to(demand: int):
    from .replica_validator import replica_validator
    
    desired = min(max(cfg.MIN_REPLICAS, demand), cfg.MAX_REPLICAS)
    
    # Use reliable Docker count
    docker_status = replica_validator.get_docker_container_status()
    running_count = docker_status['total_running']
    running_containers = docker_status['containers']
    
    if running_count < desired:
        deficit = desired - running_count
        logger.info(f"Scaling up: starting {deficit} AceStream containers (current: {running_count}, desired: {desired})")
        for i in range(deficit):
            try:
                # Use AceStream provisioning to ensure containers are AceStream-ready with proper ports
                response = start_acestream(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                logger.info(f"Started AceStream container {response.container_id[:12]} for scale-up ({i+1}/{deficit}) - HTTP port: {response.host_http_port}")
            except Exception as e:
                logger.error(f"Failed to start AceStream container for scale-up: {e}")
        
        # Reindex after scaling up to pick up new containers
        if deficit > 0:
            logger.info("Reindexing after scale-up to pick up new containers")
            try:
                from .reindex import reindex_existing
                reindex_existing()
            except Exception as e:
                logger.error(f"Failed to reindex after scale-up: {e}")
                
    elif running_count > desired:
        excess = running_count - desired
        logger.info(f"Scaling down: checking {excess} containers for safe removal (current: {running_count}, desired: {desired})")
        
        # Only stop containers that can be safely stopped (respecting grace period)
        stopped_count = 0
        for c in running_containers:
            if stopped_count >= excess:
                break
            
            if can_stop_engine(c.id, bypass_grace_period=False):
                try:
                    c.stop(timeout=5)
                    c.remove()
                    stopped_count += 1
                    logger.info(f"Stopped and removed container {c.id[:12]} ({stopped_count}/{excess})")
                except Exception as e:
                    logger.error(f"Failed to stop container {c.id[:12]}: {e}")
        
        if stopped_count < excess:
            logger.info(f"Only stopped {stopped_count}/{excess} containers due to grace period restrictions")
