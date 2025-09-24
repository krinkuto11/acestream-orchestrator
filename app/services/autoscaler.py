import os
from ..core.config import cfg
from .provisioner import StartRequest, start_container, AceProvisionRequest, start_acestream
from .health import list_managed
from .state import state
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Track when engines became empty for grace period implementation
_empty_engine_timestamps = {}

# Rate limiting for engine provisioning
_provision_semaphore: Optional[asyncio.Semaphore] = None
_provision_queue = asyncio.Queue(maxsize=100)  # Queue for engine requests
_last_provision_time = 0

def _get_provision_semaphore() -> asyncio.Semaphore:
    """Get or create the provision semaphore for rate limiting."""
    global _provision_semaphore
    if _provision_semaphore is None:
        # Use config value for max concurrent provisions
        _provision_semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENT_PROVISIONS)
    return _provision_semaphore

async def _provision_engine_rate_limited(req: AceProvisionRequest) -> Optional[dict]:
    """Provision an engine with rate limiting and concurrency control."""
    global _last_provision_time
    
    semaphore = _get_provision_semaphore()
    
    async with semaphore:
        try:
            # Rate limiting - ensure minimum interval between provisions
            current_time = asyncio.get_event_loop().time()
            time_since_last = current_time - _last_provision_time
            
            if time_since_last < cfg.MIN_PROVISION_INTERVAL_S:
                sleep_time = cfg.MIN_PROVISION_INTERVAL_S - time_since_last
                logger.debug(f"Rate limiting: sleeping {sleep_time:.3f}s")
                await asyncio.sleep(sleep_time)
            
            # Update timestamp before starting provision to ensure proper spacing
            _last_provision_time = asyncio.get_event_loop().time()
            
            # Run the actual provisioning in a thread to avoid blocking
            def sync_provision():
                return start_acestream(req)
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                response = await asyncio.get_event_loop().run_in_executor(
                    executor, sync_provision
                )
            
            return response
            
        except Exception as e:
            logger.error(f"Rate-limited provisioning failed: {e}")
            return None

