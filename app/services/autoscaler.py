import threading
import time
import os
from ..core.config import cfg
from .provisioner import ResourceScheduler, EngineSpec, execute_engine_spec, stop_container
from .state import state
from .circuit_breaker import circuit_breaker_manager
from .event_logger import event_logger
from .replica_validator import replica_validator
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, NamedTuple, Tuple

logger = logging.getLogger(__name__)

# Track when engines became empty for grace period implementation
_empty_engine_timestamps = {}

_vpn_block_log_lock = threading.Lock()
_last_vpn_block_log_ts = 0.0
_last_vpn_block_reason = ""
_suppressed_vpn_block_logs = 0
_VPN_BLOCK_LOG_INTERVAL_S = max(1.0, float(os.getenv("VPN_BLOCK_LOG_INTERVAL_S", "5")))


def _is_transient_vpn_not_ready_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    markers = (
        "no healthy dynamic vpn nodes available",
        "cannot schedule acestream engine",
        "control api not reachable",
    )
    return any(marker in message for marker in markers)


def _log_vpn_not_ready_block(error: Exception):
    """Rate-limit repeated VPN readiness block logs during startup bursts."""
    global _last_vpn_block_log_ts, _last_vpn_block_reason, _suppressed_vpn_block_logs

    reason = str(error)
    now = time.monotonic()

    with _vpn_block_log_lock:
        should_emit = (
            (now - _last_vpn_block_log_ts) >= _VPN_BLOCK_LOG_INTERVAL_S
            or reason != _last_vpn_block_reason
        )

        if not should_emit:
            _suppressed_vpn_block_logs += 1
            return

        suppressed = _suppressed_vpn_block_logs
        _suppressed_vpn_block_logs = 0
        _last_vpn_block_log_ts = now
        _last_vpn_block_reason = reason

    if suppressed > 0:
        logger.info(
            "Create intents blocked awaiting VPN readiness (%s suppressed): %s",
            suppressed,
            reason,
        )
        return

    logger.info("Create intent blocked awaiting VPN readiness: %s", reason)

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


def _compute_desired_replicas(total_running: int, free_count: int) -> tuple[int, str]:
    """Compute desired replica count declaratively based on current stream pressure."""
    all_engines = state.list_engines()
    if not all_engines:
        return max(0, int(cfg.MIN_REPLICAS)), "minimum replicas (no engines exist)"

    monitor_loads = state.get_active_monitor_load_by_engine()
    stream_counts = []
    for engine in all_engines:
        active_stream_count = len(state.list_streams(status="started", container_id=engine.container_id))
        monitor_stream_count = monitor_loads.get(engine.container_id, 0)
        stream_counts.append(active_stream_count + monitor_stream_count)

    min_streams = min(stream_counts)
    max_streams_threshold = cfg.MAX_STREAMS_PER_ENGINE - 1
    any_engine_near_capacity = any(count >= max_streams_threshold for count in stream_counts)
    all_engines_near_capacity = all(count >= max_streams_threshold for count in stream_counts)

    lookahead_layer = state.get_lookahead_layer()
    all_at_lookahead_layer = lookahead_layer is None or min_streams >= lookahead_layer

    # Always maintain an idle pool for fast failover recovery.
    idle_pool_deficit = max(0, int(cfg.MIN_FREE_REPLICAS) - int(free_count))
    desired = total_running + idle_pool_deficit
    if idle_pool_deficit > 0:
        target_description = (
            f"replenishing idle pool (missing {idle_pool_deficit}, "
            f"free engines: {free_count}/{cfg.MIN_FREE_REPLICAS})"
        )
    else:
        target_description = (
            f"idle pool satisfied (free engines: {free_count}/{cfg.MIN_FREE_REPLICAS}); "
            f"no engines at layer {max_streams_threshold} yet (lookahead not triggered)"
        )

    if any_engine_near_capacity:
        if all_engines_near_capacity:
            if all_at_lookahead_layer:
                lookahead_desired = total_running + 1
                desired = max(desired, lookahead_desired)
                if idle_pool_deficit > 0:
                    target_description = (
                        f"replenishing idle pool (missing {idle_pool_deficit}) and "
                        f"lookahead triggered (all engines at layer {max_streams_threshold})"
                    )
                else:
                    target_description = f"all engines at layer {max_streams_threshold} (LOOKAHEAD: preparing for overflow)"
                state.set_lookahead_layer(min_streams)
            else:
                if idle_pool_deficit > 0:
                    target_description = (
                        f"replenishing idle pool (missing {idle_pool_deficit}); "
                        f"waiting for all engines to reach layer {lookahead_layer}"
                    )
                else:
                    target_description = f"waiting for all engines to reach layer {lookahead_layer}"
        else:
            if free_count >= cfg.MIN_FREE_REPLICAS:
                target_description = f"lookahead buffer satisfied (free engines: {free_count})"
            elif all_at_lookahead_layer:
                lookahead_desired = total_running + 1
                desired = max(desired, lookahead_desired)
                if idle_pool_deficit > 0:
                    target_description = (
                        f"replenishing idle pool (missing {idle_pool_deficit}) and "
                        f"lookahead triggered (first engine at layer {max_streams_threshold})"
                    )
                else:
                    target_description = f"lookahead triggered (first engine at layer {max_streams_threshold})"
                state.set_lookahead_layer(min_streams)
            else:
                if idle_pool_deficit > 0:
                    target_description = (
                        f"replenishing idle pool (missing {idle_pool_deficit}); "
                        f"waiting for all engines to reach layer {lookahead_layer}"
                    )
                else:
                    target_description = f"waiting for all engines to reach layer {lookahead_layer}"
    else:
        if lookahead_layer is not None and min_streams < lookahead_layer:
            state.reset_lookahead_layer()

    # Enforce configured min/max boundaries
    desired = max(int(cfg.MIN_REPLICAS), min(desired, int(cfg.MAX_REPLICAS)))

    return desired, target_description

