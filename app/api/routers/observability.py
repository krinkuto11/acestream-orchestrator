"""Observability endpoints: metrics, health, orchestrator status, events, cache, M3U."""
import asyncio
import io
import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from starlette.responses import Response

from ...core.config import cfg
from ...api.auth import require_api_key
from ...observability.event_logger import event_logger
from ...control_plane.health_manager import health_manager
from ...observability.metrics import update_custom_metrics
from ...persistence.cache import get_cache, invalidate_cache
from ...api.sse_helpers import (
    _format_sse_message,
    _validate_sse_api_key,
    _serialize_event_row,
    _build_events_sse_payload,
    _build_sse_payload,
)

try:
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics")
def get_metrics():
    """Prometheus metrics endpoint with custom aggregated metrics."""
    update_custom_metrics()
    if _PROMETHEUS_AVAILABLE:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    return Response(content="# prometheus_client not available\n", media_type="text/plain")


@router.get("/metrics/dashboard")
def get_dashboard_metrics_snapshot(
    window_seconds: int = Query(cfg.DASHBOARD_DEFAULT_WINDOW_S, ge=60, le=604800),
    max_points: int = Query(360, ge=30, le=2000),
):
    """Get structured advanced metrics for the pane-based dashboard."""
    return update_custom_metrics(window_seconds=window_seconds, max_points=max_points)


@router.get("/metrics/performance")
def get_performance_metrics(
    operation: Optional[str] = Query(None, description="Filter by operation name"),
    window: Optional[int] = Query(None, description="Time window in seconds"),
):
    """Get performance metrics for system operations."""
    from ...observability.performance_metrics import performance_metrics

    if operation:
        stats = {operation: performance_metrics.get_stats(operation, window)}
    else:
        stats = performance_metrics.get_all_stats(window)

    return {
        "window_seconds": window or "all",
        "operations": stats,
    }


@router.get("/health/status")
def get_health_status_endpoint():
    """Get detailed health status and management information."""
    return health_manager.get_health_summary()


@router.post("/health/circuit-breaker/reset", dependencies=[Depends(require_api_key)])
def reset_circuit_breaker(operation_type: Optional[str] = None):
    """Reset circuit breakers (for manual intervention)."""
    from ...control_plane.circuit_breaker import circuit_breaker_manager

    circuit_breaker_manager.force_reset(operation_type)
    return {
        "message": f"Circuit breaker {'for ' + operation_type if operation_type else 'all'} reset successfully"
    }