def ensure_minimum():
    """Ensure minimum number of replicas are running with improved error handling."""
    try:
        from .replica_validator import replica_validator
        
        # Get fresh Docker status to ensure accurate count
        docker_status = replica_validator.get_docker_container_status()
        running_count = docker_status['total_running']
        
        deficit = cfg.MIN_REPLICAS - running_count
        
        if deficit > 0:
            logger.info(f"Starting {deficit} AceStream containers to meet MIN_REPLICAS={cfg.MIN_REPLICAS} (currently running: {running_count})")
            
            # Create an async task to handle provision requests concurrently
            async def provision_engines():
                tasks = []
                for i in range(deficit):
                    task = _provision_engine_rate_limited(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                    tasks.append(task)
                
                # Execute all provisions concurrently but rate-limited
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                success_count = 0
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Failed to start AceStream container {i+1}/{deficit}: {result}")
                    elif result:
                        logger.info(f"Successfully started AceStream container {result.container_id[:12]} ({success_count+1}/{deficit}) - HTTP port: {result.host_http_port}")
                        success_count += 1
                    else:
                        logger.error(f"Failed to start AceStream container {i+1}/{deficit}: Unknown error")
                
                return success_count
            
            # Run the async provisioning
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If we're already in an event loop, create a task and add reindexing callback
                    async def provision_and_reindex():
                        await provision_engines()
                        # Reindex after async provisioning
                        if deficit > 0:
                            logger.info("Reindexing after async provisioning to pick up new containers")
                            try:
                                from .reindex import reindex_existing
                                reindex_existing()
                            except Exception as e:
                                logger.error(f"Failed to reindex after async provisioning: {e}")
                    
                    asyncio.create_task(provision_and_reindex())
                else:
                    # If not in event loop, run it and then reindex
                    loop.run_until_complete(provision_engines())
                    
                    # Reindex after provisioning
                    if deficit > 0:
                        logger.info("Reindexing after provisioning to pick up new containers")
                        try:
                            from .reindex import reindex_existing
                            reindex_existing()
                        except Exception as e:
                            logger.error(f"Failed to reindex after provisioning: {e}")
                            
            except RuntimeError:
                # Fallback to synchronous provisioning if async fails
                logger.warning("Async provisioning not available, falling back to synchronous")
                for i in range(max(deficit, 0)):
                    try:
                        response = start_acestream(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                        logger.info(f"Successfully started AceStream container {response.container_id[:12]} ({i+1}/{deficit}) - HTTP port: {response.host_http_port}")
                    except Exception as e:
                        logger.error(f"Failed to start AceStream container {i+1}/{deficit}: {e}")
            
            # After provisioning, immediately reindex to pick up new containers
            if deficit > 0:
                logger.info("Reindexing after provisioning to pick up new containers")
                try:
                    from .reindex import reindex_existing
                    reindex_existing()
                except Exception as e:
                    logger.error(f"Failed to reindex after provisioning: {e}")
                
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
            from .replica_validator import replica_validator
            # Use reliable Docker count for MIN_REPLICAS check
            docker_status = replica_validator.get_docker_container_status()
            running_count = docker_status['total_running']
            
            # If stopping this engine would bring us below MIN_REPLICAS, don't stop it
            if running_count - 1 < cfg.MIN_REPLICAS:
                logger.debug(f"Engine {container_id[:12]} cannot be stopped - would violate MIN_REPLICAS={cfg.MIN_REPLICAS} (currently: {running_count} running, would become: {running_count - 1})")
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
    """Ensure minimum number of free (unused) engines are available with rate limiting."""
    try:
        from .replica_validator import replica_validator
        
        # Use centralized validation to get reliable counts
        total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
        
        deficit = cfg.MIN_REPLICAS - free_count
        
        if deficit > 0:
            logger.info(f"Need {deficit} more free engines (total: {total_running}, used: {used_engines}, free: {free_count}, min_free: {cfg.MIN_REPLICAS})")
            
            # Use rate-limited provisioning
            async def provision_free_engines():
                tasks = []
                for i in range(deficit):
                    task = _provision_engine_rate_limited(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                    tasks.append(task)
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                success_count = 0
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Failed to start AceStream container for free pool: {result}")
                    elif result:
                        logger.info(f"Started AceStream container {result.container_id[:12]} for free pool ({success_count+1}/{deficit}) - HTTP port: {result.host_http_port}")
                        success_count += 1
                
                return success_count
            
            # Run async provisioning
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If we're already in an event loop, create a task and add reindexing callback
                    async def provision_free_and_reindex():
                        await provision_free_engines()
                        # Reindex after async provisioning
                        if deficit > 0:
                            logger.info("Reindexing after free engine provisioning to pick up new containers")
                            try:
                                from .reindex import reindex_existing
                                reindex_existing()
                            except Exception as e:
                                logger.error(f"Failed to reindex after free engine provisioning: {e}")
                    
                    asyncio.create_task(provision_free_and_reindex())
                else:
                    loop.run_until_complete(provision_free_engines())
                    
                    # Reindex after provisioning
                    if deficit > 0:
                        logger.info("Reindexing after free engine provisioning to pick up new containers")
                        try:
                            from .reindex import reindex_existing
                            reindex_existing()
                        except Exception as e:
                            logger.error(f"Failed to reindex after free engine provisioning: {e}")
                            
            except RuntimeError:
                # Fallback to synchronous
                logger.warning("Async provisioning not available for free engines, falling back to synchronous")
                for i in range(deficit):
                    try:
                        response = start_acestream(AceProvisionRequest(image=cfg.TARGET_IMAGE))
                        logger.info(f"Started AceStream container {response.container_id[:12]} for free pool ({i+1}/{deficit}) - HTTP port: {response.host_http_port}")
                    except Exception as e:
                        logger.error(f"Failed to start AceStream container for free pool: {e}")
                
                # Reindex after synchronous provisioning
                if deficit > 0:
                    logger.info("Reindexing after synchronous free engine provisioning to pick up new containers")
                    try:
                        from .reindex import reindex_existing
                        reindex_existing()
                    except Exception as e:
                        logger.error(f"Failed to reindex after synchronous free engine provisioning: {e}")
        else:
            logger.debug(f"Sufficient free engines: {free_count} free, {cfg.MIN_REPLICAS} required")
                
    except Exception as e:
        logger.error(f"Error ensuring minimum free engines: {e}")

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
