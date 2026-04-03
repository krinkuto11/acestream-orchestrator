import threading
from ..core.config import cfg
from .provisioner import AceProvisionRequest, start_acestream, stop_container
from .state import state
from .circuit_breaker import circuit_breaker_manager
from .event_logger import event_logger
from .replica_validator import replica_validator
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Track when engines became empty for grace period implementation
_empty_engine_timestamps = {}


def _is_transient_vpn_not_ready_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    markers = (
        "no healthy dynamic vpn nodes available",
        "cannot schedule acestream engine",
        "control api not reachable",
    )
    return any(marker in message for marker in markers)

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

    desired = total_running
    target_description = f"no engines at layer {max_streams_threshold} yet (lookahead not triggered)"

    if any_engine_near_capacity:
        if all_engines_near_capacity:
            if all_at_lookahead_layer:
                desired = total_running + 1
                target_description = f"all engines at layer {max_streams_threshold} (LOOKAHEAD: preparing for overflow)"
                state.set_lookahead_layer(min_streams)
            else:
                target_description = f"waiting for all engines to reach layer {lookahead_layer}"
        else:
            if free_count >= cfg.MIN_FREE_REPLICAS:
                target_description = f"lookahead buffer satisfied (free engines: {free_count})"
            elif all_at_lookahead_layer:
                desired = total_running + 1
                target_description = f"lookahead triggered (first engine at layer {max_streams_threshold})"
                state.set_lookahead_layer(min_streams)
            else:
                target_description = f"waiting for all engines to reach layer {lookahead_layer}"
    else:
        if lookahead_layer is not None and min_streams < lookahead_layer:
            state.reset_lookahead_layer()

    return max(0, desired), target_description

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


