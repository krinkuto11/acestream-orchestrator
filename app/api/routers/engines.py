"""Engines and container endpoints."""
import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from docker.errors import NotFound

from ...core.config import cfg
from ...api.auth import require_api_key
from ...services.state import state
from ...infrastructure.docker_stats_collector import docker_stats_collector
from ...infrastructure.inspect import inspect_container, ContainerNotFound
from ...models.schemas import EngineState
from ...api.sse_helpers import _format_sse_message, _validate_sse_api_key

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/engines", response_model=List[EngineState])
def get_engines():
    """Get all engines with Docker verification and VPN health filtering."""
    engines = state.list_engines()

    def _to_int(value: Any) -> int:
        if value is None:
            return 0
        try:
            text = str(value).strip()
            if text == "":
                return 0
            return int(float(text))
        except Exception:
            return 0

    def _build_engine_runtime_metrics() -> Dict[str, Dict[str, int]]:
        from ...services.state import ACTIVE_MONITOR_SESSION_STATUSES

        metrics: Dict[str, Dict[str, int]] = {}

        def _entry(container_id: str) -> Dict[str, int]:
            return metrics.setdefault(
                container_id,
                {
                    "active_stream_count": 0,
                    "monitor_stream_count": 0,
                    "stream_peers": 0,
                    "stream_speed_down": 0,
                    "stream_speed_up": 0,
                    "monitor_peers": 0,
                    "monitor_speed_down": 0,
                    "monitor_speed_up": 0,
                },
            )

        for stream in state.list_streams_with_stats(status="started"):
            cid = stream.container_id
            item = _entry(cid)
            item["active_stream_count"] += 1
            item["stream_peers"] += _to_int(stream.peers)
            item["stream_speed_down"] += _to_int(stream.speed_down)
            item["stream_speed_up"] += _to_int(stream.speed_up)

        for monitor in state.list_monitor_sessions():
            monitor_status = str(monitor.get("status") or "").strip().lower()
            if monitor_status not in ACTIVE_MONITOR_SESSION_STATUSES:
                continue

            engine_data = monitor.get("engine") or {}
            cid = str(engine_data.get("container_id") or "").strip()
            if not cid:
                continue

            latest_status = monitor.get("latest_status") or {}
            item = _entry(cid)
            item["monitor_stream_count"] += 1
            item["monitor_peers"] += _to_int(latest_status.get("peers") or latest_status.get("http_peers"))
            item["monitor_speed_down"] += _to_int(
                latest_status.get("speed_down") or latest_status.get("http_speed_down")
            )
            item["monitor_speed_up"] += _to_int(latest_status.get("speed_up"))

        aggregated: Dict[str, Dict[str, int]] = {}
        for cid, item in metrics.items():
            aggregated[cid] = {
                "total_peers": item["stream_peers"] + item["monitor_peers"],
                "total_speed_down": item["stream_speed_down"] + item["monitor_speed_down"],
                "total_speed_up": item["stream_speed_up"] + item["monitor_speed_up"],
                "stream_count": item["active_stream_count"] + item["monitor_stream_count"],
                "monitor_stream_count": item["monitor_stream_count"],
                "monitor_speed_down": item["monitor_speed_down"],
                "monitor_speed_up": item["monitor_speed_up"],
            }

        return aggregated

    runtime_metrics = _build_engine_runtime_metrics()

    try:
        from ...control_plane.health import list_managed
        from ...vpn.gluetun import gluetun_monitor, get_forwarded_port_sync
        from ...infrastructure.engine_info import get_engine_version_info_sync

        managed_containers = list_managed()
        running_containers = [c for c in managed_containers if c.status == "running"]
        running_container_ids = {c.id for c in running_containers}

        container_started_at = {}
        for c in running_containers:
            started_at = None
            try:
                started_at = (c.attrs or {}).get("State", {}).get("StartedAt")
            except Exception:
                started_at = None
            container_started_at[c.id] = str(started_at or "unknown")

        vpn_health_cache = {}

        verified_engines = []
        for engine in engines:
            if engine.container_id not in running_container_ids:
                logger.debug(f"Engine {engine.container_id[:12]} not found in Docker, but keeping in response")
                verified_engines.append(engine)
                continue

            if engine.vpn_container:
                if engine.vpn_container not in vpn_health_cache:
                    vpn_health_cache[engine.vpn_container] = gluetun_monitor.is_healthy(engine.vpn_container)

                vpn_healthy = vpn_health_cache.get(engine.vpn_container)

                if vpn_healthy:
                    verified_engines.append(engine)
                else:
                    logger.debug(
                        f"Engine {engine.container_id[:12]} filtered out - VPN '{engine.vpn_container}' is unhealthy"
                    )
            else:
                verified_engines.append(engine)

        for engine in verified_engines:
            if not engine.engine_variant:
                from ...infrastructure.engine_config import detect_platform
                engine.engine_variant = f"global-{detect_platform()}"

            try:
                version_info = get_engine_version_info_sync(
                    engine.host,
                    engine.port,
                    cache_key=engine.container_id,
                    cache_revision=container_started_at.get(engine.container_id),
                )
                if version_info:
                    engine.platform = version_info.get("platform")
                    engine.version = version_info.get("version")
                else:
                    logger.debug(
                        f"No version info returned for engine {engine.container_id[:12]} at {engine.host}:{engine.port}"
                    )
            except Exception as e:
                logger.debug(
                    f"Could not get version info for engine {engine.container_id[:12]} at {engine.host}:{engine.port}: {e}"
                )

            if engine.forwarded and engine.vpn_container:
                try:
                    port = get_forwarded_port_sync(engine.vpn_container)
                    if port:
                        engine.forwarded_port = port
                    else:
                        logger.debug(
                            f"No forwarded port available for VPN {engine.vpn_container} (engine {engine.container_id[:12]})"
                        )
                except Exception as e:
                    logger.warning(
                        f"Could not get forwarded port for engine {engine.container_id[:12]} on VPN {engine.vpn_container}: {e}"
                    )

        enriched_engines = []
        for engine in verified_engines:
            metrics = runtime_metrics.get(
                engine.container_id,
                {
                    "total_peers": 0,
                    "total_speed_down": 0,
                    "total_speed_up": 0,
                    "stream_count": 0,
                    "monitor_stream_count": 0,
                    "monitor_speed_down": 0,
                    "monitor_speed_up": 0,
                },
            )
            enriched_engines.append(engine.model_copy(update=metrics))

        enriched_engines.sort(key=lambda e: e.port)
        return enriched_engines
    except Exception as e:
        logger.debug(f"Engine verification failed for /engines endpoint: {e}")
        enriched_engines = []
        for engine in engines:
            metrics = runtime_metrics.get(
                engine.container_id,
                {
                    "total_peers": 0,
                    "total_speed_down": 0,
                    "total_speed_up": 0,
                    "stream_count": 0,
                    "monitor_stream_count": 0,
                    "monitor_speed_down": 0,
                    "monitor_speed_up": 0,
                },
            )
            enriched_engines.append(engine.model_copy(update=metrics))

        enriched_engines.sort(key=lambda e: e.port)
        return enriched_engines


