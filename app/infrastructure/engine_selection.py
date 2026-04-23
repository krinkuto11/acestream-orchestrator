from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from ..services.state import state
from ..core.config import cfg
from ..models.schemas import EngineState

logger = logging.getLogger(__name__)


def _get_proxy_redis_client() -> Any:
    try:
        from ..shared.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bytes):
            return int(value.decode("utf-8"))
        return int(value)
    except Exception:
        return default


def select_best_engine(
    requested_container_id: Optional[str] = None,
    additional_load_by_engine: Optional[Dict[str, int]] = None,
    reserve_pending: bool = False,
    not_found_error: str = "engine_not_found",
    exclude_vpn: Optional[str] = None,
) -> Tuple[EngineState, int]:
    """Select the best available engine using the same proxy balancing algorithm.

    Returns tuple of (selected_engine, current_load).
    Raises HTTPException if no engines available or all at capacity.
    """
    engines = state.list_engines()
    if not engines:
        raise HTTPException(status_code=503, detail="No engines available")

    engines = [
        engine for engine in engines 
        if not state.is_engine_draining(engine.container_id)
        and (exclude_vpn is None or engine.vpn_container != exclude_vpn)
    ]
    if not engines:
        raise HTTPException(status_code=503, detail="No non-draining engines available (or all excluded by VPN restriction)")

    if requested_container_id:
        selected = next((e for e in engines if e.container_id == requested_container_id), None)
        if not selected:
            raise HTTPException(status_code=404, detail=not_found_error)
        return selected, 0

    redis = _get_proxy_redis_client()

    active_streams = state.list_streams(status="started")
    engine_loads: Dict[str, int] = {}
    for stream in active_streams:
        cid = stream.container_id
        engine_loads[cid] = engine_loads.get(cid, 0) + 1

    # Count active monitoring sessions as stream load on each engine.
    monitor_loads = state.get_active_monitor_load_by_engine()
    for cid, monitor_count in monitor_loads.items():
        engine_loads[cid] = engine_loads.get(cid, 0) + monitor_count

    for e in engines:
        cid = e.container_id
        pending_count = 0
        if redis:
            try:
                pending_key = f"ace_proxy:engine:{cid}:pending"
                pending_count = _to_int(redis.get(pending_key), 0)
            except Exception:
                pending_count = 0
        engine_loads[cid] = engine_loads.get(cid, 0) + pending_count

    if additional_load_by_engine:
        for cid, extra in additional_load_by_engine.items():
            engine_loads[cid] = engine_loads.get(cid, 0) + max(0, int(extra or 0))

    max_streams = cfg.MAX_STREAMS_PER_ENGINE
    available = [e for e in engines if engine_loads.get(e.container_id, 0) < max_streams]

    if not available:
        raise HTTPException(
            status_code=503,
            detail=f"All engines at maximum capacity ({max_streams} streams per engine)",
        )

    engines_sorted = sorted(
        available,
        key=lambda e: (
            engine_loads.get(e.container_id, 0),
            not e.forwarded,
        ),
    )
    selected = engines_sorted[0]
    current_load = engine_loads.get(selected.container_id, 0)

    if reserve_pending and redis:
        try:
            pending_key = f"ace_proxy:engine:{selected.container_id}:pending"
            remaining_capacity = int(max_streams) - int(current_load)
            if remaining_capacity <= 0:
                raise HTTPException(
                    status_code=503,
                    detail=f"All engines at maximum capacity ({max_streams} streams per engine)",
                )

            reserve_script = """
            local key = KEYS[1]
            local cap = tonumber(ARGV[1])
            local ttl = tonumber(ARGV[2])
            local cur = tonumber(redis.call('GET', key) or '0')
            if cur >= cap then
                return -1
            end
            local updated = redis.call('INCR', key)
            redis.call('EXPIRE', key, ttl)
            return updated
            """
            reserve_result = int(redis.eval(reserve_script, 1, pending_key, remaining_capacity, 15))
            if reserve_result < 0:
                raise HTTPException(
                    status_code=503,
                    detail=f"All engines at maximum capacity ({max_streams} streams per engine)",
                )
            logger.debug(
                "Atomically reserved engine %s pending count",
                selected.container_id[:12],
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(
                "Failed to set Redis pending reservation for engine %s: %s",
                selected.container_id[:12],
                e,
            )

    return selected, current_load
