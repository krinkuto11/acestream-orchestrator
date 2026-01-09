import os
import time
from ..core.config import cfg
from .provisioner import StartRequest, start_container, AceProvisionRequest, start_acestream, stop_container
from .health import list_managed
from .state import state
from .circuit_breaker import circuit_breaker_manager
from .event_logger import event_logger
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

def ensure_minimum(initial_startup: bool = False):
    """Ensure minimum number of replicas are available.
    
    Args:
        initial_startup: If True, provisions MIN_REPLICAS total containers on startup.
                        If False, maintains MIN_FREE_REPLICAS free/empty containers during runtime
                        OR provisions new engine when all engines have reached (ACEXY_MAX_STREAMS_PER_ENGINE - 1) streams.
    """
    try:
        from .replica_validator import replica_validator
        
        # Skip autoscaling if in emergency mode (unless initial startup)
        if not initial_startup and state.is_emergency_mode():
            emergency_info = state.get_emergency_mode_info()
            logger.debug(f"Autoscaler paused: in emergency mode (failed VPN: {emergency_info['failed_vpn']})")
            return
        
        # Skip autoscaling if in reprovisioning mode (unless initial startup)
        if not initial_startup and state.is_reprovisioning_mode():
            logger.debug("Autoscaler paused: in reprovisioning mode")
            return
        
        # Check circuit breaker before attempting provisioning
        if not circuit_breaker_manager.can_provision("general"):
            logger.warning("Circuit breaker is OPEN - skipping provisioning attempt")
            return
        
        # Use replica_validator to get accurate counts including free engines
        total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
        
        # Determine target based on startup vs runtime
        if initial_startup:
            # On startup: ensure we have MIN_REPLICAS total containers
            target = cfg.MIN_REPLICAS
            deficit = target - total_running
            target_description = f"MIN_REPLICAS={cfg.MIN_REPLICAS} total containers"
        else:
            # During runtime: check if we need to provision based on ACEXY_MAX_STREAMS_PER_ENGINE
            # LOOKAHEAD PROVISIONING: Start provisioning when FIRST engine reaches (MAX-1)
            # This gives time for the new engine to spin up before capacity is exhausted
            all_engines = state.list_engines()
            if all_engines:
                # Get stream counts per engine
                engines_with_stream_counts = []
                for engine in all_engines:
                    stream_count = len(state.list_streams(status="started", container_id=engine.container_id))
                    engines_with_stream_counts.append((engine.container_id, stream_count))
                
                # LOOKAHEAD TRIGGER: Check if ANY engine has reached (MAX_STREAMS - 1)
                # This provides early warning and provisioning buffer
                max_streams_threshold = cfg.ACEXY_MAX_STREAMS_PER_ENGINE - 1
                any_engine_near_capacity = any(count >= max_streams_threshold for _, count in engines_with_stream_counts)
                
                # Check if all engines have at least (MAX_STREAMS - 1) streams
                all_engines_near_capacity = all(count >= max_streams_threshold for _, count in engines_with_stream_counts)
                
                if any_engine_near_capacity:
                    # At least one engine has reached threshold - use lookahead provisioning
                    # Provision new engine to be ready before overflow occurs
                    if all_engines_near_capacity:
                        # All engines at threshold - this is the critical moment
                        deficit = 1
                        target = total_running + 1
                        target_description = f"all engines at layer {max_streams_threshold} (LOOKAHEAD: preparing for overflow)"
                        logger.info(f"All {len(all_engines)} engines at layer {max_streams_threshold}, provisioning new engine (lookahead)")
                    else:
                        # Only some engines at threshold - check if we already have a free engine ready
                        # If we have MIN_FREE_REPLICAS free engines, don't provision yet
                        if free_count >= cfg.MIN_FREE_REPLICAS:
                            # We have free engines ready for when needed
                            deficit = 0
                            target = total_running
                            target_description = f"lookahead buffer satisfied (free engines: {free_count})"
                            logger.debug(f"Some engines at layer {max_streams_threshold}, but {free_count} free engines available")
                        else:
                            # Start provisioning to have engine ready when needed
                            deficit = 1
                            target = total_running + 1
                            target_description = f"lookahead triggered (first engine at layer {max_streams_threshold})"
                            logger.info(f"Lookahead provisioning: first engine reached layer {max_streams_threshold}, preparing new engine")
                else:
                    # No engines at threshold yet, check MIN_FREE_REPLICAS as fallback
                    target = cfg.MIN_FREE_REPLICAS
                    deficit = target - free_count
                    target_description = f"MIN_FREE_REPLICAS={cfg.MIN_FREE_REPLICAS} free engines"
            else:
                # No engines exist, use MIN_FREE_REPLICAS
                target = cfg.MIN_FREE_REPLICAS
                deficit = target - free_count
                target_description = f"MIN_FREE_REPLICAS={cfg.MIN_FREE_REPLICAS} free engines"
        
        # When using Gluetun, respect MAX_REPLICAS as a hard limit
        if cfg.GLUETUN_CONTAINER_NAME:
            max_new_containers = cfg.MAX_REPLICAS - total_running
            if deficit > max_new_containers:
                deficit = max_new_containers
        
        if deficit <= 0:
            logger.debug(f"Sufficient replicas available for {target_description} (total: {total_running}, used: {used_engines}, free: {free_count})")
            return  # Already have enough
        
        # Check if already at MAX_REPLICAS limit (when using Gluetun)
        if cfg.GLUETUN_CONTAINER_NAME and deficit > 0:
            max_new_containers = cfg.MAX_REPLICAS - total_running
            if max_new_containers <= 0:
                logger.warning(
                    f"Cannot start containers - already at MAX_REPLICAS limit ({cfg.MAX_REPLICAS}). "
                    f"Current state: total={total_running}, used={used_engines}, free={free_count}. "
                    f"To maintain {target_description}, increase MAX_REPLICAS."
                )
                return
            # Adjust deficit to not exceed the limit
            if deficit > max_new_containers:
                logger.info(
                    f"Reducing planned containers from {deficit} to {max_new_containers} to stay within "
                    f"MAX_REPLICAS limit ({cfg.MAX_REPLICAS}). "
                    f"Current state: total={total_running}, used={used_engines}, free={free_count}"
                )
                deficit = max_new_containers
        
        logger.info(f"Starting {deficit} AceStream containers to maintain {target_description} (currently: total={total_running}, used={used_engines}, free={free_count})")
        
        if deficit > 0:
            # Log autoscaling event
            event_logger.log_event(
                event_type="system",
                category="scaling",
                message=f"Auto-scaling: provisioning {deficit} engines to meet {target_description}",
                details={
                    "deficit": deficit,
                    "total_running": total_running,
                    "free_count": free_count,
                    "target": target,
                    "initial_startup": initial_startup
                }
            )
            # Use simple synchronous provisioning for reliability
            success_count = 0
            failure_count = 0
            
            for i in range(deficit):
                try:
                    logger.debug(f"Attempting to start container {i+1}/{deficit}")
                    response = start_acestream(AceProvisionRequest())
                    
                    if response and response.container_id:
                        success_count += 1
                        circuit_breaker_manager.record_provisioning_success("general")
                        logger.info(f"Successfully started AceStream container {response.container_id[:12]} ({success_count}/{deficit})")
                        
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
    
    # Check if stopping this engine would violate replica constraints
    try:
        from .replica_validator import replica_validator
        # Get accurate counts including free engines
        total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
        
        # Check 1: Never go below MIN_REPLICAS total containers
        # Only enforce this if we actually have engines running (total_running > 0)
        # When total_running is 0, the engine being checked doesn't exist in Docker (state/docker mismatch),
        # so replica constraints don't apply - we're just cleaning up stale state
        if total_running > 0 and total_running - 1 < cfg.MIN_REPLICAS:
            # Engine is part of minimum replicas, remove from grace period tracking
            if container_id in _empty_engine_timestamps:
                del _empty_engine_timestamps[container_id]
            logger.debug(f"Engine {container_id[:12]} cannot be stopped - would violate MIN_REPLICAS={cfg.MIN_REPLICAS} (currently: {total_running} total, would become: {total_running - 1})")
            return False
        
        # Check 2: Maintain MIN_FREE_REPLICAS free engines
        # Only enforce this if we actually have free engines (free_count > 0)
        # When free_count is 0, there are no free engines in Docker (state/docker mismatch),
        # so replica constraints don't apply - we're just cleaning up stale state
        if cfg.MIN_FREE_REPLICAS > 0 and free_count > 0:
            # If stopping this empty engine would leave us with fewer than MIN_FREE_REPLICAS free engines, don't stop it
            # Since this engine is already empty (has no active streams), stopping it reduces free count by 1
            if free_count - 1 < cfg.MIN_FREE_REPLICAS:
                # Engine is part of minimum free replicas, remove from grace period tracking
                if container_id in _empty_engine_timestamps:
                    del _empty_engine_timestamps[container_id]
                logger.debug(f"Engine {container_id[:12]} cannot be stopped - would violate MIN_FREE_REPLICAS={cfg.MIN_FREE_REPLICAS} (currently: {free_count} free, would become: {free_count - 1})")
                return False
        
        # Check 3: In redundant VPN mode, maintain balanced distribution across VPNs
        # Don't stop engines that would break the balance (prefer stopping from the VPN with more engines)
        if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
            # Get the engine's VPN assignment
            engine = state.get_engine(container_id)
            if engine and engine.vpn_container:
                vpn1_name = cfg.GLUETUN_CONTAINER_NAME
                vpn2_name = cfg.GLUETUN_CONTAINER_NAME_2
                
                # Count engines per VPN
                vpn1_engines = state.get_engines_by_vpn(vpn1_name)
                vpn2_engines = state.get_engines_by_vpn(vpn2_name)
                vpn1_count = len(vpn1_engines)
                vpn2_count = len(vpn2_engines)
                
                engine_vpn = engine.vpn_container
                
                # Determine if stopping this engine would unbalance the distribution
                # Allow stopping only if this VPN has MORE engines than the other VPN
                # or if both have equal counts (balanced)
                if engine_vpn == vpn1_name:
                    # This engine is on VPN1
                    if vpn1_count < vpn2_count:
                        # VPN1 has fewer engines, don't stop this one
                        if container_id in _empty_engine_timestamps:
                            del _empty_engine_timestamps[container_id]
                        logger.debug(
                            f"Engine {container_id[:12]} cannot be stopped - would unbalance VPN distribution "
                            f"(VPN1: {vpn1_count} engines, VPN2: {vpn2_count} engines)"
                        )
                        return False
                elif engine_vpn == vpn2_name:
                    # This engine is on VPN2
                    if vpn2_count < vpn1_count:
                        # VPN2 has fewer engines, don't stop this one
                        if container_id in _empty_engine_timestamps:
                            del _empty_engine_timestamps[container_id]
                        logger.debug(
                            f"Engine {container_id[:12]} cannot be stopped - would unbalance VPN distribution "
                            f"(VPN1: {vpn1_count} engines, VPN2: {vpn2_count} engines)"
                        )
                        return False
    except Exception as e:
        logger.error(f"Error checking replica constraints: {e}")
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
    
    # When using Gluetun, MAX_REPLICAS already serves as the hard limit
    # No need for additional capping since desired is already capped at MAX_REPLICAS above
    
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
                response = start_acestream(AceProvisionRequest())
                logger.info(f"Started AceStream container {response.container_id[:12]} for scale-up ({i+1}/{deficit})")
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
                    stop_container(c.id)
                    stopped_count += 1
                    logger.info(f"Stopped and removed container {c.id[:12]} ({stopped_count}/{excess})")
                except Exception as e:
                    logger.error(f"Failed to stop container {c.id[:12]}: {e}")
        
        if stopped_count < excess:
            logger.info(f"Only stopped {stopped_count}/{excess} containers due to grace period restrictions")
