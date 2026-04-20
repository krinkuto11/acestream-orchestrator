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
from ...proxy.utils import get_client_ip, sanitize_stream_id
from ...proxy.manager import ProxyManager
from ...proxy.ace_api_client import AceLegacyApiClient, AceLegacyApiError
from ...proxy.constants import PROXY_MODE_HTTP, PROXY_MODE_API, normalize_proxy_mode
from ...models.schemas import StreamState, StreamStartedEvent

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models (also defined in legacy_monitor.py for symmetry)
# ---------------------------------------------------------------------------

class StreamSeekRequest(BaseModel):
    target_timestamp: int


class StreamSaveRequest(BaseModel):
    path: str
    index: int = 0
    infohash: Optional[str] = None


# ---------------------------------------------------------------------------
# Stream-input helpers (duplicated from main.py to avoid circular imports)
# ---------------------------------------------------------------------------

def _select_stream_input(
    id: Optional[str],
    infohash: Optional[str],
    torrent_url: Optional[str],
    direct_url: Optional[str],
    raw_data: Optional[str],
) -> tuple:
    choices = []
    for input_type, raw_value in [
        ("content_id", id),
        ("infohash", infohash),
        ("torrent_url", torrent_url),
        ("direct_url", direct_url),
        ("raw_data", raw_data),
    ]:
        text = sanitize_stream_id(raw_value)
        if text:
            choices.append((input_type, text))

    if not choices:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one input: id, infohash, torrent_url, direct_url, or raw_data",
        )

    if len(choices) > 1:
        raise HTTPException(
            status_code=400,
            detail="Input parameters are mutually exclusive. Provide only one of: id, infohash, torrent_url, direct_url, raw_data",
        )

    return choices[0]


def _normalize_file_indexes(file_indexes: Optional[str]) -> str:
    normalized = str(file_indexes if file_indexes is not None else "0").strip()
    if not normalized:
        return "0"

    if not re.fullmatch(r"\d+(,\d+)*", normalized):
        raise HTTPException(
            status_code=400,
            detail="file_indexes must be a comma-separated list of non-negative integers (for example: 0 or 0,2)",
        )
    return normalized


def _normalize_seekback(seekback: Optional[int]) -> int:
    if seekback is None:
        return 0
    try:
        normalized = int(float(seekback))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="live_delay (or seekback) must be a non-negative integer")
    if normalized < 0:
        raise HTTPException(status_code=400, detail="live_delay (or seekback) must be a non-negative integer")
    return normalized


def _resolve_live_delay(seekback: Optional[int], live_delay: Optional[int]) -> int:
    """Resolve effective startup delay using query override, legacy alias, then global default."""
    if live_delay is not None:
        return _normalize_seekback(live_delay)
    if seekback is not None:
        return _normalize_seekback(seekback)
    return _normalize_seekback(cfg.ACE_LIVE_EDGE_DELAY)


def _resolve_control_mode(mode: Optional[str]) -> str:
    """Normalize control mode to canonical values (http/api) with legacy aliases."""
    return normalize_proxy_mode(mode, default=PROXY_MODE_API) or PROXY_MODE_API


def _build_stream_key(input_type: str, input_value: str, file_indexes: str = "0", seekback: int = 0) -> str:
    input_value = sanitize_stream_id(input_value)

    if input_type in {"content_id", "infohash"} and file_indexes == "0" and seekback <= 0:
        return input_value

    if input_type not in {"content_id", "infohash"} and file_indexes == "0" and seekback <= 0:
        digest = hashlib.sha1(input_value.encode("utf-8")).hexdigest()
        return f"{input_type}:{digest}"

    keyed_payload = f"{input_type}:{input_value}|file_indexes={file_indexes}|seekback={seekback}"
    digest = hashlib.sha1(keyed_payload.encode("utf-8")).hexdigest()
    return f"{input_type}:{digest}"


