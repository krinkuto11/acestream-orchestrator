import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Source-instability guardrails for repeated EOF/read-timeout failovers.
EOF_FAILOVER_BUDGET = 3
EOF_FAILOVER_WINDOW_S = 180.0
PING_PONG_COOLDOWN_S = 20.0
PAIR_PENALTY_WINDOW_S = 300.0
PAIR_PENALTY_SCORE = 50
MAX_COOLDOWN_SLEEP_S = 30.0
MAX_TRACKED_EVENTS = 32

_guardrails_lock = threading.RLock()
_active_recoveries_lock = threading.Lock()
_active_recoveries: set[str] = set()
_stream_eof_failures: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=MAX_TRACKED_EVENTS))
_stream_recent_engines: Dict[str, Deque[Tuple[float, str]]] = defaultdict(lambda: deque(maxlen=MAX_TRACKED_EVENTS))
_stream_engine_failure_times: Dict[str, Dict[str, Deque[float]]] = defaultdict(
    lambda: defaultdict(lambda: deque(maxlen=MAX_TRACKED_EVENTS))
)
_stream_cooldowns: Dict[str, float] = {}


def _prune_deque(deq: Deque[float], now: float, window_s: float) -> None:
    while deq and (now - deq[0]) > window_s:
        deq.popleft()


def _record_source_failure(stream_id: str, stream_key: str, engine_id: Optional[str], now: float) -> int:
    with _guardrails_lock:
        eof_events = _stream_eof_failures[stream_id]
        eof_events.append(now)
        _prune_deque(eof_events, now, EOF_FAILOVER_WINDOW_S)

        if engine_id:
            key = str(stream_key or stream_id)
            engine_events = _stream_engine_failure_times[key][engine_id]
            engine_events.append(now)
            _prune_deque(engine_events, now, PAIR_PENALTY_WINDOW_S)

            recent_engines = _stream_recent_engines[stream_id]
            recent_engines.append((now, engine_id))
            while recent_engines and (now - recent_engines[0][0]) > PAIR_PENALTY_WINDOW_S:
                recent_engines.popleft()

            # If we keep bouncing between the same two engines quickly, apply
            # a short cooldown before attempting another migration.
            last_four = [item[1] for item in list(recent_engines)[-4:]]
            if len(last_four) == 4:
                if len(set(last_four)) == 2 and last_four[0] == last_four[2] and last_four[1] == last_four[3]:
                    _stream_cooldowns[stream_id] = now + PING_PONG_COOLDOWN_S

        return len(eof_events)


def _get_cooldown_remaining(stream_id: str, now: float) -> float:
    with _guardrails_lock:
        until = float(_stream_cooldowns.get(stream_id, 0.0))
        remaining = until - now
        if remaining <= 0:
            _stream_cooldowns.pop(stream_id, None)
            return 0.0
        return remaining


def _recent_pair_penalties(stream_key: str, now: float) -> Dict[str, int]:
    penalties: Dict[str, int] = {}
    key = str(stream_key or "")
    if not key:
        return penalties

    with _guardrails_lock:
        engine_map = _stream_engine_failure_times.get(key, {})
        for engine_id, deq in list(engine_map.items()):
            _prune_deque(deq, now, PAIR_PENALTY_WINDOW_S)
            if not deq:
                continue
            penalties[engine_id] = penalties.get(engine_id, 0) + (len(deq) * PAIR_PENALTY_SCORE)

    return penalties


def _mark_source_unstable(stream_id: str, dead_container_id: Optional[str], reason: str) -> None:
    from ..models.schemas import StreamEndedEvent
    from ..data_plane.internal_events import handle_stream_ended

    logger.warning(
        "Failover budget exceeded for stream %s; marking stream as source_unstable (reason=%s).",
        stream_id,
        reason,
    )
    handle_stream_ended(
        StreamEndedEvent(
            stream_id=stream_id,
            container_id=dead_container_id,
            reason="source_unstable",
        )
    )


def _is_source_instability_reason(reason: Optional[str]) -> bool:
    normalized = str(reason or "").strip().lower()
    return normalized in {"eof", "read_timeout", "read_timed_out", "no_data", "chunked_encoding_error"}


def _reset_guardrails_for_tests() -> None:
    with _guardrails_lock:
        _stream_eof_failures.clear()
        _stream_recent_engines.clear()
        _stream_engine_failure_times.clear()
        _stream_cooldowns.clear()

    with _active_recoveries_lock:
        _active_recoveries.clear()