def ensure_minimum(*_args, **_kwargs):
    """Ensure minimum number of replicas are available.
    
    Uses layer-based lookahead provisioning to update desired replicas.
    """
    try:
        # Skip autoscaling if manual mode is enabled
        from .settings_persistence import SettingsPersistence
        engine_settings = SettingsPersistence.load_engine_settings() or {}
        if engine_settings.get('manual_mode'):
            logger.debug("Autoscaler paused: manual mode is enabled")
            return

        # Keep explicit check for compatibility and observability.
        can_provision_now = circuit_breaker_manager.can_provision("general")
        if not can_provision_now:
            logger.warning("Circuit breaker is OPEN - provisioning will be blocked until recovery")
        
        # Use replica_validator to get accurate counts including free engines
        try:
            total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
        except Exception:
            docker_status = replica_validator.get_docker_container_status() if hasattr(replica_validator, "get_docker_container_status") else {}
            total_running = int(docker_status.get("total_running", len(state.list_engines())))
            free_count = 0
            used_engines = total_running

        desired, target_description = _compute_desired_replicas(total_running=total_running, free_count=free_count)
        previous_desired = state.get_desired_replica_count()
        state.set_desired_replica_count(desired)

        if desired != previous_desired:
            event_logger.log_event(
                event_type="system",
                category="scaling",
                message=f"Desired replicas updated to {desired}",
                details={
                    "previous_desired": previous_desired,
                    "new_desired": desired,
                    "actual": total_running,
                    "free_count": free_count,
                    "reason": target_description,
                },
            )

        logger.debug(
            "Autoscaler desired state updated "
            f"(actual={total_running}, desired={desired}, used={used_engines}, free={free_count}, reason={target_description})"
        )
        engine_controller.request_reconcile(reason="ensure_minimum")
        if not engine_controller.is_running():
            try:
                asyncio.run(engine_controller.reconcile_once())
            except RuntimeError:
                pass
                
    except Exception as e:
        logger.error(f"Error in ensure_minimum: {e}")
        import traceback
        logger.debug(f"Traceback: {traceback.format_exc()}")


class Intent(NamedTuple):
    action: str  # "create" | "terminate"
    payload: Any # EngineSpec | str (container_id)
    intent_id: str