def _build_engine_stream_params(
    input_type: str,
    input_value: str,
    pid: str,
    file_indexes: str = "0",
    seekback: int = 0,
) -> Dict[str, str]:
    params: Dict[str, str] = {
        "format": "json",
        "pid": pid,
        "file_indexes": file_indexes,
    }

    if seekback > 0:
        params["seekback"] = str(seekback)

    if input_type in {"content_id", "infohash"}:
        params["id"] = input_value
        if input_type == "infohash":
            params["infohash"] = input_value
    elif input_type == "torrent_url":
        params["torrent_url"] = input_value
    elif input_type == "direct_url":
        params["direct_url"] = input_value
        params["url"] = input_value
    elif input_type == "raw_data":
        params["raw_data"] = input_value
    else:
        params["id"] = input_value

    return params


def _build_stream_query_params(
    input_type: str,
    input_value: str,
    file_indexes: str = "0",
    seekback: int = 0,
) -> Dict[str, str]:
    if input_type == "content_id":
        params = {"id": input_value}
    else:
        params = {input_type: input_value}

    if file_indexes != "0":
        params["file_indexes"] = file_indexes

    if seekback > 0:
        params["live_delay"] = str(seekback)

    return params


# ---------------------------------------------------------------------------
# Stream control helpers
# ---------------------------------------------------------------------------

async def _resolve_proxy_stream_manager(monitor_id: str):
    """Resolve active StreamManager by stream id or monitor_id fallback."""
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

    proxy = ProxyManager.get_instance()

    stream = state.get_stream(monitor_id)
    stream_key = stream.key if stream else monitor_id
    manager = proxy.stream_managers.get(stream_key)

    # Compatibility fallback when monitor_id points to a legacy monitor session.
    if not manager:
        monitor = await legacy_stream_monitoring_service.get_monitor(monitor_id, include_recent_status=False)
        normalized_monitor_content_id = sanitize_stream_id(monitor_content_id)  # noqa: F821 (preserves original bug)
        if normalized_monitor_content_id:
            manager = proxy.stream_managers.get(normalized_monitor_content_id)
            stream_key = normalized_monitor_content_id

    return manager, stream, stream_key


def _set_stream_paused_runtime_state(monitor_id: str, stream: Optional[StreamState], paused: bool):
    if stream:
        state.set_stream_paused(stream.id, paused)

    monitor_session = state.get_monitor_session(monitor_id)
    if monitor_session:
        monitor_session["paused"] = bool(paused)
        monitor_session["status"] = "paused" if paused else "running"
        state.upsert_monitor_session(monitor_id, monitor_session)


# ---------------------------------------------------------------------------
# /ace/preflight
# ---------------------------------------------------------------------------

