"""Proxy endpoints: ace/getstream, HLS segments, stream control, proxy status/config."""
import asyncio
import hashlib
import logging
import re
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...core.config import cfg
from ...api.auth import require_api_key
from ...services.state import state
from ...observability.metrics import observe_proxy_request, observe_proxy_ttfb, observe_proxy_egress_bytes
from ...infrastructure.engine_selection import select_best_engine as select_best_engine_shared
from ...models.schemas import StreamState, StreamStartedEvent

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models (also defined in legacy_monitor.py for symmetry)
# ---------------------------------------------------------------------------



class TelemetryRequestEvent(BaseModel):
    mode: str
    endpoint: str
    duration_seconds: float
    success: bool
    status_code: int
    ttfb_seconds: float

class TelemetryBatchRequest(BaseModel):
    requests: list[TelemetryRequestEvent] = []
    connects: list[str] = []
    disconnects: list[str] = []


# ---------------------------------------------------------------------------
# Stream-input helpers (duplicated from main.py to avoid circular imports)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Stream control helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# /ace/preflight
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# /ace/getstream
# ---------------------------------------------------------------------------

# -- Legacy ace_getstream removed --

# -- Legacy api_hls_segment_file removed --


# ---------------------------------------------------------------------------
# Stream control: seek / pause / resume / save
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Proxy status / sessions / clients / debug
# ---------------------------------------------------------------------------



async def get_stream_clients(stream_key: str):
    """Get list of clients connected to a specific stream."""
    from ...data_plane.client_tracker import client_tracking_service
    try:
        clients = client_tracking_service.get_stream_clients(stream_key)
        return {"clients": clients}
    except Exception as e:
        logger.error(f"Error getting clients for stream {stream_key}: {e}")
        return {"clients": []}

@router.post("/internal/proxy/go/telemetry")
async def receive_go_proxy_telemetry(payload: TelemetryBatchRequest):
    """Receive batched RED telemetry from the Go Proxy."""
    from ...observability.metrics import observe_proxy_request, observe_proxy_ttfb, observe_proxy_client_connect, observe_proxy_client_disconnect
    
    for req in payload.requests:
        observe_proxy_request(req.mode, req.endpoint, req.duration_seconds, req.success, req.status_code)
        if req.ttfb_seconds > 0:
            observe_proxy_ttfb(req.mode, req.endpoint, req.ttfb_seconds)
            
    for mode in payload.connects:
        observe_proxy_client_connect(mode)
        
    for mode in payload.disconnects:
        observe_proxy_client_disconnect(mode)
        
    return {"status": "ok"}




# ---------------------------------------------------------------------------
# /proxy/config GET + POST
# ---------------------------------------------------------------------------

@router.get("/proxy/config")
def get_proxy_config():
    """Get current proxy configuration settings."""
    from ...infrastructure.engine_config import detect_platform
    from ...persistence.settings_persistence import SettingsPersistence

    persisted = SettingsPersistence.load_proxy_config() or {}
    
    # Base defaults that might not be in SettingsPersistence but are needed by the UI/Proxy
    base_config = {
        "vlc_user_agent": "AceStream-Orchestrator/1.0",
        "engine_variant": f"global-{detect_platform()}",
    }
    
    return {**base_config, **persisted}


def notify_proxy_config_update():
    """Publish the current proxy configuration to Redis for the Go proxy to consume."""
    import json
    from ...persistence.settings_persistence import SettingsPersistence
    from ...shared.redis_client import get_redis_client

    try:
        config = get_proxy_config()
        rdb = get_redis_client()
        # We publish the full config to the 'proxy_config_updates' channel
        rdb.publish("proxy_config_updates", json.dumps(config))
        logger.info("Published proxy configuration update to Redis")
        return True
    except Exception as e:
        logger.error(f"Failed to publish proxy configuration update: {e}")
        return False



