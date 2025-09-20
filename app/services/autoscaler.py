from ..core.config import cfg
from .provisioner import StartRequest, start_container
from .health import list_managed
import logging

logger = logging.getLogger(__name__)

def ensure_minimum():
    """Ensure minimum number of replicas are running."""
    try:
        running = [c for c in list_managed() if c.status == "running"]
        deficit = cfg.MIN_REPLICAS - len(running)
        
        if deficit > 0:
            logger.info(f"Starting {deficit} containers to meet MIN_REPLICAS={cfg.MIN_REPLICAS} (currently running: {len(running)})")
            
        for i in range(max(deficit, 0)):
            try:
                container_id = start_container(StartRequest(image=cfg.TARGET_IMAGE))
                logger.info(f"Successfully started container {container_id[:12]} ({i+1}/{deficit})")
            except Exception as e:
                logger.error(f"Failed to start container {i+1}/{deficit}: {e}")
                # Continue trying to start remaining containers
                
    except Exception as e:
        logger.error(f"Error in ensure_minimum: {e}")

def scale_to(demand: int):
    desired = min(max(cfg.MIN_REPLICAS, demand), cfg.MAX_REPLICAS)
    current = list_managed()
    running = [c for c in current if c.status == "running"]
    if len(running) < desired:
        deficit = desired - len(running)
        logger.info(f"Scaling up: starting {deficit} containers (current: {len(running)}, desired: {desired})")
        for i in range(deficit):
            try:
                container_id = start_container(StartRequest(image=cfg.TARGET_IMAGE))
                logger.info(f"Started container {container_id[:12]} for scale-up ({i+1}/{deficit})")
            except Exception as e:
                logger.error(f"Failed to start container for scale-up: {e}")
    elif len(running) > desired:
        excess = len(running) - desired
        logger.info(f"Scaling down: stopping {excess} containers (current: {len(running)}, desired: {desired})")
        for c in running[desired:]:
            try:
                c.stop(timeout=5)
                c.remove()
                logger.info(f"Stopped and removed container {c.id[:12]}")
            except Exception as e:
                logger.error(f"Failed to stop container {c.id[:12]}: {e}")