@router.get(
    "/ace/preflight",
    tags=["Proxy"],
    summary="Preflight AceStream input",
    description="Runs availability checks and canonicalizes stream identifiers before playback.",
    responses={
        200: {"description": "Preflight result"},
        400: {"description": "Invalid request parameters"},
        503: {"description": "No available engine capacity"},
    },
)
def ace_preflight(
    id: Optional[str] = Query(None, description="AceStream content ID (PID/content_id)"),
    infohash: Optional[str] = Query(None, description="AceStream infohash"),
    torrent_url: Optional[str] = Query(None, description="Direct .torrent URL"),
    direct_url: Optional[str] = Query(None, description="Direct media/magnet URL"),
    raw_data: Optional[str] = Query(None, description="Raw torrent file data"),
    file_indexes: Optional[str] = Query("0", description="Comma-separated torrent file indexes (for example: 0 or 0,2)"),
    seekback: Optional[int] = Query(None, description="Deprecated alias for live_delay"),
    live_delay: Optional[int] = Query(None, description="Optional startup delay in seconds behind live edge"),
    tier: str = Query("light", description="Availability probe tier: light or deep"),
):
    """Run a short availability probe and canonicalize content IDs before playback."""
    from ...proxy.config_helper import Config as ProxyConfig
    import requests

    input_type, input_value = _select_stream_input(id, infohash, torrent_url, direct_url, raw_data)
    normalized_file_indexes = _normalize_file_indexes(file_indexes)
    normalized_seekback = _resolve_live_delay(seekback, live_delay)
    stream_key = _build_stream_key(input_type, input_value, normalized_file_indexes, normalized_seekback)

    normalized_tier = (tier or "light").strip().lower()
    if normalized_tier not in {"light", "deep"}:
        raise HTTPException(status_code=400, detail="tier must be 'light' or 'deep'")

    control_mode = _resolve_control_mode(ProxyConfig.CONTROL_MODE)

    engines = state.list_engines()
    if not engines:
        raise HTTPException(status_code=503, detail="No engines available")

    active_streams = state.list_streams(status="started")
    monitor_loads = state.get_active_monitor_load_by_engine()
    engine_loads = {}
    for stream in active_streams:
        engine_loads[stream.container_id] = engine_loads.get(stream.container_id, 0) + 1

    for container_id, monitor_count in monitor_loads.items():
        engine_loads[container_id] = engine_loads.get(container_id, 0) + monitor_count

    max_streams = cfg.MAX_STREAMS_PER_ENGINE
    available_engines = [e for e in engines if engine_loads.get(e.container_id, 0) < max_streams]
    if not available_engines:
        raise HTTPException(
            status_code=503,
            detail=f"All engines at maximum capacity ({max_streams} streams per engine)",
        )

    selected_engine = sorted(
        available_engines,
        key=lambda e: (engine_loads.get(e.container_id, 0), not e.forwarded),
    )[0]

    if control_mode == PROXY_MODE_API:
        api_port = selected_engine.api_port or 62062
        client = AceLegacyApiClient(
            host=selected_engine.host,
            port=api_port,
            connect_timeout=8,
            response_timeout=8,
        )
        try:
            client.connect()
            client.authenticate()
            if input_type in {"content_id", "infohash"}:
                preflight_result = client.preflight(
                    input_value,
                    tier=normalized_tier,
                    file_indexes=normalized_file_indexes,
                )
            else:
                resolve_resp, resolved_mode = client.resolve_content(
                    input_value,
                    session_id="0",
                    mode=input_type,
                )
                status_code = resolve_resp.get("status")
                available = True if resolved_mode == "direct_url" else status_code in (1, 2)

                preflight_result = {
                    "tier": normalized_tier,
                    "available": available,
                    "status_code": 1 if resolved_mode == "direct_url" else status_code,
                    "mode": resolved_mode,
                    "infohash": resolve_resp.get("infohash"),
                    "loadresp": resolve_resp,
                    "can_retry": True,
                    "should_wait": bool(status_code == 2),
                }

                if not preflight_result["available"]:
                    preflight_result["message"] = resolve_resp.get("message", "content unavailable")

                if normalized_tier == "deep" and preflight_result["available"]:
                    start_info = client.start_stream(
                        input_value,
                        mode=resolved_mode,
                        file_indexes=normalized_file_indexes,
                        seekback=normalized_seekback,
                    )
                    status_probe = client.collect_status_samples(samples=4, interval_s=0.5, per_sample_timeout_s=2.0)
                    preflight_result["start"] = start_info
                    preflight_result["status_probe"] = status_probe

                    preflight_bitrate = int(start_info.get("bitrate") or 0)
                    logger.info(
                        "Preflight (deep) for %s on engine %s: bitrate=%s bps",
                        stream_key,
                        selected_engine.container_id[:12],
                        preflight_bitrate
                    )
                    client.stop_stream()

            return {
                "control_mode": control_mode,
                "tier": normalized_tier,
                "input_type": input_type,
                "file_indexes": normalized_file_indexes,
                "seekback": normalized_seekback,
                "stream_key": stream_key,
                "engine": {
                    "container_id": selected_engine.container_id,
                    "host": selected_engine.host,
                    "port": selected_engine.port,
                    "api_port": api_port,
                    "forwarded": selected_engine.forwarded,
                },
                "result": preflight_result,
            }
        except AceLegacyApiError as e:
            raise HTTPException(status_code=503, detail=str(e))
        finally:
            try:
                client.shutdown()
            except Exception:
                pass

    # HTTP control fallback for compatibility in HTTP mode.
    url = f"http://{selected_engine.host}:{selected_engine.port}/ace/getstream"
    pid = f"preflight-{int(time.time())}"
    params = _build_engine_stream_params(
        input_type,
        input_value,
        pid=pid,
        file_indexes=normalized_file_indexes,
        seekback=normalized_seekback,
    )
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"HTTP preflight failed: {e}")

    if payload.get("error"):
        return {
            "control_mode": control_mode,
            "tier": normalized_tier,
            "input_type": input_type,
            "file_indexes": normalized_file_indexes,
            "seekback": normalized_seekback,
            "stream_key": stream_key,
            "engine": {
                "container_id": selected_engine.container_id,
                "host": selected_engine.host,
                "port": selected_engine.port,
                "api_port": selected_engine.api_port,
                "forwarded": selected_engine.forwarded,
            },
            "result": {
                "available": False,
                "message": payload.get("error"),
                "can_retry": True,
                "should_wait": False,
            },
        }

    response_data = payload.get("response") or {}
    result: Dict[str, Any] = {
        "available": bool(response_data.get("playback_url")),
        "infohash": response_data.get("infohash"),
        "playback_session_id": response_data.get("playback_session_id"),
        "playback_url": response_data.get("playback_url"),
        "stat_url": response_data.get("stat_url"),
        "command_url": response_data.get("command_url"),
        "is_live": response_data.get("is_live"),
        "can_retry": True,
        "should_wait": False,
    }

    if result["available"]:
        preflight_bitrate = int(response_data.get("bitrate") or 0)
        logger.info(
            "Preflight (HTTP) for %s on engine %s: bitrate=%s bps",
            stream_key,
            selected_engine.container_id[:12],
            preflight_bitrate
        )

    if normalized_tier == "deep" and response_data.get("stat_url"):
        try:
            stat_response = requests.get(response_data.get("stat_url"), timeout=8)
            stat_response.raise_for_status()
            stat_payload = (stat_response.json() or {}).get("response") or {}
            result["status_probe"] = {
                "status_text": stat_payload.get("status_text") or stat_payload.get("status"),
                "status": stat_payload.get("status"),
                "progress": stat_payload.get("progress"),
                "peers": stat_payload.get("peers"),
                "http_peers": stat_payload.get("http_peers"),
                "speed_down": stat_payload.get("speed_down"),
                "speed_up": stat_payload.get("speed_up"),
                "downloaded": stat_payload.get("downloaded"),
                "uploaded": stat_payload.get("uploaded"),
                "livepos": stat_payload.get("livepos"),
            }
        except Exception as e:
            result["status_probe_error"] = str(e)

    return {
        "control_mode": control_mode,
        "tier": normalized_tier,
        "input_type": input_type,
        "file_indexes": normalized_file_indexes,
        "seekback": normalized_seekback,
        "stream_key": stream_key,
        "engine": {
            "container_id": selected_engine.container_id,
            "host": selected_engine.host,
            "port": selected_engine.port,
            "api_port": selected_engine.api_port,
            "forwarded": selected_engine.forwarded,
        },
        "result": result,
    }