@router.post("/proxy/config", dependencies=[Depends(require_api_key)])
def update_proxy_config(
    initial_data_wait_timeout: Optional[int] = None,
    initial_data_check_interval: Optional[float] = None,
    no_data_timeout_checks: Optional[int] = None,
    no_data_check_interval: Optional[float] = None,
    connection_timeout: Optional[int] = None,
    upstream_connect_timeout: Optional[int] = None,
    upstream_read_timeout: Optional[int] = None,
    stream_timeout: Optional[int] = None,
    channel_shutdown_delay: Optional[int] = None,
    proxy_prebuffer_seconds: Optional[int] = None,
    pacing_bitrate_multiplier: Optional[float] = None,
    max_streams_per_engine: Optional[int] = None,
    stream_mode: Optional[str] = None,
    control_mode: Optional[str] = None,
    legacy_api_preflight_tier: Optional[str] = None,
    ace_live_edge_delay: Optional[int] = None,
    hls_max_segments: Optional[int] = None,
    hls_initial_segments: Optional[int] = None,
    hls_window_size: Optional[int] = None,
    hls_buffer_ready_timeout: Optional[int] = None,
    hls_first_segment_timeout: Optional[int] = None,
    hls_initial_buffer_seconds: Optional[int] = None,
    hls_max_initial_segments: Optional[int] = None,
    hls_segment_fetch_interval: Optional[float] = None,
):
    """Update proxy configuration settings at runtime."""
    from ...persistence.settings_persistence import SettingsPersistence
    
    current = SettingsPersistence.load_proxy_config() or {}
    
    updates = {
        "initial_data_wait_timeout": initial_data_wait_timeout,
        "initial_data_check_interval": initial_data_check_interval,
        "no_data_timeout_checks": no_data_timeout_checks,
        "no_data_check_interval": no_data_check_interval,
        "connection_timeout": connection_timeout,
        "upstream_connect_timeout": upstream_connect_timeout,
        "upstream_read_timeout": upstream_read_timeout,
        "stream_timeout": stream_timeout,
        "channel_shutdown_delay": channel_shutdown_delay,
        "proxy_prebuffer_seconds": proxy_prebuffer_seconds,
        "pacing_bitrate_multiplier": pacing_bitrate_multiplier,
        "max_streams_per_engine": max_streams_per_engine,
        "stream_mode": stream_mode,
        "control_mode": control_mode,
        "legacy_api_preflight_tier": legacy_api_preflight_tier,
        "ace_live_edge_delay": ace_live_edge_delay,
        "hls_max_segments": hls_max_segments,
        "hls_initial_segments": hls_initial_segments,
        "hls_window_size": hls_window_size,
        "hls_buffer_ready_timeout": hls_buffer_ready_timeout,
        "hls_first_segment_timeout": hls_first_segment_timeout,
        "hls_initial_buffer_seconds": hls_initial_buffer_seconds,
        "hls_max_initial_segments": hls_max_initial_segments,
        "hls_segment_fetch_interval": hls_segment_fetch_interval,
    }
    
    # Filter out None values
    updates = {k: v for k, v in updates.items() if v is not None}
    
    if not updates:
        return {"message": "No updates provided", "config": current}
        
    merged = {**current, **updates}
    
    if SettingsPersistence.save_proxy_config(merged):
        logger.info("Proxy configuration updated and persisted")
        notify_proxy_config_update()
        return {"message": "Proxy configuration updated and persisted", "config": merged}
    else:
        raise HTTPException(status_code=500, detail="Failed to persist proxy configuration")



# ---------------------------------------------------------------------------
# Internal endpoint consumed by the Go proxy — engine selection
# ---------------------------------------------------------------------------

@router.get("/internal/proxy/select-engine")
def internal_select_engine():
    """Return the best available engine for the Go proxy to connect to."""
    from ...persistence.settings_persistence import SettingsPersistence
    
    proxy_settings = SettingsPersistence.load_proxy_config() or {}
    engine, _ = select_best_engine_shared()
    
    return {
        "host": engine.host,
        "port": engine.port,
        "api_port": engine.api_port or 62062,
        "container_id": engine.container_id,
        "proxy_prebuffer_seconds": int(proxy_settings.get("proxy_prebuffer_seconds", 3)),
        "pacing_bitrate_multiplier": float(proxy_settings.get("pacing_bitrate_multiplier", 1.5)),
        "stream_mode": str(proxy_settings.get("stream_mode", "TS")).upper(),
        "control_mode": str(proxy_settings.get("control_mode", "api")).lower(),
    }



# ---------------------------------------------------------------------------
# Lifecycle notifications from the Go proxy
# ---------------------------------------------------------------------------