def recover_stream(stream_id: str, dead_vpn: Optional[str] = None, failure_reason: Optional[str] = None):
    """
    Background recovery task orchestration.
    Queries the global state to decouple from dead engines, finds a healthy replacement, 
    and issues a migration payload to the proxy for Perfect Splice recovery.
    """
    with _active_recoveries_lock:
        if stream_id in _active_recoveries:
            logger.debug(f"Recovery already in progress for stream {stream_id}. Ignoring duplicate trigger.")
            return
        _active_recoveries.add(stream_id)

    def _recovery_task():
        try:
            from ..services.state import state
            from ..proxy.manager import ProxyManager
            from ..infrastructure.engine_selection import select_best_engine

            # Wait for state to synchronize globally.
            stream_state = None
            for _ in range(40):  # 40 * 0.05s = 2.0s timeout
                stream_state = state.get_stream(stream_id)
                if stream_state and stream_state.status == "pending_failover":
                    break
                time.sleep(0.05)

            if not stream_state:
                logger.warning(
                    f"Recovery failed: Stream {stream_id} not found in state after 2.0s synchronization wait."
                )
                return

            if stream_state.status != "pending_failover":
                logger.info(
                    f"Stream {stream_id} did not reach pending failover within 2.0s "
                    f"(status: {stream_state.status}). Aborting recovery."
                )
                return

            dead_container_id = stream_state.container_id
            normalized_reason = str(failure_reason or "unknown").strip().lower()
            source_instability = _is_source_instability_reason(normalized_reason)
            
            # Only blacklist the VPN if it was explicitly marked as dead by the Control Plane
            resolved_dead_vpn = dead_vpn
            
            logger.info(
                "Initiating Control Plane recovery for stream %s (previous engine: %s, previous VPN: %s, reason: %s)",
                stream_id,
                dead_container_id,
                resolved_dead_vpn or "N/A",
                normalized_reason or "unknown",
            )

            now = time.monotonic()
            if source_instability and not resolved_dead_vpn:
                eof_count = _record_source_failure(stream_id, stream_state.key, dead_container_id, now)
                if eof_count >= EOF_FAILOVER_BUDGET:
                    _mark_source_unstable(stream_id, dead_container_id, normalized_reason)
                    return

                cooldown_remaining = _get_cooldown_remaining(stream_id, now)
                if cooldown_remaining > 0.0:
                    logger.info(
                        "Applying source-instability cooldown for stream %s (%.1fs remaining) before migration.",
                        stream_id,
                        cooldown_remaining,
                    )
                    # Intentional per-stream rate limiting: each recovery runs in
                    # its own thread, so waiting here only slows repeated failovers
                    # for this stream and prevents rapid ping-pong migrations.
                    threading.Event().wait(min(cooldown_remaining, MAX_COOLDOWN_SLEEP_S))

            # Try to select a new engine, heavily penalizing the dead one
            penalties = {dead_container_id: 999} if dead_container_id else {}
            if source_instability and stream_state.key:
                penalties.update(_recent_pair_penalties(stream_state.key, now))
            new_engine = None
            max_retries = 15
            
            for attempt in range(max_retries):
                try:
                    new_engine, _ = select_best_engine(
                        additional_load_by_engine=penalties,
                        exclude_vpn=resolved_dead_vpn
                    )
                except Exception as e:
                    logger.warning(f"Engine selection failed for stream {stream_id} (attempt {attempt + 1}/{max_retries}): {e}. Waiting for capacity...")
                    time.sleep(2.0)
                    continue
                
                logger.info(f"Selected new engine {new_engine.container_id} for stream {stream_id}. Triggering migration API...")
                
                # Instruct the proxy to hot-swap to the new engine via the ProxyManager facade
                migration_result = ProxyManager.migrate_stream(stream_state.key, new_engine)

                if migration_result.get("migrated"):
                    logger.info(f"Successfully migrated stream {stream_id} to engine {new_engine.container_id}.")
                    
                    # ProxyManager handles calling state.reassign_active_streams_to_engine_by_key
                    # automatically so we just need to ensure the stream status is flipped back to started.
                    with state._lock:
                        st = state.streams.get(stream_id)
                        if st:
                            st.status = "started"
                            # DB synchronization will occur async or on next stats payload
                            def db_work(session):
                                from ..models.db_models import StreamRow
                                row = session.get(StreamRow, stream_id)
                                if row:
                                    row.status = "started"
                            state._enqueue_db_task(db_work)
                    return  # Success, exit the thread
                else:
                    reason = migration_result.get("reason", "unknown")
                    logger.warning(f"Migration failed for stream {stream_id} on engine {new_engine.container_id} (attempt {attempt + 1}/{max_retries}): {reason}. Retrying...")
                    # Apply a soft penalty to the engine so we might explore others if available, 
                    # but we can still re-select it if it's the only one (waiting for it to boot).
                    penalties[new_engine.container_id] = penalties.get(new_engine.container_id, 0) + 1
                    time.sleep(2.0)

            # If we exhaust the loop:
            logger.error(f"Exhausted all retries waiting for a replacement engine for stream {stream_id}.")
            from ..models.schemas import StreamEndedEvent
            from ..data_plane.internal_events import handle_stream_ended
            handle_stream_ended(StreamEndedEvent(stream_id=stream_id, container_id=dead_container_id, reason="failover_exhausted"))
                
        except Exception as e:
            logger.error(f"Error during stream recovery task for {stream_id}: {e}", exc_info=True)
        finally:
            with _active_recoveries_lock:
                _active_recoveries.discard(stream_id)

    thread = threading.Thread(target=_recovery_task, name=f"recovery-{stream_id[:8]}", daemon=True)
    thread.start()
