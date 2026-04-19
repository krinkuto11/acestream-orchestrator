"""SSE helper functions shared across router modules."""
from contextlib import suppress
from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import json

from ..core.config import cfg
from ..observability.event_logger import event_logger
from ..infrastructure.docker_stats_collector import docker_stats_collector


def _format_sse_message(payload: Dict[str, Any], *, event_name: Optional[str] = None, event_id: Optional[str] = None) -> str:
    chunks: List[str] = []
    if event_name:
        chunks.append(f"event: {event_name}\n")
    if event_id:
        chunks.append(f"id: {event_id}\n")

    data = json.dumps(payload, separators=(",", ":"), default=str)
    for line in data.splitlines() or [data]:
        chunks.append(f"data: {line}\n")
    chunks.append("\n")
    return "".join(chunks)


def _build_sse_payload() -> Dict[str, Any]:
    from ..services.state import state
    from ..vpn.gluetun import get_vpn_status

    engines = state.list_engines()
    streams = state.list_streams_with_stats(status="started")
    pending_failover_streams = state.list_streams_with_stats(status="pending_failover")
    seen_stream_ids = {str(getattr(stream, "id", "")) for stream in streams}
    streams = streams + [
        stream for stream in pending_failover_streams
        if str(getattr(stream, "id", "")) not in seen_stream_ids
    ]
    engine_docker_stats = docker_stats_collector.get_all_stats()

    total_peers = 0
    total_speed_down = 0
    total_speed_up = 0

    # Pre-fetch tracker for efficiency (but only if we have active streams)
    active_stream_keys = [s.key for s in streams if getattr(s, "key", None)]
    from ..data_plane.client_tracker import client_tracking_service

    for stream in streams:
        # Populate clients for dashboard runway calculation
        if stream.status == "started" and getattr(stream, "key", None):
            with suppress(Exception):
                stream.clients = client_tracking_service.get_stream_clients(stream.key)

        try:
            total_peers += int(stream.peers or 0)
        except Exception:
            pass
        try:
            total_speed_down += int(stream.speed_down or 0)
        except Exception:
            pass
        try:
            total_speed_up += int(stream.speed_up or 0)
        except Exception:
            pass

    try:
        vpn_status = get_vpn_status()
    except Exception:
        vpn_status = {"enabled": False}

    try:
        from ..persistence.cache import get_cache as _get_cache
        _cache = _get_cache()
        orchestrator_status = _cache.get("orchestrator:status")
        if orchestrator_status is None:
            # Re-compute if not cached
            import importlib as _il
            _main = _il.import_module("app.main")
            orchestrator_status = _main.get_orchestrator_status()
    except Exception:
        orchestrator_status = None

    return {
        "engines": jsonable_encoder(engines),
        "engine_docker_stats": jsonable_encoder(engine_docker_stats),
        "streams": jsonable_encoder(streams),
        "vpn_status": jsonable_encoder(vpn_status),
        "orchestrator_status": jsonable_encoder(orchestrator_status),
        "kpis": {
            "total_engines": len(engines),
            "active_streams": len(streams),
            "healthy_engines": sum(1 for e in engines if (e.health_status or "").lower() == "healthy"),
            "total_peers": total_peers,
            "total_speed_down": total_speed_down,
            "total_speed_up": total_speed_up,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _validate_sse_api_key(request: Request, api_key: Optional[str]):
    """Validate SSE clients where EventSource cannot send custom auth headers."""
    if not cfg.API_KEY:
        return

    token = (api_key or "").strip()
    if not token:
        authorization = str(request.headers.get("Authorization") or "")
        if authorization.startswith("Bearer "):
            token = authorization.split(" ", 1)[1].strip()

    if token != cfg.API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid SSE API key")


def _serialize_event_row(event_row) -> Dict[str, Any]:
    timestamp = event_row.timestamp
    if timestamp and getattr(timestamp, "tzinfo", None) is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    return {
        "id": event_row.id,
        "timestamp": timestamp,
        "event_type": event_row.event_type,
        "category": event_row.category,
        "message": event_row.message,
        "details": event_row.details or {},
        "container_id": event_row.container_id,
        "stream_id": event_row.stream_id,
    }


def _build_events_sse_payload(limit: int, event_type: Optional[str]) -> Dict[str, Any]:
    rows = event_logger.get_events(limit=limit, event_type=event_type)
    return {
        "events": jsonable_encoder([_serialize_event_row(row) for row in rows]),
        "stats": jsonable_encoder(event_logger.get_event_stats()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