class EngineController:
    def __init__(self):
        self._running = False
        self._loop_tasks: List[asyncio.Task] = []
        self._reconcile_event = asyncio.Event()
        self.intent_queue: asyncio.Queue[Intent] = asyncio.Queue()
        self.scheduler = ResourceScheduler()
        self._last_reconcile_at = 0.0

    async def start(self):
        if self._running:
            return
        self._running = True
        logger.info("Starting EngineController (Async Intent-Based Architecture)")
        
        # Spawn the two core loops
        self._loop_tasks = [
            asyncio.create_task(self._reconciliation_loop(), name="reconciler"),
            asyncio.create_task(self._intent_worker_loop(), name="intent-worker")
        ]

    async def stop(self):
        self._running = False
        self._reconcile_event.set()
        for task in self._loop_tasks:
            task.cancel()
        
        if self._loop_tasks:
            await asyncio.gather(*self._loop_tasks, return_exceptions=True)
        self._loop_tasks = []
        logger.info("EngineController stopped")

    def is_running(self) -> bool:
        return self._running

    def request_reconcile(self, reason: str = "manual"):
        """Nudge the controller to evaluate state immediately."""
        logger.debug(f"Reconciliation requested: {reason}")
        self._reconcile_event.set()

    async def reconcile_once(self):
        """Force a single-pass reconciliation (useful for startup/tests)."""
        await self._do_reconcile()

    async def _reconciliation_loop(self):
        while self._running:
            try:
                try:
                    await asyncio.wait_for(self._reconcile_event.wait(), timeout=float(cfg.AUTOSCALE_INTERVAL_S))
                except asyncio.TimeoutError:
                    pass
                
                self._reconcile_event.clear()
                if not self._running: break

                now = time.time()
                if now - self._last_reconcile_at < 1.0:
                    continue
                self._last_reconcile_at = now

                await self._do_reconcile()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in reconciliation loop: {e}")
                await asyncio.sleep(2)

    async def _do_reconcile(self):
        from .settings_persistence import SettingsPersistence
        if (SettingsPersistence.load_engine_settings() or {}).get("manual_mode"):
            return

        desired = state.get_desired_replica_count()
        engines = state.list_engines()
        managed_engines = [e for e in engines if not e.labels.get("manual")]
        
        # Count engines that are either healthy OR starting (unknown)
        # but exclude those marked as draining
        active_alive = [e for e in managed_engines if e.health_status in ("healthy", "unknown") and not state.is_engine_draining(e.container_id)]
        actual_count = len(active_alive)
        deficit = desired - actual_count

        # 1. Scaling UP (Deficit Filling)
        # We process this in a guarded block so that capacity rejections in one VPN 
        # do not abort the entire management pass for other nodes.
        if deficit > 0:
            total_engines = len(engines)
            creation_count = min(deficit, max(0, cfg.MAX_REPLICAS - total_engines))
            if creation_count > 0:
                logger.info(f"Scaling UP: deficit={deficit}, emitting {creation_count} creation intents")
                loop_reserved_names = []
                try:
                    for _ in range(creation_count):
                        try:
                            spec = self.scheduler.schedule_new_engine(extra_reserved_names=loop_reserved_names)
                            if spec:
                                loop_reserved_names.append(spec.container_name)
                                intent_data = state.emit_scaling_intent(
                                    "create_request", 
                                    details={
                                        "source": "autoscaler",
                                        "container_name": spec.container_name,
                                        "vpn_container": spec.vpn_container_id,
                                        "forwarded": spec.forwarded
                                    }
                                )
                                await self.intent_queue.put(Intent("create", spec, intent_data["id"]))
                        except Exception as e:
                            if _is_transient_vpn_not_ready_error(e):
                                _log_vpn_not_ready_block(e)
                                # Stop creating in this pass but continue to other lifecycle stages
                                break
                            else:
                                logger.error(f"Unexpected error creating engine intent: {e}")
                                break
                except Exception as e:
                    logger.error(f"Failed scaling up pass: {e}")

        # 2. Scaling DOWN (Purge Surplus)
        elif deficit < 0:
            surplus = abs(deficit)
            candidates = self._select_termination_candidates(active_alive, surplus)
            if candidates:
                logger.info(f"Scaling DOWN: surplus={surplus}, emitting {len(candidates)} termination intents")
                for c_id in candidates:
                    try:
                        intent_data = state.emit_scaling_intent("terminate_request", details={"container_id": c_id, "reason": "surplus_cleanup"})
                        await self.intent_queue.put(Intent("terminate", c_id, intent_data["id"]))
                    except Exception as e:
                        logger.error(f"Failed emitting termination intent for {c_id}: {e}")

        # 3. Density Rebalancing & Headless Node Recovery
        # We calculate the BALANCED LIMIT here to match the scheduler's logic.
        vpn_settings = SettingsPersistence.load_vpn_config() or {}
        max_per_vpn = vpn_settings.get("preferred_engines_per_vpn", cfg.PREFERRED_ENGINES_PER_VPN)
        
        # Determine the target density limit (Balanced Density)
        if max_per_vpn > 0 and desired > 0:
            # We count ACTIVE nodes (healthy or starting) to know our split target
            all_vpn_nodes = {node.get("container_name"): node for node in state.list_vpn_nodes() if node.get("managed_dynamic")}
            ready_vpn_count = sum(1 for n in all_vpn_nodes.values() if not state.is_vpn_node_draining(str(n.get("container_name") or "")))
            
            # If we know we need multiple nodes, but only one is ready, we still 
            # use the final desired count to calculate the fair-share split.
            required_nodes = math.ceil(desired / max_per_vpn)
            effective_limit = min(max_per_vpn, math.ceil(desired / max(1, required_nodes)))
        else:
            effective_limit = max_per_vpn

        if effective_limit > 0:
            active_by_vpn = {}
            for e in active_alive:
                if e.vpn_container:
                    active_by_vpn.setdefault(e.vpn_container, []).append(e)

            for vpn_name, vpn_engines in active_by_vpn.items():
                if len(vpn_engines) > effective_limit:
                    # Node is over-balanced. Check if we should move engines to less dense nodes.
                    all_engines_on_node = state.get_engines_by_vpn(vpn_name)
                    if any(state.is_engine_draining(e.container_id) for e in all_engines_on_node):
                        continue # Rebalance already in progress on this node

                    excess_count = len(vpn_engines) - effective_limit
                    # Prioritize draining followers with least workload
                    drain_candidates = [e for e in vpn_engines if not e.forwarded]
                    if not drain_candidates:
                        continue # Don't drain the leader unless absolutely necessary

                    to_drain = sorted(drain_candidates, key=lambda e: len(e.streams))[:excess_count]
                    logger.info("VPN node %s is over-balanced (%s > %s); rebalancing %s follower(s)", vpn_name, len(vpn_engines), effective_limit, len(to_drain))
                    for eng in to_drain:
                        state.mark_engine_draining(eng.container_id, reason="density_balanced")
                else:
                    # Node is WITHIN density balance limits. Check for "Headless" state.
                    if ResourceScheduler._node_supports_port_forwarding(all_vpn_nodes.get(vpn_name, {})):
                        has_leader = any(e.forwarded for e in vpn_engines)
                        has_pending = state.is_forwarded_engine_pending(vpn_name)
                        
                        if not has_leader and not has_pending:
                            # Node supports PF but has no leader. 
                            # If we have followers, we must drain one to force a leader replacement.
                            if vpn_engines:
                                logger.warning("VPN node %s is headless (PF-capable but no leader). Marking one follower for replacement.", vpn_name)
                                # Drain the most idle follower
                                candidate = sorted(vpn_engines, key=lambda e: len(e.streams))[0]
                                state.mark_engine_draining(candidate.container_id, reason="headless_correction")

        # 4. Cleanup Idle Draining Engines
        draining_engines = [e for e in managed_engines if state.is_engine_draining(e.container_id)]
        for e in draining_engines:
            if not e.streams and can_stop_engine(e.container_id, bypass_grace_period=True):
                intent_data = state.emit_scaling_intent("terminate_request", details={"container_id": e.container_id, "reason": "draining_idle_cleanup"})
                await self.intent_queue.put(Intent("terminate", e.container_id, intent_data["id"]))

    def _select_termination_candidates(self, engines: List[Any], count: int) -> List[str]:
        # Sort by idle status (no streams first), then by age (oldest first)
        candidates = sorted(
            engines,
            key=lambda e: (
                bool(e.streams),
                e.first_seen or datetime.min
            )
        )
        return [e.container_id for e in candidates[:count] if can_stop_engine(e.container_id)]

    async def _intent_worker_loop(self):
        consecutive_failures = 0
        while self._running:
            try:
                intent = await self.intent_queue.get()
                try:
                    if intent.action == "create":
                        # Check circuit breaker before each creation intent
                        if not circuit_breaker_manager.can_provision("general"):
                            raise RuntimeError("Provisioning intent blocked by open circuit breaker")

                        await asyncio.to_thread(execute_engine_spec, intent.payload)
                        circuit_breaker_manager.record_provisioning_success("general")
                        consecutive_failures = 0
                    elif intent.action == "terminate":
                        await asyncio.to_thread(stop_container, intent.payload)
                    
                    state.resolve_scaling_intent(intent.intent_id, "applied")
                except Exception as e:
                    error_msg = str(e).lower()
                    
                    # Detect host-level seccomp/BPF ceiling (errno 524)
                    is_seccomp_error = any(m in error_msg for m in ["seccomp", "errno 524", "failed to create shim task"])
                    if is_seccomp_error:
                        logger.critical(
                            "CRITICAL: Host OS seccomp limit reached (errno 524). "
                            "New containers cannot be started. Please check kernel BPF limits or restart Docker."
                        )
                        # Speed up circuit breaker opening for this specific fatal host error
                        consecutive_failures = max(consecutive_failures, 5)

                    if intent.action == "create":
                        circuit_breaker_manager.record_provisioning_failure("general")
                        consecutive_failures += 1
                        
                        # Apply back-off to prevent hammering the Docker API during failures
                        backoff_s = min(60, 2 ** (consecutive_failures - 1))
                        logger.warning(f"Provisioning intent failed (failure #{consecutive_failures}). Backing off for {backoff_s}s...")
                        await asyncio.sleep(backoff_s)

                    logger.error(f"Failed to execute {intent.action} intent {intent.intent_id}: {e}")
                    state.resolve_scaling_intent(intent.intent_id, "failed", {"error": str(e)})
                finally:
                    self.intent_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in intent worker loop: {e}")
                await asyncio.sleep(1)