# ---------------------------------------------------------------------------
# /ace/getstream
# ---------------------------------------------------------------------------

# -- Legacy ace_getstream removed --

# -- Legacy api_hls_segment_file removed --


# ---------------------------------------------------------------------------
# Stream control: seek / pause / resume / save
# ---------------------------------------------------------------------------

@router.post("/api/v1/streams/{monitor_id}/seek", dependencies=[Depends(require_api_key)])
async def seek_stream_live(monitor_id: str, req: StreamSeekRequest):
    """Seek an active API-mode stream to the given live timestamp via LIVESEEK."""
    target_timestamp = int(req.target_timestamp)
    if target_timestamp < 0:
        raise HTTPException(status_code=400, detail="target_timestamp must be non-negative")

    manager, _, _ = await _resolve_proxy_stream_manager(monitor_id)

    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for seek request")

    try:
        await asyncio.to_thread(manager.seek_stream, target_timestamp)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to seek stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Seek failed: {str(e)}")

    return {"status": "seek_issued"}


@router.post("/api/v1/streams/{monitor_id}/pause", dependencies=[Depends(require_api_key)])
async def pause_stream_live(monitor_id: str):
    """Pause an active API-mode stream via PAUSE command."""
    manager, stream, _ = await _resolve_proxy_stream_manager(monitor_id)
    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for pause request")

    try:
        await asyncio.to_thread(manager.pause_stream)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AceLegacyApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to pause stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Pause failed: {str(e)}")

    _set_stream_paused_runtime_state(monitor_id, stream, True)
    return {"status": "paused"}