@router.get("/orchestrator/status")
def get_orchestrator_status():
    """Get comprehensive orchestrator status for proxy integration (cached for 0.5 seconds)."""
    cache = get_cache()
    cache_key = "orchestrator:status"

    cached_value = cache.get(cache_key)
    if cached_value is not None:
        return cached_value

    from ...services.state import state
    from ...control_plane.replica_validator import replica_validator
    from ...control_plane.circuit_breaker import circuit_breaker_manager
    from ...observability.metrics import get_dashboard_snapshot
    from ...vpn.gluetun import get_vpn_status
    from ...infrastructure.engine_config import detect_platform

    engines = state.list_engines()
    active_streams = state.list_streams(status="started")
    monitor_container_ids = state.get_active_monitor_container_ids()

    docker_status = replica_validator.get_docker_container_status()
    vpn_status = get_vpn_status()
    health_summary = health_manager.get_health_summary()
    circuit_breaker_status = circuit_breaker_manager.get_status()

    total_capacity = len(engines)
    engines_with_streams = len(
        set(stream.container_id for stream in active_streams).union(monitor_container_ids)
    )
    used_capacity = engines_with_streams
    available_capacity = max(0, total_capacity - used_capacity)

    vpn_enabled = vpn_status.get("enabled", False)
    vpn_connected = vpn_status.get("connected", False)
    circuit_breaker_state = circuit_breaker_status.get("general", {}).get("state")

    can_provision = True
    blocked_reason = None
    blocked_reason_details = None
    recovery_eta = None

    if vpn_enabled and not vpn_connected:
        can_provision = False
        blocked_reason = "VPN not connected"
        blocked_reason_details = {
            "code": "vpn_disconnected",
            "message": "VPN connection is required but currently disconnected. Engines cannot be provisioned without VPN.",
            "recovery_eta_seconds": 60,
            "can_retry": True,
            "should_wait": True,
        }
    elif circuit_breaker_state != "closed":
        can_provision = False
        cb_info = circuit_breaker_status.get("general", {})
        recovery_timeout = cb_info.get("recovery_timeout", 300)
        last_failure = cb_info.get("last_failure_time")

        if last_failure:
            try:
                last_failure_dt = datetime.fromisoformat(last_failure.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - last_failure_dt).total_seconds()
                recovery_eta = max(0, int(recovery_timeout - elapsed))
            except Exception:
                recovery_eta = recovery_timeout
        else:
            recovery_eta = recovery_timeout

        blocked_reason = f"Circuit breaker is {circuit_breaker_state}"
        blocked_reason_details = {
            "code": "circuit_breaker",
            "message": f"Provisioning circuit breaker is {circuit_breaker_state} due to repeated failures. System is waiting for conditions to improve.",
            "recovery_eta_seconds": recovery_eta,
            "can_retry": False if circuit_breaker_state == "open" else True,
            "should_wait": True,
        }
    elif docker_status["total_running"] >= cfg.MAX_REPLICAS:
        can_provision = False
        blocked_reason = "Maximum capacity reached"
        blocked_reason_details = {
            "code": "max_capacity",
            "message": f"Maximum number of engines ({cfg.MAX_REPLICAS}) already running. Wait for streams to end or increase MAX_REPLICAS.",
            "recovery_eta_seconds": cfg.ENGINE_GRACE_PERIOD_S if cfg.AUTO_DELETE else None,
            "can_retry": False,
            "should_wait": True,
        }

    if docker_status["total_running"] == 0:
        overall_status = "unavailable"
    elif not can_provision and blocked_reason_details and blocked_reason_details["code"] in [
        "vpn_disconnected",
        "circuit_breaker",
    ]:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    result = {
        "status": overall_status,
        "engines": {
            "total": len(engines),
            "running": docker_status["total_running"],
            "healthy": health_summary.get("healthy_engines", 0),
            "unhealthy": health_summary.get("unhealthy_engines", 0),
        },
        "streams": {
            "active": len(active_streams),
            "total": len(state.list_streams()),
        },
        "capacity": {
            "total": total_capacity,
            "used": used_capacity,
            "available": available_capacity,
            "max_replicas": cfg.MAX_REPLICAS,
            "min_replicas": cfg.MIN_REPLICAS,
        },
        "vpn": {
            "enabled": vpn_enabled,
            "connected": vpn_connected,
            "health": vpn_status.get("health", "unknown"),
            "container": vpn_status.get("container"),
            "forwarded_port": vpn_status.get("forwarded_port"),
        },
        "provisioning": {
            "can_provision": can_provision,
            "circuit_breaker_state": circuit_breaker_state,
            "last_failure": circuit_breaker_status.get("general", {}).get("last_failure_time"),
            "blocked_reason": blocked_reason,
            "blocked_reason_details": blocked_reason_details,
        },
        "config": {
            "auto_delete": cfg.AUTO_DELETE,
            "grace_period_s": cfg.ENGINE_GRACE_PERIOD_S,
            "engine_variant": f"global-{detect_platform()}",
            "debug_mode": cfg.DEBUG_MODE,
        },
        "proxy": get_dashboard_snapshot().get("proxy", {}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    cache.set(cache_key, result, ttl=0.5)
    return result


@router.get("/events")
def get_events(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of events to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    event_type: Optional[str] = Query(None, description="Filter by event type (engine, stream, vpn, health, system)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    container_id: Optional[str] = Query(None, description="Filter by container ID"),
    stream_id: Optional[str] = Query(None, description="Filter by stream ID"),
    since: Optional[datetime] = Query(None, description="Only return events after this timestamp"),
):
    """Retrieve application events with optional filtering."""
    from ...models.schemas import EventLog

    events = event_logger.get_events(
        limit=limit,
        offset=offset,
        event_type=event_type,
        category=category,
        container_id=container_id,
        stream_id=stream_id,
        since=since,
    )
    return [EventLog(**_serialize_event_row(e)) for e in events]


@router.get("/events/stats")
def get_event_stats():
    """Get statistics about logged events."""
    return event_logger.get_event_stats()


@router.post("/events/cleanup", dependencies=[Depends(require_api_key)])
def cleanup_events(
    max_age_days: int = Query(30, ge=1, description="Delete events older than this many days")
):
    """Manually trigger cleanup of old events."""
    deleted = event_logger.cleanup_old_events(max_age_days)
    return {"deleted": deleted, "message": f"Cleaned up {deleted} events older than {max_age_days} days"}


@router.get("/cache/stats")
def get_cache_stats():
    """Get cache statistics for monitoring and debugging."""
    cache = get_cache()
    return cache.get_stats()


@router.post("/cache/clear", dependencies=[Depends(require_api_key)])
def clear_cache():
    """Manually clear all cache entries."""
    cache = get_cache()
    cache.clear()
    return {"message": "Cache cleared successfully"}


@router.get("/engine-cache/stats", tags=["Cache"])
async def engine_cache_stats(api_key: str = Depends(require_api_key)):
    """Get current cache usage statistics."""
    from ...services.state import state

    return state.cache_stats


@router.post("/engine-cache/purge", tags=["Cache"])
async def purge_engine_cache(api_key: str = Depends(require_api_key)):
    """Manually purge all cache volume contents."""
    from ...infrastructure.engine_cache_manager import engine_cache_manager

    await engine_cache_manager.purge_all_contents()
    return {"status": "success", "message": "All cache volume contents purged"}


@router.get("/modify_m3u", tags=["M3U"])
async def modify_m3u(
    m3u_url: str = Query(..., description="URL of the source M3U playlist"),
    host: str = Query(..., description="Replacement hostname or IP address"),
    port: str = Query(..., description="Replacement port (1-65535)"),
    timeout: Optional[float] = Query(None, description="HTTP request timeout in seconds"),
    mode: str = Query("default", description="Rewrite mode: 'default' or 'proxy'"),
):
    """Download an M3U playlist and rewrite its internal URLs."""
    from ...api.m3u import get_m3u_content, validate_host_port, modify_m3u_content

    if mode not in ("default", "proxy"):
        raise HTTPException(status_code=400, detail="Parameter 'mode' must be 'default' or 'proxy'.")

    effective_timeout: float
    if timeout is not None:
        if timeout <= 0:
            raise HTTPException(status_code=400, detail="Parameter 'timeout' must be a positive number.")
        effective_timeout = timeout
    else:
        effective_timeout = cfg.M3U_TIMEOUT

    ok, port_or_msg = validate_host_port(host.strip(), port.strip())
    if not ok:
        raise HTTPException(status_code=400, detail=port_or_msg)
    validated_port: int = port_or_msg  # type: ignore[assignment]

    content = get_m3u_content(m3u_url.strip(), effective_timeout)
    if content is None:
        raise HTTPException(status_code=400, detail="Failed to download the M3U file.")

    modified = modify_m3u_content(content, host.strip(), validated_port, mode)

    return StreamingResponse(
        io.BytesIO(modified.encode("utf-8")),
        media_type="application/x-mpegURL",
        headers={"Content-Disposition": 'attachment; filename="modified_playlist.m3u"'},
    )


# ---- SSE endpoints ----

@router.get("/api/v1/events/stream")
async def stream_realtime_events(request: Request, api_key: Optional[str] = Query(None)):
    """Server-Sent Events endpoint for low-latency dashboard updates."""
    _validate_sse_api_key(request, api_key)

    from ...services.state import state

    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    loop = asyncio.get_running_loop()

    def _on_state_change(event: Dict[str, object]):
        def _enqueue():
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(dict(event))

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_enqueue)

    unsubscribe = state.subscribe_state_changes(_on_state_change)

    async def _event_generator():
        try:
            initial_payload = {
                "type": "full_sync",
                "payload": _build_sse_payload(),
                "meta": {
                    "reason": "initial_sync",
                    "seq": state.get_state_change_seq(),
                },
            }
            yield _format_sse_message(
                initial_payload,
                event_name="full_sync",
                event_id=str(state.get_state_change_seq()),
            )

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                message = {
                    "type": "full_sync",
                    "payload": _build_sse_payload(),
                    "meta": {
                        "reason": event.get("change_type"),
                        "seq": event.get("seq"),
                        "at": event.get("at"),
                    },
                }
                yield _format_sse_message(
                    message,
                    event_name="full_sync",
                    event_id=str(event.get("seq") or ""),
                )
        finally:
            unsubscribe()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@router.get("/api/v1/events/live")
async def stream_live_event_log(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    event_type: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for event log updates used by the Events page."""
    _validate_sse_api_key(request, api_key)

    queue: asyncio.Queue = asyncio.Queue(maxsize=16)
    loop = asyncio.get_running_loop()

    def _on_event(event: Dict[str, object]):
        if event_type and str(event.get("event_type") or "") != event_type:
            return

        def _enqueue():
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(dict(event))

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_enqueue)

    unsubscribe = event_logger.subscribe(_on_event)

    async def _event_generator():
        try:
            initial_payload = {
                "type": "events_snapshot",
                "payload": _build_events_sse_payload(limit=limit, event_type=event_type),
                "meta": {"reason": "initial_sync"},
            }
            yield _format_sse_message(initial_payload, event_name="events_snapshot")

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                message = {
                    "type": "events_snapshot",
                    "payload": _build_events_sse_payload(limit=limit, event_type=event_type),
                    "meta": {
                        "reason": "event_logged",
                        "seq": event.get("seq"),
                        "event_type": event.get("event_type"),
                    },
                }
                yield _format_sse_message(
                    message,
                    event_name="events_snapshot",
                    event_id=str(event.get("seq") or ""),
                )
        finally:
            unsubscribe()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@router.get("/api/v1/metrics/stream")
async def stream_live_metrics(
    request: Request,
    window_seconds: int = Query(cfg.DASHBOARD_DEFAULT_WINDOW_S, ge=60, le=604800),
    max_points: int = Query(360, ge=30, le=2000),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for dashboard metrics snapshots."""
    _validate_sse_api_key(request, api_key)

    async def _event_generator():
        while True:
            if await request.is_disconnected():
                break

            payload = update_custom_metrics(window_seconds=window_seconds, max_points=max_points)
            message = {
                "type": "metrics_snapshot",
                "payload": jsonable_encoder(payload),
                "meta": {
                    "window_seconds": window_seconds,
                    "max_points": max_points,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            yield _format_sse_message(message, event_name="metrics_snapshot")

            await asyncio.sleep(2.0)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@router.get("/api/v1/custom-variant/reprovision/status/stream")
@router.get("/api/v1/settings/engine/reprovision/status/stream")
async def stream_reprovision_status(
    request: Request,
    interval_seconds: float = Query(1.0, ge=0.5, le=10.0),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for engine reprovision status."""
    _validate_sse_api_key(request, api_key)

    async def _event_generator():
        last_digest: Optional[str] = None
        while True:
            if await request.is_disconnected():
                break

            from ..routers.provisioning import get_reprovision_status
            payload = jsonable_encoder(get_reprovision_status())
            digest = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)

            if digest != last_digest:
                last_digest = digest
                message = {
                    "type": "reprovision_status",
                    "payload": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="reprovision_status")
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(interval_seconds)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)