@router.post("/internal/proxy/go/stream-started")
async def go_proxy_stream_started(request: Request):
    """Called by the Go proxy after it successfully connects to an engine.
    Registers the stream in the Python state so the GUI shows activity."""
    body = await request.json()
    try:
        from ...data_plane.internal_events import handle_stream_started

        evt = StreamStartedEvent(
            container_id=body.get("container_id") or None,
            engine={"host": body["engine_host"], "port": int(body["engine_port"])},
            stream={
                "key_type": body.get("key_type", "content_id"),
                "key": body["key"],
                "live_delay": int(body.get("live_delay", 0)),
            },
            session={
                "playback_session_id": body.get("playback_session_id") or body["key"],
                "stat_url": body.get("stat_url") or None,
                "command_url": body.get("command_url") or None,
                "is_live": int(body.get("is_live", 0)),
                "bitrate": int(body["bitrate"]) if body.get("bitrate") else None,
            },
            labels={"proxy": "go", **(body.get("labels") or {})},
        )
        result = await asyncio.to_thread(handle_stream_started, evt)
        stream_id = result.id if result else None
        logger.info("go_proxy_stream_started: registered stream_id=%s key=%s", stream_id, body.get("key"))
        return {"stream_id": stream_id}
    except Exception as e:
        logger.warning("go_proxy_stream_started failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/internal/proxy/go/stream-ended")
async def go_proxy_stream_ended(request: Request):
    """Called by the Go proxy when a stream stops."""
    body = await request.json()
    try:
        from ...data_plane.internal_events import handle_stream_ended
        from ...models.schemas import StreamEndedEvent

        evt = StreamEndedEvent(
            container_id=body.get("container_id") or None,
            stream_id=body.get("stream_id") or None,
            reason=body.get("reason") or "stopped",
        )
        await asyncio.to_thread(handle_stream_ended, evt)
        return {"ok": True}
    except Exception as e:
        logger.warning("go_proxy_stream_ended failed: %s", e, exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/internal/proxy/go/stream-stats")
async def go_proxy_stream_stats(request: Request):
    """Called by the Go proxy to push live stream statistics.

    For API-mode streams (no stat_url) this carries the full STATUS payload
    (peers, speed_down, speed_up, downloaded, uploaded) plus the ring-buffer
    measured bitrate.  For HTTP-mode streams only the measured bitrate is sent
    so the Python collector's engine stats are enriched without duplication.
    """
    body = await request.json()

    # Resolve stream_id — prefer the UUID returned by go_proxy_stream_started,
    # fall back to the content key so early pushes before registration succeed.
    stream_id: Optional[str] = body.get("stream_id") or None
    content_key: Optional[str] = body.get("content_key") or None

    if not stream_id and content_key:
        for s in state.list_streams(status="started"):
            if s.key == content_key:
                stream_id = s.id
                break

    if not stream_id:
        return {"ok": False, "reason": "stream not found"}

    from ...models.schemas import StreamStatSnapshot
    from datetime import datetime, timezone

    bitrate = body.get("bitrate")  # Go-measured ring-buffer bitrate (bytes/s)
    peers = body.get("peers")
    speed_down = body.get("speed_down")
    speed_up = body.get("speed_up")
    downloaded = body.get("downloaded")
    uploaded = body.get("uploaded")
    livepos = body.get("livepos")
    proxy_buffer_pieces = body.get("proxy_buffer_pieces")
    status_state = body.get("status") or body.get("state")

    has_full_stats = (peers is not None or speed_down is not None or livepos is not None)

    if has_full_stats:
        # API mode: create a full snapshot replacing what Python can't collect.
        snap = StreamStatSnapshot(
            ts=datetime.now(timezone.utc),
            peers=peers,
            speed_down=speed_down,
            speed_up=speed_up,
            downloaded=downloaded,
            uploaded=uploaded,
            status=status_state,
            bitrate=bitrate,
            livepos=livepos,
            proxy_buffer_pieces=proxy_buffer_pieces,
        )
        state.append_stat(stream_id, snap)
    elif bitrate is not None:
        # HTTP mode: carry forward existing peer/speed values from the latest
        # stat snapshot so we don't lose them, and inject the measured bitrate.
        existing = state.get_stream_stats(stream_id)
        if existing:
            latest = existing[-1]
            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=latest.peers,
                speed_down=latest.speed_down,
                speed_up=latest.speed_up,
                downloaded=latest.downloaded,
                uploaded=latest.uploaded,
                status=getattr(latest, "status", None),
                bitrate=bitrate,
                livepos=getattr(latest, "livepos", None),
                proxy_buffer_pieces=getattr(latest, "proxy_buffer_pieces", None),
            )
            state.append_stat(stream_id, snap)

    return {"ok": True}