engine_controller = EngineController()

def can_stop_engine(container_id: str, bypass_grace_period: bool = False) -> bool:
    """Check if an engine can be safely stopped based on grace period and minimum free replicas."""
    now = datetime.now()
    
    # Check if engine has any active streams
    active_streams = state.list_streams(status="started", container_id=container_id)
    monitor_loads = state.get_active_monitor_load_by_engine()
    monitor_stream_count = monitor_loads.get(container_id, 0)
    if active_streams or monitor_stream_count > 0:
        # Engine has active streams, remove from empty tracking
        if container_id in _empty_engine_timestamps:
            del _empty_engine_timestamps[container_id]
        logger.debug(
            f"Engine {container_id[:12]} cannot be stopped - has "
            f"{len(active_streams)} active streams and {monitor_stream_count} monitoring sessions"
        )
        return False
    
    # Check if stopping this engine would violate replica constraints
    try:
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
        
        # Check 3: Maintain balanced distribution across healthy VPN nodes when possible.
        engine = state.get_engine(container_id)
        if engine and engine.vpn_container:
            healthy_vpn_names = {
                str(node.get("container_name") or "")
                for node in state.list_vpn_nodes()
                if bool(node.get("healthy")) and str(node.get("container_name") or "")
            }
            if len(healthy_vpn_names) > 1 and engine.vpn_container in healthy_vpn_names:
                counts = {
                    vpn_name: len(state.get_engines_by_vpn(vpn_name))
                    for vpn_name in healthy_vpn_names
                }
                current = counts.get(engine.vpn_container, 0)
                lowest_other = min(
                    (count for vpn_name, count in counts.items() if vpn_name != engine.vpn_container),
                    default=current,
                )
                if current < lowest_other:
                    if container_id in _empty_engine_timestamps:
                        del _empty_engine_timestamps[container_id]
                    logger.debug(
                        "Engine %s cannot be stopped - would unbalance healthy VPN distribution (current=%s, other_min=%s)",
                        container_id[:12],
                        current,
                        lowest_other,
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
    # Skip autoscaling if manual mode is enabled
    from .settings_persistence import SettingsPersistence
    engine_settings = SettingsPersistence.load_engine_settings() or {}
    if engine_settings.get('manual_mode'):
        logger.debug("Manual mode is enabled, skipping scale_to")
        return

    desired = min(max(cfg.MIN_REPLICAS, demand), cfg.MAX_REPLICAS)

    # Keep validator call for state/docker consistency before updating desired state.
    docker_status = replica_validator.get_docker_container_status()
    running_count = docker_status['total_running']
    previous_desired = state.get_desired_replica_count()
    state.set_desired_replica_count(desired)

    logger.info(
        f"Scale target updated (actual={running_count}, previous_desired={previous_desired}, desired={desired})"
    )

    event_logger.log_event(
        event_type="system",
        category="scaling",
        message=f"Manual scale request updated desired replicas to {desired}",
        details={
            "actual": running_count,
            "previous_desired": previous_desired,
            "new_desired": desired,
        },
    )

    engine_controller.request_reconcile(reason="scale_to")
    if not engine_controller.is_running():
        try:
            asyncio.run(engine_controller.reconcile_once())
        except RuntimeError:
            pass
