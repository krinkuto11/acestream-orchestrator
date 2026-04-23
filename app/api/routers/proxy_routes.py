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

    return {
        "vlc_user_agent": "AceStream-Orchestrator/1.0",
        "initial_data_wait_timeout": cfg.PROXY_INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": 0.2,
        "no_data_timeout_checks": 60,
        "no_data_check_interval": 1,
        "connection_timeout": 30,
        "upstream_connect_timeout": 3,
        "upstream_read_timeout": 90,
        "stream_timeout": 60,
        "chunk_size": 32768,
        "buffer_chunk_size": 131072,
        "redis_chunk_ttl": 60,
        "channel_shutdown_delay": 5,
        "proxy_prebuffer_seconds": 0,
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": "TS",
        "control_mode": "api",
        "legacy_api_preflight_tier": "light",
        "ace_live_edge_delay": cfg.ACE_LIVE_EDGE_DELAY,
        "engine_variant": f"global-{detect_platform()}",
        "hls_max_segments": 20,
        "hls_initial_segments": 3,
        "hls_window_size": 6,
        "hls_buffer_ready_timeout": 30,
        "hls_first_segment_timeout": 30,
        "hls_initial_buffer_seconds": 10,
        "hls_max_initial_segments": 10,
        "hls_segment_fetch_interval": 0.5,
    }


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
    return {"message": "Config updates are currently disabled during migration"}

    if initial_data_check_interval is not None:
        if initial_data_check_interval < 0.1 or initial_data_check_interval > 2.0:
            raise HTTPException(status_code=400, detail="initial_data_check_interval must be between 0.1 and 2.0 seconds")
        ProxyConfig.INITIAL_DATA_CHECK_INTERVAL = initial_data_check_interval

    if no_data_timeout_checks is not None:
        if no_data_timeout_checks < 5 or no_data_timeout_checks > 600:
            raise HTTPException(status_code=400, detail="no_data_timeout_checks must be between 5 and 600")
        ProxyConfig.NO_DATA_TIMEOUT_CHECKS = no_data_timeout_checks

    if no_data_check_interval is not None:
        if no_data_check_interval < 0.01 or no_data_check_interval > 1.0:
            raise HTTPException(status_code=400, detail="no_data_check_interval must be between 0.01 and 1.0 seconds")
        ProxyConfig.NO_DATA_CHECK_INTERVAL = no_data_check_interval

    if connection_timeout is not None:
        if connection_timeout < 5 or connection_timeout > 60:
            raise HTTPException(status_code=400, detail="connection_timeout must be between 5 and 60 seconds")
        ProxyConfig.CONNECTION_TIMEOUT = connection_timeout

    if upstream_connect_timeout is not None:
        if upstream_connect_timeout < 1 or upstream_connect_timeout > 60:
            raise HTTPException(status_code=400, detail="upstream_connect_timeout must be between 1 and 60 seconds")
        ProxyConfig.UPSTREAM_CONNECT_TIMEOUT = upstream_connect_timeout

    if upstream_read_timeout is not None:
        if upstream_read_timeout < 1 or upstream_read_timeout > 120:
            raise HTTPException(status_code=400, detail="upstream_read_timeout must be between 1 and 120 seconds")
        ProxyConfig.UPSTREAM_READ_TIMEOUT = upstream_read_timeout

    if stream_timeout is not None:
        if stream_timeout < 10 or stream_timeout > 300:
            raise HTTPException(status_code=400, detail="stream_timeout must be between 10 and 300 seconds")
        ProxyConfig.STREAM_TIMEOUT = stream_timeout

    if channel_shutdown_delay is not None:
        if channel_shutdown_delay < 1 or channel_shutdown_delay > 60:
            raise HTTPException(status_code=400, detail="channel_shutdown_delay must be between 1 and 60 seconds")
        ProxyConfig.CHANNEL_SHUTDOWN_DELAY = channel_shutdown_delay

    if proxy_prebuffer_seconds is not None:
        if proxy_prebuffer_seconds < 0 or proxy_prebuffer_seconds > 300:
            raise HTTPException(status_code=400, detail="proxy_prebuffer_seconds must be between 0 and 300 seconds")
        ProxyConfig.PROXY_PREBUFFER_SECONDS = int(proxy_prebuffer_seconds)

    if max_streams_per_engine is not None:
        if max_streams_per_engine < 1 or max_streams_per_engine > 20:
            raise HTTPException(status_code=400, detail="max_streams_per_engine must be between 1 and 20")
        cfg.MAX_STREAMS_PER_ENGINE = max_streams_per_engine

    if stream_mode is not None:
        if stream_mode not in ['TS', 'HLS']:
            raise HTTPException(status_code=400, detail="stream_mode must be either 'TS' or 'HLS'")
        ProxyConfig.STREAM_MODE = stream_mode

    if control_mode is not None:
        normalized_control_mode = normalize_proxy_mode(control_mode, default=None)
        if normalized_control_mode not in [PROXY_MODE_HTTP, PROXY_MODE_API]:
            raise HTTPException(status_code=400, detail="control_mode must be either 'http' or 'api'")
        ProxyConfig.CONTROL_MODE = normalized_control_mode

    if legacy_api_preflight_tier is not None:
        normalized_tier = str(legacy_api_preflight_tier).strip().lower()
        if normalized_tier not in ['light', 'deep']:
            raise HTTPException(status_code=400, detail="legacy_api_preflight_tier must be either 'light' or 'deep'")
        ProxyConfig.LEGACY_API_PREFLIGHT_TIER = normalized_tier

    if ace_live_edge_delay is not None:
        if ace_live_edge_delay < 0:
            raise HTTPException(status_code=400, detail="ace_live_edge_delay must be >= 0")
        cfg.ACE_LIVE_EDGE_DELAY = ace_live_edge_delay

    if hls_max_segments is not None:
        if hls_max_segments < 5 or hls_max_segments > 100:
            raise HTTPException(status_code=400, detail="hls_max_segments must be between 5 and 100")
        ProxyConfig.HLS_MAX_SEGMENTS = hls_max_segments

    if hls_initial_segments is not None:
        if hls_initial_segments < 1 or hls_initial_segments > 10:
            raise HTTPException(status_code=400, detail="hls_initial_segments must be between 1 and 10")
        ProxyConfig.HLS_INITIAL_SEGMENTS = hls_initial_segments

    if hls_window_size is not None:
        if hls_window_size < 3 or hls_window_size > 20:
            raise HTTPException(status_code=400, detail="hls_window_size must be between 3 and 20")
        ProxyConfig.HLS_WINDOW_SIZE = hls_window_size

    if hls_buffer_ready_timeout is not None:
        if hls_buffer_ready_timeout < 5 or hls_buffer_ready_timeout > 120:
            raise HTTPException(status_code=400, detail="hls_buffer_ready_timeout must be between 5 and 120 seconds")
        ProxyConfig.HLS_BUFFER_READY_TIMEOUT = hls_buffer_ready_timeout

    if hls_first_segment_timeout is not None:
        if hls_first_segment_timeout < 5 or hls_first_segment_timeout > 120:
            raise HTTPException(status_code=400, detail="hls_first_segment_timeout must be between 5 and 120 seconds")
        ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT = hls_first_segment_timeout

    if hls_initial_buffer_seconds is not None:
        if hls_initial_buffer_seconds < 5 or hls_initial_buffer_seconds > 60:
            raise HTTPException(status_code=400, detail="hls_initial_buffer_seconds must be between 5 and 60 seconds")
        ProxyConfig.HLS_INITIAL_BUFFER_SECONDS = hls_initial_buffer_seconds

    if hls_max_initial_segments is not None:
        if hls_max_initial_segments < 1 or hls_max_initial_segments > 20:
            raise HTTPException(status_code=400, detail="hls_max_initial_segments must be between 1 and 20")
        ProxyConfig.HLS_MAX_INITIAL_SEGMENTS = hls_max_initial_segments

    if hls_segment_fetch_interval is not None:
        if hls_segment_fetch_interval < 0.1 or hls_segment_fetch_interval > 2.0:
            raise HTTPException(status_code=400, detail="hls_segment_fetch_interval must be between 0.1 and 2.0")
        ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL = hls_segment_fetch_interval

    logger.info(
        f"Proxy configuration updated: "
        f"initial_data_wait_timeout={ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT}, "
        f"initial_data_check_interval={ProxyConfig.INITIAL_DATA_CHECK_INTERVAL}, "
        f"no_data_timeout_checks={ProxyConfig.NO_DATA_TIMEOUT_CHECKS}, "
        f"no_data_check_interval={ProxyConfig.NO_DATA_CHECK_INTERVAL}, "
        f"connection_timeout={ProxyConfig.CONNECTION_TIMEOUT}, "
        f"upstream_connect_timeout={ProxyConfig.UPSTREAM_CONNECT_TIMEOUT}, "
        f"upstream_read_timeout={ProxyConfig.UPSTREAM_READ_TIMEOUT}, "
        f"stream_timeout={ProxyConfig.STREAM_TIMEOUT}, "
        f"channel_shutdown_delay={ProxyConfig.CHANNEL_SHUTDOWN_DELAY}, "
        f"proxy_prebuffer_seconds={int(ProxyConfig.PROXY_PREBUFFER_SECONDS)}, "
        f"max_streams_per_engine={cfg.MAX_STREAMS_PER_ENGINE}, "
        f"stream_mode={ProxyConfig.STREAM_MODE}, "
        f"control_mode={_resolve_control_mode(ProxyConfig.CONTROL_MODE)}, "
        f"legacy_api_preflight_tier={str(ProxyConfig.LEGACY_API_PREFLIGHT_TIER).strip().lower()}, "
        f"ace_live_edge_delay={cfg.ACE_LIVE_EDGE_DELAY}"
    )

    config_to_save = {
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "upstream_connect_timeout": ProxyConfig.UPSTREAM_CONNECT_TIMEOUT,
        "upstream_read_timeout": ProxyConfig.UPSTREAM_READ_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "proxy_prebuffer_seconds": int(ProxyConfig.PROXY_PREBUFFER_SECONDS),
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": _resolve_control_mode(ProxyConfig.CONTROL_MODE),
        "legacy_api_preflight_tier": str(ProxyConfig.LEGACY_API_PREFLIGHT_TIER).strip().lower(),
        "ace_live_edge_delay": cfg.ACE_LIVE_EDGE_DELAY,
        "hls_max_segments": ProxyConfig.HLS_MAX_SEGMENTS,
        "hls_initial_segments": ProxyConfig.HLS_INITIAL_SEGMENTS,
        "hls_window_size": ProxyConfig.HLS_WINDOW_SIZE,
        "hls_buffer_ready_timeout": ProxyConfig.HLS_BUFFER_READY_TIMEOUT,
        "hls_first_segment_timeout": ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT,
        "hls_initial_buffer_seconds": ProxyConfig.HLS_INITIAL_BUFFER_SECONDS,
        "hls_max_initial_segments": ProxyConfig.HLS_MAX_INITIAL_SEGMENTS,
        "hls_segment_fetch_interval": ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL,
    }
    if SettingsPersistence.save_proxy_config(config_to_save):
        logger.info("Proxy configuration persisted to RuntimeSettings DB")

    return {
        "message": "Proxy configuration updated and persisted",
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "upstream_connect_timeout": ProxyConfig.UPSTREAM_CONNECT_TIMEOUT,
        "upstream_read_timeout": ProxyConfig.UPSTREAM_READ_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "proxy_prebuffer_seconds": int(ProxyConfig.PROXY_PREBUFFER_SECONDS),
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": _resolve_control_mode(ProxyConfig.CONTROL_MODE),
        "legacy_api_preflight_tier": str(ProxyConfig.LEGACY_API_PREFLIGHT_TIER).strip().lower(),
        "ace_live_edge_delay": cfg.ACE_LIVE_EDGE_DELAY,
        "hls_max_segments": ProxyConfig.HLS_MAX_SEGMENTS,
        "hls_initial_segments": ProxyConfig.HLS_INITIAL_SEGMENTS,
        "hls_window_size": ProxyConfig.HLS_WINDOW_SIZE,
        "hls_buffer_ready_timeout": ProxyConfig.HLS_BUFFER_READY_TIMEOUT,
        "hls_first_segment_timeout": ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT,
        "hls_initial_buffer_seconds": ProxyConfig.HLS_INITIAL_BUFFER_SECONDS,
        "hls_max_initial_segments": ProxyConfig.HLS_MAX_INITIAL_SEGMENTS,
        "hls_segment_fetch_interval": ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL,
    }


# ---------------------------------------------------------------------------
# Internal endpoint consumed by the Go proxy — engine selection
# ---------------------------------------------------------------------------

@router.get("/internal/proxy/select-engine")
def internal_select_engine():
    """Return the best available engine for the Go proxy to connect to."""
    engine, _ = select_best_engine_shared()
    return {
        "host": engine.host,
        "port": engine.port,
        "api_port": engine.api_port or 62062,
        "container_id": engine.container_id,
        "proxy_prebuffer_seconds": getattr(cfg, "PROXY_PREBUFFER_SECONDS", 3),
        "pacing_bitrate_multiplier": getattr(cfg, "PACING_BITRATE_MULTIPLIER", 1.5),
        "stream_mode": getattr(cfg, "STREAM_MODE", "TS").upper(),
        "control_mode": getattr(cfg, "CONTROL_MODE", "api").lower(),
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
