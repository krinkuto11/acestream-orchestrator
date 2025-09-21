from ..core.config import cfg
from .provisioner import StartRequest, start_container, AceProvisionRequest, start_acestream
from .health import list_managed
from .state import state
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Track when engines became empty for grace period implementation
_empty_engine_timestamps = {}

def ensure_minimum():
    """Ensure minimum number of replicas are running."""
    try:
        running = [c for c in list_managed() if c.status == "running"]
        deficit = cfg.MIN_REPLICAS - len(running)
        
        if deficit > 0:
            logger.info(f"Starting {deficit} AceStream containers to meet MIN_REPLICAS={cfg.MIN_REPLICAS} (currently running: {len(running)})")
            
        for i in range(max(deficit, 0)):
            try:
                # Use AceStream provisioning to ensure containers are AceStream-ready with proper ports
                response = start_acestream(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                logger.info(f"Successfully started AceStream container {response.container_id[:12]} ({i+1}/{deficit}) - HTTP port: {response.host_http_port}")
            except Exception as e:
                logger.error(f"Failed to start AceStream container {i+1}/{deficit}: {e}")
                # Continue trying to start remaining containers
                
    except Exception as e:
        logger.error(f"Error in ensure_minimum: {e}")

def can_stop_engine(container_id: str, bypass_grace_period: bool = False) -> bool:
    """Check if an engine can be safely stopped based on grace period and minimum replicas."""
    now = datetime.now()
    
    # Check if engine has any active streams
    active_streams = state.list_streams(status="started", container_id=container_id)
    if active_streams:
        # Engine has active streams, remove from empty tracking
        if container_id in _empty_engine_timestamps:
            del _empty_engine_timestamps[container_id]
        return False
    
    # Check if stopping this engine would violate MIN_REPLICAS constraint
    if cfg.MIN_REPLICAS > 0:
        try:
            running_containers = [c for c in list_managed() if c.status == "running"]
            # If stopping this engine would bring us below MIN_REPLICAS, don't stop it
            if len(running_containers) - 1 < cfg.MIN_REPLICAS:
                logger.debug(f"Engine {container_id[:12]} cannot be stopped - would violate MIN_REPLICAS={cfg.MIN_REPLICAS} (currently: {len(running_containers)} running, would become: {len(running_containers) - 1})")
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

def ensure_minimum_free():
    """Ensure minimum number of free (unused) engines are available."""
    try:
        # Get current state
        all_engines = state.list_engines()
        active_streams = state.list_streams(status="started")
        
        # Find engines that are currently in use
        used_container_ids = {stream.container_id for stream in active_streams}
        
        # Calculate free engines
        running_containers = [c for c in list_managed() if c.status == "running"]
        total_running = len(running_containers)
        used_engines = len(used_container_ids)
        free_count = total_running - used_engines
        
        deficit = cfg.MIN_REPLICAS - free_count
        
        if deficit > 0:
            logger.info(f"Need {deficit} more free engines (total: {total_running}, used: {used_engines}, free: {free_count}, min_free: {cfg.MIN_REPLICAS})")
            
            # Start new containers to meet the free engine requirement
            for i in range(deficit):
                try:
                    response = start_acestream(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                    logger.info(f"Started AceStream container {response.container_id[:12]} for free pool ({i+1}/{deficit}) - HTTP port: {response.host_http_port}")
                except Exception as e:
                    logger.error(f"Failed to start AceStream container for free pool: {e}")
        else:
            logger.debug(f"Sufficient free engines: {free_count} free, {cfg.MIN_REPLICAS} required")
                
    except Exception as e:
        logger.error(f"Error ensuring minimum free engines: {e}")

def scale_to(demand: int):
    desired = min(max(cfg.MIN_REPLICAS, demand), cfg.MAX_REPLICAS)
    current = list_managed()
    running = [c for c in current if c.status == "running"]
    if len(running) < desired:
        deficit = desired - len(running)
        logger.info(f"Scaling up: starting {deficit} AceStream containers (current: {len(running)}, desired: {desired})")
        for i in range(deficit):
            try:
                # Use AceStream provisioning to ensure containers are AceStream-ready with proper ports
                response = start_acestream(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                logger.info(f"Started AceStream container {response.container_id[:12]} for scale-up ({i+1}/{deficit}) - HTTP port: {response.host_http_port}")
            except Exception as e:
                logger.error(f"Failed to start AceStream container for scale-up: {e}")
    elif len(running) > desired:
        excess = len(running) - desired
        logger.info(f"Scaling down: checking {excess} containers for safe removal (current: {len(running)}, desired: {desired})")
        
        # Only stop containers that can be safely stopped (respecting grace period)
        stopped_count = 0
        for c in running:
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