class EngineController:
    """Controller loop that reconciles desired and actual engine replicas."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._reconcile_signal = asyncio.Event()
        self._thread_signal = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._interval_s = max(1, int(cfg.AUTOSCALE_INTERVAL_S))

    async def start(self):
        if self._task and not self._task.done():
            return

        self._stop.clear()
        self._reconcile_signal.clear()
        self._thread_signal.clear()
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())
        logger.info(f"Engine controller started (interval={self._interval_s}s)")

    async def stop(self):
        self._stop.set()
        self._reconcile_signal.set()
        self._thread_signal.set()
        if self._task:
            await self._task
        logger.info("Engine controller stopped")

    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def reconcile_once(self):
        await self._reconcile_once()

    def request_reconcile(self, reason: str = "manual"):
        logger.debug(f"Engine controller reconcile requested: {reason}")
        self._thread_signal.set()
        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._reconcile_signal.set)
            except RuntimeError:
                pass

    async def _run(self):
        # Trigger an initial reconcile shortly after startup.
        self.request_reconcile(reason="startup")

        while not self._stop.is_set():
            if self._thread_signal.is_set():
                self._thread_signal.clear()

            await self._reconcile_once()

            stop_task = asyncio.create_task(self._stop.wait())
            signal_task = asyncio.create_task(self._reconcile_signal.wait())
            done, pending = await asyncio.wait(
                {stop_task, signal_task},
                timeout=self._interval_s,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()

            if stop_task in done:
                break

            self._reconcile_signal.clear()

    async def _reconcile_once(self):
        from .settings_persistence import SettingsPersistence

        engine_settings = SettingsPersistence.load_engine_settings() or {}
        if engine_settings.get("manual_mode"):
            return

        await self._enqueue_outdated_engine_termination_intents()
        await self._process_pending_termination_intents()

        desired = state.get_desired_replica_count()
        engines = state.list_engines()
        actual = len(engines)

        if actual < desired:
            deficit = desired - actual
            for _ in range(deficit):
                intent = state.emit_scaling_intent(
                    intent_type="create_request",
                    details={
                        "requested_by": "engine_controller",
                        "scheduler_request": True,
                        "desired": desired,
                        "actual": actual,
                    },
                )
                await self._execute_create_intent(intent)
                actual += 1

        elif actual > desired:
            excess = actual - desired
            candidates = self._build_termination_candidates(engines)
            removed = 0

            for engine in candidates:
                if removed >= excess:
                    break

                if not can_stop_engine(engine.container_id, bypass_grace_period=False):
                    continue

                intent = state.emit_scaling_intent(
                    intent_type="terminate_request",
                    details={
                        "requested_by": "engine_controller",
                        "desired": desired,
                        "actual": actual,
                        "container_id": engine.container_id,
                    },
                )
                await self._execute_terminate_intent(intent, engine.container_id)
                removed += 1
                actual -= 1

    async def _enqueue_outdated_engine_termination_intents(self):
        target = state.get_target_engine_config()
        target_hash = str(target.get("config_hash") or "").strip()
        if not target_hash:
            return

        pending = state.list_pending_scaling_intents(intent_type="terminate_request", limit=1000)
        pending_ids = {
            str((intent.get("details") or {}).get("container_id") or "")
            for intent in pending
        }

        outdated_candidates = []
        for engine in state.list_engines():
            engine_hash = str((engine.labels or {}).get("acestream.config_hash") or "").strip()
            if engine_hash and engine_hash == target_hash:
                continue
            if engine.container_id in pending_ids:
                continue
            outdated_candidates.append(engine)

        if not outdated_candidates:
            return

        candidate = sorted(
            outdated_candidates,
            key=lambda e: (
                bool(state.list_streams(status="started", container_id=e.container_id)),
                e.last_seen or datetime.min,
            ),
        )[0]

        state.emit_scaling_intent(
            intent_type="terminate_request",
            details={
                "requested_by": "engine_controller",
                "eviction_reason": "config_hash_mismatch",
                "target_config_hash": target_hash,
                "container_id": candidate.container_id,
                "force": False,
            },
        )

    async def _process_pending_termination_intents(self):
        pending = state.list_pending_scaling_intents(intent_type="terminate_request", limit=1000)
        if not pending:
            return

        for intent in pending:
            details = intent.get("details") or {}
            container_id = str(details.get("container_id") or "").strip()
            if not container_id:
                state.resolve_scaling_intent(intent.get("id"), "failed", {"error": "missing_container_id"})
                continue

            force = bool(details.get("force", False))
            if not force and not can_stop_engine(container_id, bypass_grace_period=False):
                continue

            await self._execute_terminate_intent(intent, container_id, force=force)

    def _build_termination_candidates(self, engines):
        active_streams = state.list_streams(status="started")
        used_container_ids = {stream.container_id for stream in active_streams}
        used_container_ids.update(state.get_active_monitor_container_ids())

        # Prefer terminating idle non-forwarded engines first.
        return sorted(
            engines,
            key=lambda e: (
                e.container_id in used_container_ids,
                bool(e.forwarded),
                e.last_seen or datetime.min,
            ),
        )

    async def _execute_create_intent(self, intent: dict):
        intent_id = intent.get("id")

        if not circuit_breaker_manager.can_provision("general"):
            state.resolve_scaling_intent(intent_id, "blocked", {"reason": "circuit_breaker_open"})
            return

        try:
            response = await asyncio.to_thread(start_acestream, AceProvisionRequest())
            circuit_breaker_manager.record_provisioning_success("general")
            state.resolve_scaling_intent(
                intent_id,
                "applied",
                {"container_id": response.container_id, "container_name": response.container_name},
            )
        except Exception as e:
            if _is_transient_vpn_not_ready_error(e):
                logger.info("Create intent blocked awaiting VPN readiness: %s", e)
                state.resolve_scaling_intent(intent_id, "blocked", {"reason": "vpn_not_ready", "error": str(e)})
                return
            circuit_breaker_manager.record_provisioning_failure("general")
            state.resolve_scaling_intent(intent_id, "failed", {"error": str(e)})

    async def _execute_terminate_intent(self, intent: dict, container_id: str, force: bool = False):
        intent_id = intent.get("id")
        try:
            await asyncio.to_thread(stop_container, container_id, force)
            state.remove_engine(container_id)
            state.resolve_scaling_intent(intent_id, "applied", {"container_id": container_id})
        except Exception as e:
            state.resolve_scaling_intent(intent_id, "failed", {"container_id": container_id, "error": str(e)})


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