@router.post("/api/v1/streams/{monitor_id}/resume", dependencies=[Depends(require_api_key)])
async def resume_stream_live(monitor_id: str):
    """Resume an active API-mode stream via RESUME command."""
    manager, stream, _ = await _resolve_proxy_stream_manager(monitor_id)
    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for resume request")

    try:
        await asyncio.to_thread(manager.resume_stream)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AceLegacyApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to resume stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(e)}")

    _set_stream_paused_runtime_state(monitor_id, stream, False)
    return {"status": "resumed"}


@router.post("/api/v1/streams/{monitor_id}/save", dependencies=[Depends(require_api_key)])
async def save_stream_live(monitor_id: str, req: StreamSaveRequest):
    """Request SAVE for an active API-mode stream session."""
    save_path = str(req.path or "").strip()
    if not save_path:
        raise HTTPException(status_code=400, detail="path is required")

    file_index = int(req.index)
    if file_index < 0:
        raise HTTPException(status_code=400, detail="index must be non-negative")

    manager, _, _ = await _resolve_proxy_stream_manager(monitor_id)
    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for save request")

    try:
        result = await asyncio.to_thread(
            manager.save_stream,
            infohash=req.infohash,
            index=file_index,
            path=save_path,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AceLegacyApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to save stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")

    return result


# ---------------------------------------------------------------------------
# Proxy status / sessions / clients / debug
# ---------------------------------------------------------------------------

@router.get("/proxy/status")
async def proxy_status():
    """Get proxy status and active sessions."""
    proxy_manager = ProxyManager.get_instance()
    status = await proxy_manager.get_status()
    return status


@router.get("/proxy/sessions")
async def proxy_sessions():
    """Get list of active proxy sessions."""
    proxy_manager = ProxyManager.get_instance()
    status = await proxy_manager.get_status()
    return {"sessions": status.get("sessions", [])}


@router.get("/proxy/sessions/{ace_id}")
async def proxy_session_info(ace_id: str):
    """Get detailed info for a specific proxy session."""
    proxy_manager = ProxyManager.get_instance()
    session_info = await proxy_manager.get_session_info(ace_id)

    if not session_info:
        raise HTTPException(status_code=404, detail=f"Session {ace_id} not found")

    return session_info


@router.get("/proxy/streams/{stream_key}/clients")
async def get_stream_clients(stream_key: str):
    stream_key = sanitize_stream_id(stream_key)

    """Get list of clients connected to a specific stream."""
    from ...data_plane.client_tracker import client_tracking_service
    from ...proxy.config_helper import Config as ProxyConfig

    try:
        from ...main import _prune_client_tracker_if_due
        _prune_client_tracker_if_due(ttl_s=float(ProxyConfig.CLIENT_RECORD_TTL), min_interval_s=3.0)
        payload = client_tracking_service.get_stream_clients_payload(stream_key)
        return payload

    except Exception as e:
        logger.error(f"Error getting clients for stream {stream_key}: {e}")
        return {"clients": []}


@router.get("/debug/sync-check")
async def sync_check():
    """Debug endpoint to check synchronization between state and proxy."""
    from ...proxy.server import ProxyServer
    from ...proxy.hls_proxy import HLSProxyServer

    state_streams = state.list_streams_with_stats(status="started")
    state_keys = {s.key for s in state_streams}

    ts_proxy = ProxyServer.get_instance()
    ts_sessions = set(ts_proxy.stream_managers.keys())

    hls_proxy = HLSProxyServer.get_instance()
    hls_sessions = set(hls_proxy.stream_managers.keys())

    orphaned_ts = ts_sessions - state_keys
    orphaned_hls = hls_sessions - state_keys
    missing_ts = state_keys - ts_sessions
    missing_hls = state_keys - hls_sessions

    return {
        "state": {
            "stream_count": len(state_streams),
            "stream_keys": list(state_keys)
        },
        "ts_proxy": {
            "session_count": len(ts_sessions),
            "session_keys": list(ts_sessions)
        },
        "hls_proxy": {
            "session_count": len(hls_sessions),
            "session_keys": list(hls_sessions)
        },
        "discrepancies": {
            "orphaned_ts_sessions": list(orphaned_ts),
            "orphaned_hls_sessions": list(orphaned_hls),
            "missing_ts_sessions": list(missing_ts),
            "missing_hls_sessions": list(missing_hls),
            "has_issues": any([orphaned_ts, orphaned_hls, missing_ts, missing_hls])
        }
    }


# ---------------------------------------------------------------------------
# /proxy/config GET + POST
# ---------------------------------------------------------------------------

@router.get("/proxy/config")
def get_proxy_config():
    """Get current proxy configuration settings."""
    from ...proxy import constants as proxy_constants
    from ...proxy.config_helper import Config as ProxyConfig, ConfigHelper
    from ...infrastructure.engine_config import detect_platform

    return {
        "vlc_user_agent": proxy_constants.VLC_USER_AGENT,
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "upstream_connect_timeout": ProxyConfig.UPSTREAM_CONNECT_TIMEOUT,
        "upstream_read_timeout": ProxyConfig.UPSTREAM_READ_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "chunk_size": ProxyConfig.CHUNK_SIZE,
        "buffer_chunk_size": ProxyConfig.BUFFER_CHUNK_SIZE,
        "redis_chunk_ttl": ProxyConfig.REDIS_CHUNK_TTL,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "proxy_prebuffer_seconds": ConfigHelper.proxy_prebuffer_seconds(),
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": _resolve_control_mode(ProxyConfig.CONTROL_MODE),
        "legacy_api_preflight_tier": ConfigHelper.legacy_api_preflight_tier(),
        "ace_live_edge_delay": cfg.ACE_LIVE_EDGE_DELAY,
        "engine_variant": f"global-{detect_platform()}",
        "hls_max_segments": ProxyConfig.HLS_MAX_SEGMENTS,
        "hls_initial_segments": ProxyConfig.HLS_INITIAL_SEGMENTS,
        "hls_window_size": ProxyConfig.HLS_WINDOW_SIZE,
        "hls_buffer_ready_timeout": ProxyConfig.HLS_BUFFER_READY_TIMEOUT,
        "hls_first_segment_timeout": ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT,
        "hls_initial_buffer_seconds": ProxyConfig.HLS_INITIAL_BUFFER_SECONDS,
        "hls_max_initial_segments": ProxyConfig.HLS_MAX_INITIAL_SEGMENTS,
        "hls_segment_fetch_interval": ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL,
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
    from ...proxy import constants as proxy_constants
    from ...proxy.config_helper import Config as ProxyConfig, ConfigHelper
    from ...persistence.settings_persistence import SettingsPersistence

    if initial_data_wait_timeout is not None:
        if initial_data_wait_timeout < 1 or initial_data_wait_timeout > 60:
            raise HTTPException(status_code=400, detail="initial_data_wait_timeout must be between 1 and 60 seconds")
        ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT = initial_data_wait_timeout

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
    """Return the best available engine for the Go proxy to connect to.

    Called by the Go proxy at stream-start time. Returns JSON with host, port,
    api_port, container_id, and proxy_prebuffer_seconds.
    Reachable only from localhost (no auth required).
    """
    from ...proxy.config_helper import ConfigHelper
    engine, _ = select_best_engine_shared()
    return {
        "host": engine.host,
        "port": engine.port,
        "api_port": engine.api_port or 62062,
        "container_id": engine.container_id,
        "proxy_prebuffer_seconds": int(ConfigHelper.proxy_prebuffer_seconds()),
        "stream_mode": (ConfigHelper.stream_mode() or "TS").upper(),
        "control_mode": (ConfigHelper.control_mode() or "api").lower(),
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
