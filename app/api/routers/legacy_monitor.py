"""Legacy stream monitoring endpoints."""
import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...api.auth import require_api_key
from ...api.sse_helpers import _format_sse_message, _validate_sse_api_key

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LegacyStreamMonitorStartRequest(BaseModel):
    monitor_id: Optional[str] = None
    content_id: str
    stream_name: Optional[str] = None
    live_delay: Optional[int] = None
    interval_s: float = 1.0
    run_seconds: int = 0
    per_sample_timeout_s: float = 1.0
    engine_container_id: Optional[str] = None


class LegacyStreamMonitorM3UParseRequest(BaseModel):
    m3u_content: str


class StreamSeekRequest(BaseModel):
    target_timestamp: int


class StreamSaveRequest(BaseModel):
    path: str
    index: int = 0
    infohash: Optional[str] = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _resolve_live_delay(seekback: Optional[int], live_delay: Optional[int]) -> int:
    from ...core.config import cfg
    from ...api.routers.proxy_routes import _normalize_seekback
    if live_delay is not None:
        return _normalize_seekback(live_delay)
    if seekback is not None:
        return _normalize_seekback(seekback)
    return _normalize_seekback(cfg.ACE_LIVE_EDGE_DELAY)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/ace/monitor/legacy/start", dependencies=[Depends(require_api_key)])
async def start_legacy_stream_monitor(req: LegacyStreamMonitorStartRequest):
    """Start a background legacy API monitor that collects STATUS every interval."""
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

    try:
        resolved_live_delay = _resolve_live_delay(None, req.live_delay)
        monitor = await legacy_stream_monitoring_service.start_monitor(
            content_id=req.content_id,
            stream_name=req.stream_name,
            live_delay=resolved_live_delay,
            interval_s=req.interval_s,
            run_seconds=req.run_seconds,
            per_sample_timeout_s=req.per_sample_timeout_s,
            engine_container_id=req.engine_container_id,
            monitor_id=req.monitor_id,
        )
        return monitor
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/ace/monitor/legacy/parse-m3u", dependencies=[Depends(require_api_key)])
async def parse_legacy_monitor_m3u(req: LegacyStreamMonitorM3UParseRequest):
    """Parse M3U content and extract acestream IDs with stream names."""
    from ...api.m3u import parse_acestream_m3u_entries

    content = (req.m3u_content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="m3u_content is required")

    entries = parse_acestream_m3u_entries(content)
    return {
        "count": len(entries),
        "items": entries,
    }


@router.get("/ace/monitor/legacy", dependencies=[Depends(require_api_key)])
async def list_legacy_stream_monitors(
    include_recent_status: bool = Query(
        True,
        description="Include recent_status history in each monitor item. Set false to return latest_status-only summaries.",
    )
):
    """List all legacy monitoring sessions and their latest STATUS sample."""
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

    return {
        "items": await legacy_stream_monitoring_service.list_monitors(
            include_recent_status=include_recent_status,
        )
    }


@router.get("/ace/monitor/legacy/{monitor_id}", dependencies=[Depends(require_api_key)])
async def get_legacy_stream_monitor(
    monitor_id: str,
    include_recent_status: bool = Query(
        True,
        description="Include recent_status history. Set false to return latest_status-only summary for this monitor.",
    ),
):
    """Get a single legacy monitoring session including recent STATUS history."""
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

    monitor = await legacy_stream_monitoring_service.get_monitor(
        monitor_id,
        include_recent_status=include_recent_status,
    )
    if not monitor:
        raise HTTPException(status_code=404, detail="legacy monitor not found")
    return monitor


@router.delete("/ace/monitor/legacy/{monitor_id}", dependencies=[Depends(require_api_key)])
async def stop_legacy_stream_monitor(monitor_id: str):
    """Stop a legacy monitoring session and close its API connection."""
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

    stopped = await legacy_stream_monitoring_service.stop_monitor(monitor_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="legacy monitor not found")
    return {"stopped": True, "monitor_id": monitor_id}


@router.delete("/ace/monitor/legacy/{monitor_id}/entry", dependencies=[Depends(require_api_key)])
async def delete_legacy_stream_monitor(monitor_id: str):
    """Delete a legacy monitoring entry and ensure its API session is stopped."""
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

    deleted = await legacy_stream_monitoring_service.delete_monitor(monitor_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="legacy monitor not found")
    return {"deleted": True, "monitor_id": monitor_id}


@router.get("/api/v1/ace/monitor/legacy/stream")
async def stream_legacy_monitor_sessions(
    request: Request,
    include_recent_status: bool = Query(
        True,
        description="Include recent_status history in monitor payloads.",
    ),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for legacy monitor session updates."""
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

    _validate_sse_api_key(request, api_key)

    queue: asyncio.Queue = asyncio.Queue(maxsize=24)
    loop = asyncio.get_running_loop()

    def _shape_event_payload(event: Dict[str, object]) -> Dict[str, object]:
        payload = dict(event or {})
        monitor_payload = payload.get("monitor")
        if include_recent_status or not isinstance(monitor_payload, dict):
            return payload

        compact_monitor = dict(monitor_payload)
        compact_monitor.pop("recent_status", None)
        payload["monitor"] = compact_monitor
        return payload

    def _on_update(event: Dict[str, object]):
        shaped = _shape_event_payload(event)

        def _enqueue():
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(shaped)

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_enqueue)

    unsubscribe = legacy_stream_monitoring_service.subscribe_updates(_on_update)

    async def _event_generator():
        try:
            initial_items = await legacy_stream_monitoring_service.list_monitors(
                include_recent_status=include_recent_status,
            )
            initial_payload = {
                "type": "legacy_monitor_snapshot",
                "payload": {
                    "items": jsonable_encoder(initial_items),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "meta": {"reason": "initial_sync"},
            }
            yield _format_sse_message(initial_payload, event_name="legacy_monitor_snapshot")

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                message = {
                    "type": "legacy_monitor_event",
                    "payload": jsonable_encoder(event),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(
                    message,
                    event_name="legacy_monitor_event",
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