@router.get("/engines/with-metrics")
def get_engines_with_metrics():
    """Get all engines with aggregated stream metrics (peers, download/upload speeds)."""
    engines = get_engines()

    result = []
    for engine in engines:
        engine_dict = engine.model_dump()
        result.append(engine_dict)

    return result


@router.get("/engines/stats/all")
def get_all_engine_stats():
    """Get Docker stats for all engines from background collector (instant response)."""
    stats = docker_stats_collector.get_all_stats()
    return stats


@router.get("/engines/stats/total")
def get_total_engine_stats():
    """Get aggregated Docker stats across all engines from background collector (instant response)."""
    total_stats = docker_stats_collector.get_total_stats()
    return total_stats


@router.get("/engines/{container_id}/stats")
def get_engine_stats(container_id: str):
    """Get Docker stats for a specific engine from background collector (instant response)."""
    stats = docker_stats_collector.get_engine_stats(container_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Container not found or stats unavailable")
    return stats


@router.get("/engines/{container_id}")
def get_engine(container_id: str):
    eng = state.get_engine(container_id)
    if not eng:
        return {"error": "not found"}
    streams = state.list_streams(status="started", container_id=container_id)
    return {"engine": eng, "streams": streams}


@router.delete("/containers/{container_id}", dependencies=[Depends(require_api_key)])
def delete(container_id: str):
    from ...control_plane.provisioner import stop_container
    from ...observability.event_logger import event_logger
    from ...persistence.cache import invalidate_cache

    event_logger.log_event(
        event_type="engine",
        category="deleted",
        message=f"Engine deleted: {container_id[:12]}",
        container_id=container_id,
    )
    stop_container(container_id)
    invalidate_cache("orchestrator:status")
    return {"deleted": container_id}


@router.get("/containers/{container_id}")
def get_container(container_id: str):
    try:
        return inspect_container(container_id)
    except ContainerNotFound:
        raise HTTPException(status_code=404, detail="container not found")


def _fetch_container_logs_payload(
    container_id: str,
    *,
    tail: int,
    since_seconds: Optional[int],
    timestamps: bool,
) -> Dict[str, Any]:
    from ...infrastructure.docker_client import get_client

    client = get_client(timeout=20)
    container = client.containers.get(container_id)

    since = None
    if since_seconds is not None:
        since = int(time.time()) - since_seconds

    logs_raw = container.logs(
        stdout=True,
        stderr=True,
        tail=tail,
        since=since,
        timestamps=timestamps,
    )

    logs_text = (
        logs_raw.decode("utf-8", errors="replace")
        if isinstance(logs_raw, (bytes, bytearray))
        else str(logs_raw)
    )

    return {
        "container_id": container_id,
        "tail": tail,
        "since_seconds": since_seconds,
        "timestamps": timestamps,
        "logs": logs_text,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/containers/{container_id}/logs", dependencies=[Depends(require_api_key)])
def get_container_logs(
    container_id: str,
    tail: int = Query(200, ge=1, le=2000, description="Maximum number of recent log lines"),
    since_seconds: Optional[int] = Query(
        None,
        ge=1,
        le=86400,
        description="Return logs newer than this many seconds",
    ),
    timestamps: bool = Query(False, description="Include Docker timestamps in each log line"),
):
    """Get recent Docker logs for a container."""
    try:
        return _fetch_container_logs_payload(
            container_id,
            tail=tail,
            since_seconds=since_seconds,
            timestamps=timestamps,
        )
    except NotFound:
        raise HTTPException(status_code=404, detail="container not found")
    except Exception as exc:
        logger.error(f"Failed to fetch logs for container {container_id[:12]}: {exc}")
        raise HTTPException(status_code=500, detail=f"failed_to_fetch_logs: {exc}")


@router.get("/api/v1/containers/{container_id}/logs/stream")
async def stream_container_logs(
    request: Request,
    container_id: str,
    tail: int = Query(300, ge=1, le=2000),
    since_seconds: Optional[int] = Query(1200, ge=1, le=86400),
    timestamps: bool = Query(False),
    interval_seconds: float = Query(2.5, ge=0.5, le=10.0),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for near-real-time container logs updates."""
    _validate_sse_api_key(request, api_key)

    async def _event_generator():
        last_logs_text: Optional[str] = None

        while True:
            if await request.is_disconnected():
                break

            try:
                payload = _fetch_container_logs_payload(
                    container_id,
                    tail=tail,
                    since_seconds=since_seconds,
                    timestamps=timestamps,
                )
            except NotFound:
                message = {
                    "type": "container_logs_error",
                    "payload": {
                        "container_id": container_id,
                        "status_code": 404,
                        "detail": "container not found",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="container_logs_error")
                break
            except Exception as exc:
                logger.debug(f"Logs SSE fetch failed for {container_id[:12]}: {exc}")
                message = {
                    "type": "container_logs_error",
                    "payload": {
                        "container_id": container_id,
                        "status_code": 500,
                        "detail": f"failed_to_fetch_logs: {exc}",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="container_logs_error")
                await asyncio.sleep(interval_seconds)
                continue

            logs_text = str(payload.get("logs") or "")
            if logs_text != last_logs_text:
                last_logs_text = logs_text
                message = {
                    "type": "container_logs_snapshot",
                    "payload": jsonable_encoder(payload),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="container_logs_snapshot")
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(interval_seconds)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)
