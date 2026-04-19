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

@router.get(
    "/ace/getstream",
    tags=["Proxy"],
    summary="Start or join stream playback",
    description="Starts or joins multiplexed stream playback and returns MPEG-TS or HLS depending on proxy mode.",
    responses={
        200: {"description": "Streaming response"},
        400: {"description": "Invalid request or unsupported mode"},
        422: {"description": "Stream blacklisted or unprocessable"},
        500: {"description": "Internal streaming error"},
    },
)
async def ace_getstream(
    id: Optional[str] = Query(None, description="AceStream content ID (PID/content_id)"),
    infohash: Optional[str] = Query(None, description="AceStream infohash"),
    torrent_url: Optional[str] = Query(None, description="Direct .torrent URL"),
    direct_url: Optional[str] = Query(None, description="Direct media/magnet URL"),
    raw_data: Optional[str] = Query(None, description="Raw torrent file data"),
    file_indexes: Optional[str] = Query("0", description="Comma-separated torrent file indexes (for example: 0 or 0,2)"),
    seekback: Optional[int] = Query(None, description="Deprecated alias for live_delay"),
    live_delay: Optional[int] = Query(None, description="Optional startup delay in seconds behind live edge"),
    request: Request = None,
):
    """Proxy endpoint for AceStream video streams with multiplexing."""
    from fastapi.responses import StreamingResponse
    from uuid import uuid4
    from app.proxy.stream_generator import create_stream_generator
    from app.proxy.config_helper import Config as ProxyConfig
    from ...data_plane.looping_streams import looping_streams_tracker
    from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service
    from ...data_plane.hls_segmenter import hls_segmenter_service
    import os

    request_started_at = time.perf_counter()

    input_type, input_value = _select_stream_input(id, infohash, torrent_url, direct_url, raw_data)
    normalized_file_indexes = _normalize_file_indexes(file_indexes)
    normalized_seekback = _resolve_live_delay(seekback, live_delay)
    stream_key = _build_stream_key(input_type, input_value, normalized_file_indexes, normalized_seekback)

    stream_mode = ProxyConfig.STREAM_MODE
    control_mode = _resolve_control_mode(ProxyConfig.CONTROL_MODE)

    # HTTP mode does not support orchestrated liveseek/seekback.
    if control_mode == PROXY_MODE_HTTP:
        if normalized_seekback > 0:
            logger.info(f"Overriding seekback/live_delay to 0 for HTTP-mode stream {stream_key}")
            normalized_seekback = 0
            stream_key = _build_stream_key(input_type, input_value, normalized_file_indexes, normalized_seekback)

    # Check if stream is on the looping blacklist
    if looping_streams_tracker.is_looping(stream_key):
        logger.warning(f"Stream request denied: {stream_key} is on looping blacklist")
        raise HTTPException(
            status_code=422,
            detail={
                "error": "stream_blacklisted",
                "code": "looping_stream",
                "message": "This stream has been detected as looping (no new data) and is temporarily blacklisted"
            }
        )

    # Get client info
    client_ip = get_client_ip(request) if request else "unknown"
    user_agent = request.headers.get('user-agent', 'unknown') if request else "unknown"
    client_id = hashlib.md5(f"{client_ip}_{user_agent}_{stream_key}".encode()).hexdigest()
    client_identity = f"{client_ip}:{hashlib.sha1(user_agent.encode('utf-8', errors='ignore')).hexdigest()[:12]}"

    reusable_monitor_session = None
    if input_type in {"content_id", "infohash"} and normalized_file_indexes == "0" and normalized_seekback <= 0:
        reusable_monitor_session = await legacy_stream_monitoring_service.get_reusable_session_for_content(input_value)
    if reusable_monitor_session:
        monitor_engine = reusable_monitor_session.get("engine") or {}
        logger.info(
            "Reusing monitor session %s for stream %s on engine %s",
            reusable_monitor_session.get("monitor_id"),
            stream_key,
            str(monitor_engine.get("container_id") or "unknown")[:12],
        )

    reservation_engine_id = None

    def rollback_reservation(target_engine_id: Optional[str] = None):
        engine_id = target_engine_id or reservation_engine_id
        if engine_id:
            try:
                from app.proxy.manager import ProxyManager
                redis = ProxyManager.get_instance().redis_client
                if redis:
                    pending_key = f"ace_proxy:engine:{engine_id}:pending"
                    decr_script = """
                    local current = redis.call('GET', KEYS[1])
                    if current and tonumber(current) > 0 then
                        return redis.call('DECR', KEYS[1])
                    else
                        return 0
                    end
                    """
                    redis.eval(decr_script, 1, pending_key)
                    logger.debug(f"Rolled back pending reservation for engine {engine_id[:12]}")
            except Exception as e:
                logger.warning(f"Failed to rollback reservation for engine {engine_id[:12]}: {e}")

    def select_best_engine(additional_load_by_engine: Optional[Dict[str, int]] = None):
        return select_best_engine_shared(
            reserve_pending=True,
            additional_load_by_engine=additional_load_by_engine,
        )

    def _find_active_api_hls_stream_id(stream_key: str) -> Optional[str]:
        active_streams = state.list_streams(status="started")
        for active_stream in active_streams:
            if active_stream.key != stream_key:
                continue
            if normalize_proxy_mode(active_stream.control_mode, default=PROXY_MODE_HTTP) == PROXY_MODE_API:
                return active_stream.id
        return None

    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    async def _register_api_hls_stream_if_missing(
        *,
        container_id: str,
        engine_host: str,
        engine_port: int,
        engine_api_port: int,
        playback_session_id: str,
        stat_url: str,
        command_url: str,
        is_live: int,
        bitrate: int = 0,
        stream_key: str,
        input_type: str,
        normalized_file_indexes: str,
        normalized_seekback: int,
    ) -> Optional[str]:
        existing_stream_id = _find_active_api_hls_stream_id(stream_key)
        if existing_stream_id:
            return existing_stream_id

        if not container_id or not engine_host or not engine_port or not playback_session_id:
            return None

        try:
            from ...data_plane.internal_events import handle_stream_started

            event = StreamStartedEvent(
                container_id=container_id,
                engine={"host": engine_host, "port": int(engine_port)},
                stream={
                    "key_type": input_type,
                    "key": stream_key,
                    "file_indexes": normalized_file_indexes,
                    "seekback": normalized_seekback,
                    "live_delay": normalized_seekback,
                    "control_mode": PROXY_MODE_API,
                },
                session={
                    "playback_session_id": playback_session_id,
                    "stat_url": stat_url,
                    "command_url": command_url,
                    "is_live": int(is_live or 1),
                    "bitrate": bitrate,
                },
                labels={
                    "source": "api_hls_segmenter",
                    "stream_mode": "HLS",
                    "proxy.control_mode": PROXY_MODE_API,
                    "stream.input_type": input_type,
                    "stream.file_indexes": normalized_file_indexes,
                    "stream.seekback": str(normalized_seekback),
                    "stream.live_delay": str(normalized_seekback),
                    "host.api_port": str(engine_api_port or ""),
                    "client.id": client_identity,
                    "client.ip": client_ip,
                    "client.user_agent": user_agent[:200],
                },
            )

            result = await asyncio.to_thread(handle_stream_started, event)
            return result.id if result else None
        except Exception as e:
            logger.warning("Failed to register API-mode HLS stream in state for %s: %s", stream_key, e)
            return None

    try:
        # Handle HLS mode differently from TS mode
        if stream_mode == 'HLS':
            import requests
            if control_mode == PROXY_MODE_HTTP:
                from app.proxy.hls_proxy import HLSProxyServer
                from uuid import uuid4

                hls_proxy = HLSProxyServer.get_instance()

                if hls_proxy.has_channel(stream_key):
                    logger.debug(f"HLS channel {stream_key} already exists, serving manifest to client {client_id} from {client_ip}")

                    try:
                        manifest_content = await hls_proxy.get_manifest_async(stream_key)
                        manifest_bytes = manifest_content.encode('utf-8')
                        manifest_seconds_behind = hls_proxy.get_manifest_buffer_seconds_behind(stream_key)
                        hls_proxy.record_client_activity(
                            stream_key,
                            client_ip,
                            client_id=client_identity,
                            user_agent=user_agent,
                            request_kind="manifest",
                            bytes_sent=len(manifest_bytes),
                            buffer_seconds_behind=manifest_seconds_behind,
                        )

                        elapsed = time.perf_counter() - request_started_at
                        observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                        observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                        return StreamingResponse(
                            iter([manifest_bytes]),
                            media_type="application/vnd.apple.mpegurl",
                            headers={
                                "Cache-Control": "no-cache, no-store, must-revalidate",
                                "Connection": "keep-alive",
                            }
                        )
                    except TimeoutError as e:
                        logger.error(f"Timeout getting HLS manifest: {e}")
                        raise HTTPException(status_code=503, detail=f"Timeout waiting for stream buffer: {str(e)}")

                if reusable_monitor_session:
                    monitor_engine = reusable_monitor_session.get("engine") or {}
                    monitor_session = reusable_monitor_session.get("session") or {}
                    selected_engine = SimpleNamespace(
                        container_id=monitor_engine.get("container_id"),
                        host=monitor_engine.get("host"),
                        port=monitor_engine.get("port"),
                        api_port=monitor_engine.get("api_port") or 62062,
                        forwarded=bool(monitor_engine.get("forwarded")),
                    )
                    monitor_loads = state.get_active_monitor_load_by_engine()
                    current_load = len(state.list_streams(status="started", container_id=selected_engine.container_id)) + monitor_loads.get(selected_engine.container_id, 0)
                    playback_url = monitor_session.get("playback_url")
                    if not playback_url:
                        raise HTTPException(status_code=500, detail="Monitor session has no playback URL")
                else:
                    selected_engine, current_load = select_best_engine()
                    reservation_engine_id = selected_engine.container_id

                logger.info(
                    f"Selected engine {selected_engine.container_id[:12]} for new {stream_mode} stream {stream_key} "
                    f"(forwarded={selected_engine.forwarded}, current_load={current_load})"
                )
                logger.info(f"Client {client_id} initializing new {stream_mode} stream {stream_key} from {client_ip}")

                try:
                    if reusable_monitor_session:
                        logger.info("Using playback URL from monitoring session for HLS stream")
                        api_key = os.getenv('API_KEY')
                        monitor_session = reusable_monitor_session.get("session") or {}
                        monitor_playback_session_id = monitor_session.get('playback_session_id')
                        if not monitor_playback_session_id:
                            monitor_playback_session_id = f"hls-reuse-{stream_key[:16]}-{int(time.time())}"
                        session_info = {
                            'playback_session_id': monitor_playback_session_id,
                            'stat_url': monitor_session.get('stat_url') or '',
                            'command_url': monitor_session.get('command_url') or '',
                            'is_live': 1,
                            'owns_engine_session': False,
                        }
                    else:
                        hls_url = f"http://{selected_engine.host}:{selected_engine.port}/ace/manifest.m3u8"
                        pid = str(uuid4())
                        params = _build_engine_stream_params(
                            input_type,
                            input_value,
                            pid=pid,
                            file_indexes=normalized_file_indexes,
                            seekback=normalized_seekback,
                        )

                        logger.info(f"Requesting HLS stream from engine: {hls_url}")
                        response = requests.get(hls_url, params=params, timeout=10)
                        response.raise_for_status()
                        data = response.json()

                        if data.get("error"):
                            error_msg = data['error']
                            logger.error(f"AceStream engine returned error: {error_msg}")
                            raise HTTPException(status_code=500, detail=f"AceStream engine error: {error_msg}")

                        resp_data = data.get("response", {})
                        playback_url = resp_data.get("playback_url")
                        if not playback_url:
                            logger.error("No playback_url in AceStream response")
                            raise HTTPException(status_code=500, detail="No playback URL in engine response")

                        bitrate = int(resp_data.get("bitrate") or 0)
                        logger.info(f"HLS playback URL: {playback_url} bitrate={bitrate} bps")

                        api_key = os.getenv('API_KEY')
                        session_info = {
                            "playback_session_id": resp_data.get("playback_session_id"),
                            "stat_url": resp_data.get("stat_url"),
                            "command_url": resp_data.get("command_url"),
                            "is_live": resp_data.get("is_live", 1),
                            "owns_engine_session": True
                        }

                        hls_proxy.initialize_channel(
                            channel_id=stream_key,
                            playback_url=playback_url,
                            engine_host=selected_engine.host,
                            engine_port=selected_engine.port,
                            engine_container_id=selected_engine.container_id,
                            session_info=session_info,
                            engine_api_port=selected_engine.api_port,
                            api_key=api_key,
                            stream_key_type=input_type,
                            file_indexes=normalized_file_indexes,
                            seekback=normalized_seekback,
                            bitrate=bitrate
                        )

                    # Record activity immediately to acknowledge the client before prebuffering wait
                    manifest_seconds_behind = hls_proxy.get_manifest_buffer_seconds_behind(stream_key)
                    hls_proxy.record_client_activity(
                        stream_key,
                        client_ip,
                        client_id=client_identity,
                        user_agent=user_agent,
                        request_kind="manifest",
                        bytes_sent=500,  # Estimated manifest size
                        buffer_seconds_behind=manifest_seconds_behind,
                    )

                    elapsed = time.perf_counter() - request_started_at
                    observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                    observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                    return StreamingResponse(
                        hls_proxy.get_manifest_stream(stream_key, client_id=client_identity),
                        media_type="application/vnd.apple.mpegurl",
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Connection": "keep-alive",
                        }
                    )

                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to request HLS stream from engine: {e}")
                    raise HTTPException(status_code=503, detail=f"Engine communication error: {str(e)}")
                except TimeoutError as e:
                    logger.error(f"Timeout getting HLS manifest: {e}")
                    raise HTTPException(status_code=503, detail=f"Timeout waiting for stream buffer: {str(e)}")
            else:
                # API mode: expose HLS by segmenting MPEG-TS playback with local FFmpeg.
                existing_manifest = await hls_segmenter_service.get_or_wait_manifest(stream_key, timeout_s=15.0)
                if existing_manifest:
                    session_meta = hls_segmenter_service.get_session_metadata(stream_key) or {}
                    existing_stream_id = str(session_meta.get("stream_id") or "").strip()
                    if not existing_stream_id:
                        if not hls_proxy.has_channel(stream_key):
                            stream_id = await _register_api_hls_stream_if_missing(
                                container_id=str(session_meta.get("container_id") or ""),
                                engine_host=str(session_meta.get("engine_host") or ""),
                                engine_port=_safe_int(session_meta.get("engine_port"), default=0),
                                engine_api_port=_safe_int(session_meta.get("engine_api_port"), default=0),
                                playback_session_id=str(session_meta.get("playback_session_id") or ""),
                                stat_url=str(session_meta.get("stat_url") or ""),
                                command_url=str(session_meta.get("command_url") or ""),
                                is_live=_safe_int(session_meta.get("is_live"), default=1),
                                bitrate=_safe_int(session_meta.get("bitrate"), default=0),
                                stream_key=stream_key,
                                input_type=input_type,
                                normalized_file_indexes=normalized_file_indexes,
                                normalized_seekback=normalized_seekback,
                            )
                            if stream_id:
                                hls_segmenter_service.set_session_metadata(stream_key, {"stream_id": stream_id})

                    logger.debug("Reusing external HLS segmenter for stream %s", stream_key)
                    hls_segmenter_service.record_activity(stream_key)
                    manifest_content = await hls_segmenter_service.read_manifest(stream_key, rewrite=True)
                    manifest_bytes = manifest_content.encode('utf-8')
                    hls_segmenter_service.record_client_activity(
                        stream_key,
                        client_identity,
                        client_ip,
                        user_agent,
                        request_kind="manifest",
                        bytes_sent=len(manifest_bytes),
                        buffer_seconds_behind=hls_segmenter_service.estimate_manifest_buffer_seconds_behind(stream_key),
                    )

                    elapsed = time.perf_counter() - request_started_at
                    observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                    observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                    return StreamingResponse(
                        iter([manifest_bytes]),
                        media_type="application/vnd.apple.mpegurl",
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Connection": "keep-alive",
                        }
                    )

                if reusable_monitor_session:
                    monitor_engine = reusable_monitor_session.get("engine") or {}
                    monitor_session = reusable_monitor_session.get("session") or {}
                    selected_engine = SimpleNamespace(
                        container_id=monitor_engine.get("container_id"),
                        host=monitor_engine.get("host"),
                        port=monitor_engine.get("port"),
                        api_port=monitor_engine.get("api_port") or 62062,
                        forwarded=bool(monitor_engine.get("forwarded")),
                    )
                    playback_url = str(monitor_session.get("playback_url") or "").strip()
                    if not playback_url:
                        raise HTTPException(status_code=500, detail="Monitor session has no playback URL")
                    start_info = {
                        "playback_session_id": str(monitor_session.get("playback_session_id") or f"api-hls-reuse-{int(time.time())}"),
                        "stat_url": str(monitor_session.get("stat_url") or ""),
                        "command_url": str(monitor_session.get("command_url") or ""),
                        "is_live": int(monitor_session.get("is_live") or 1),
                        "bitrate": int(monitor_session.get("bitrate") or 0),
                    }
                    playback_url = str(monitor_session.get("playback_url") or "").strip()
                    if not playback_url:
                        raise HTTPException(status_code=500, detail="Monitor session has no playback URL")
                    legacy_api_client = None
                    logger.info(
                        "Reusing monitor HLS session for stream %s: bitrate=%s bps",
                        stream_key,
                        start_info["bitrate"]
                    )
                else:
                    engines_count = max(1, len(state.list_engines()))
                    max_engine_attempts = min(2, engines_count)
                    excluded_engine_penalties: Dict[str, int] = {}
                    last_start_error: Optional[HTTPException] = None
                    selected_engine = None
                    legacy_api_client = None
                    start_info = {}
                    playback_url = ""

                    session_meta = hls_segmenter_service.get_session_metadata(stream_key) or {}
                    migrated_seekback = session_meta.get("seekback")
                    if migrated_seekback is not None and int(migrated_seekback) > 0:
                        if int(migrated_seekback) != normalized_seekback:
                            logger.info(f"Using migrated seekback {migrated_seekback}s instead of initial {normalized_seekback}s for API-HLS stream {stream_key}")
                            normalized_seekback = int(migrated_seekback)

                    for attempt_idx in range(max_engine_attempts):
                        selected_engine, current_load = select_best_engine(
                            additional_load_by_engine=excluded_engine_penalties,
                        )
                        reservation_engine_id = selected_engine.container_id

                        logger.info(
                            "Starting API-mode HLS session on engine %s for stream %s (attempt %s/%s)",
                            selected_engine.container_id[:12],
                            stream_key,
                            attempt_idx + 1,
                            max_engine_attempts,
                        )

                        client = AceLegacyApiClient(
                            host=selected_engine.host,
                            port=selected_engine.api_port or 62062,
                            connect_timeout=10,
                            response_timeout=10,
                        )

                        try:
                            await asyncio.to_thread(client.connect)
                            await asyncio.to_thread(client.authenticate)

                            start_mode = input_type
                            start_payload = input_value

                            loadresp, resolved_mode = await asyncio.to_thread(
                                client.resolve_content,
                                input_value,
                                "0",
                                input_type,
                            )
                            if resolved_mode != "direct_url":
                                status_code = loadresp.get("status")
                                if status_code not in (1, 2):
                                    message = loadresp.get("message") or "content unavailable"
                                    raise HTTPException(status_code=503, detail=f"LOADASYNC status={status_code}: {message}")
                            start_mode = resolved_mode

                            start_info = await asyncio.to_thread(
                                client.start_stream,
                                start_payload,
                                start_mode,
                                "output_format=http",
                                normalized_file_indexes,
                                normalized_seekback,
                            )
                            playback_url = str(start_info.get("url") or "").strip()
                            if not playback_url:
                                raise HTTPException(status_code=500, detail="No playback URL returned by API START")

                            bitrate = int(start_info.get("bitrate") or 0)
                            logger.info(
                                "Obtained API session for stream %s: engine=%s bitrate=%s bps",
                                stream_key,
                                selected_engine.container_id[:12],
                                bitrate
                            )
                            legacy_api_client = client
                            break
                        except HTTPException as e:
                            last_start_error = e
                            excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                            rollback_reservation(selected_engine.container_id)
                            try:
                                await asyncio.to_thread(client.shutdown)
                            except Exception:
                                pass
                            if attempt_idx + 1 >= max_engine_attempts:
                                raise
                            logger.warning(
                                "API-mode HLS startup failed on engine %s (attempt %s/%s): %s. Retrying with another engine.",
                                selected_engine.container_id[:12],
                                attempt_idx + 1,
                                max_engine_attempts,
                                e.detail,
                            )
                        except AceLegacyApiError as e:
                            last_start_error = HTTPException(status_code=503, detail=str(e))
                            excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                            rollback_reservation(selected_engine.container_id)
                            try:
                                await asyncio.to_thread(client.shutdown)
                            except Exception:
                                pass
                            if attempt_idx + 1 >= max_engine_attempts:
                                raise last_start_error
                            logger.warning(
                                "API-mode HLS legacy API error on engine %s (attempt %s/%s): %s. Retrying with another engine.",
                                selected_engine.container_id[:12],
                                attempt_idx + 1,
                                max_engine_attempts,
                                e,
                            )

                    if not playback_url:
                        if last_start_error:
                            raise last_start_error
                        raise HTTPException(status_code=503, detail="Unable to start API-mode HLS stream")

                logger.info("Starting external HLS segmenter for stream %s", stream_key)
                segmenter_metadata = {
                    "playback_session_id": str(start_info.get("playback_session_id") or f"api-hls-{int(time.time())}"),
                    "stat_url": str(start_info.get("stat_url") or ""),
                    "command_url": str(start_info.get("command_url") or ""),
                    "is_live": int(start_info.get("is_live") or 1),
                    "bitrate": int(start_info.get("bitrate") or 0),
                    "container_id": str(selected_engine.container_id or ""),
                    "engine_host": str(selected_engine.host or ""),
                    "engine_port": int(selected_engine.port or 0),
                    "engine_api_port": int(selected_engine.api_port or 0),
                    "stream_key_type": input_type,
                    "file_indexes": normalized_file_indexes,
                    "seekback": normalized_seekback,
                    "control_client": legacy_api_client,
                    "bitrate": bitrate,
                }
                try:
                    await hls_segmenter_service.start_segmenter(stream_key, playback_url, metadata=segmenter_metadata)
                except (FileNotFoundError, RuntimeError, TimeoutError) as e:
                    if legacy_api_client is not None:
                        try:
                            await asyncio.to_thread(legacy_api_client.shutdown)
                        except Exception:
                            pass
                    logger.error("Failed to initialize external HLS segmenter for stream %s: %s", stream_key, e)
                    raise HTTPException(status_code=503, detail=f"Failed to initialize HLS segmenter: {e}")

                stream_id = await _register_api_hls_stream_if_missing(
                    container_id=str(selected_engine.container_id or ""),
                    engine_host=str(selected_engine.host or ""),
                    engine_port=int(selected_engine.port or 0),
                    engine_api_port=int(selected_engine.api_port or 0),
                    playback_session_id=str(segmenter_metadata.get("playback_session_id") or ""),
                    stat_url=str(segmenter_metadata.get("stat_url") or ""),
                    command_url=str(segmenter_metadata.get("command_url") or ""),
                    is_live=int(segmenter_metadata.get("is_live") or 1),
                    stream_key=stream_key,
                    input_type=input_type,
                    normalized_file_indexes=normalized_file_indexes,
                    normalized_seekback=normalized_seekback,
                )
                if stream_id:
                    hls_segmenter_service.set_session_metadata(stream_key, {"stream_id": stream_id})

                hls_segmenter_service.record_activity(stream_key)
                hls_segmenter_service.record_client_activity(
                    stream_key,
                    client_identity,
                    client_ip,
                    user_agent,
                    request_kind="manifest",
                    bytes_sent=500,
                    buffer_seconds_behind=hls_segmenter_service.estimate_manifest_buffer_seconds_behind(stream_key),
                )

                elapsed = time.perf_counter() - request_started_at
                observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                return StreamingResponse(
                    hls_segmenter_service.read_manifest_stream(stream_key, client_id=client_identity, rewrite=True),
                    media_type="application/vnd.apple.mpegurl",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Connection": "keep-alive",
                    }
                )
        else:
            # TS mode - use existing ts_proxy architecture
            if reusable_monitor_session:
                monitor_engine = reusable_monitor_session.get("engine") or {}
                monitor_session = reusable_monitor_session.get("session") or {}
                selected_engine = SimpleNamespace(
                    container_id=monitor_engine.get("container_id"),
                    host=monitor_engine.get("host"),
                    port=monitor_engine.get("port"),
                    api_port=monitor_engine.get("api_port") or 62062,
                    forwarded=bool(monitor_engine.get("forwarded")),
                )
                start_info = {
                    "playback_session_id": str(monitor_session.get("playback_session_id") or f"api-ts-reuse-{int(time.time())}"),
                    "stat_url": str(monitor_session.get("stat_url") or ""),
                    "command_url": str(monitor_session.get("command_url") or ""),
                    "is_live": int(monitor_session.get("is_live") or 1),
                }
                playback_url = str(monitor_session.get("playback_url") or "").strip()
                bitrate = int(monitor_session.get("bitrate") or 0)
                legacy_api_client = None
                logger.info(
                    "Using monitor session %s for content_id=%s bitrate=%s bps",
                    reusable_monitor_session.get("monitor_id"),
                    stream_key,
                    bitrate
                )
            elif control_mode == PROXY_MODE_API:
                engines_count = max(1, len(state.list_engines()))
                max_engine_attempts = min(2, engines_count)
                excluded_engine_penalties: Dict[str, int] = {}
                last_start_error: Optional[HTTPException] = None
                selected_engine = None
                legacy_api_client = None
                start_info = {}
                playback_url = ""
                bitrate = 0

                for attempt_idx in range(max_engine_attempts):
                    selected_engine, current_load = select_best_engine(
                        additional_load_by_engine=excluded_engine_penalties,
                    )
                    reservation_engine_id = selected_engine.container_id

                    logger.info(
                        "Starting API-mode %s session on engine %s for stream %s (attempt %s/%s)",
                        stream_mode,
                        selected_engine.container_id[:12],
                        stream_key,
                        attempt_idx + 1,
                        max_engine_attempts,
                    )

                    client = AceLegacyApiClient(
                        host=selected_engine.host,
                        port=selected_engine.api_port or 62062,
                        connect_timeout=10,
                        response_timeout=10,
                    )

                    try:
                        await asyncio.to_thread(client.connect)
                        await asyncio.to_thread(client.authenticate)

                        loadresp, resolved_mode = await asyncio.to_thread(
                            client.resolve_content,
                            input_value,
                            "0",
                            input_type,
                        )
                        if resolved_mode != "direct_url":
                            status_code = loadresp.get("status")
                            if status_code not in (1, 2):
                                message = loadresp.get("message") or "content unavailable"
                                raise HTTPException(status_code=503, detail=f"LOADASYNC status={status_code}: {message}")

                        start_mode = resolved_mode
                        start_payload = input_value
                        if loadresp.get("infohash"):
                            start_mode = "infohash"
                            start_payload = loadresp.get("infohash")

                        start_info = await asyncio.to_thread(
                            client.start_stream,
                            start_payload,
                            start_mode,
                            "output_format=http",
                            normalized_file_indexes,
                            normalized_seekback,
                        )
                        playback_url = str(start_info.get("url") or "").strip()
                        if not playback_url:
                            raise HTTPException(status_code=500, detail="No playback URL returned by API START")

                        legacy_api_client = client
                        bitrate = int(start_info.get("bitrate") or 0)
                        logger.info(
                            "Obtained API session for stream %s: engine=%s bitrate=%s bps",
                            stream_key,
                            selected_engine.container_id[:12],
                            bitrate
                        )
                        break
                    except HTTPException as e:
                        last_start_error = e
                        excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                        rollback_reservation(selected_engine.container_id)
                        try:
                            await asyncio.to_thread(client.shutdown)
                        except Exception:
                            pass
                        if attempt_idx + 1 >= max_engine_attempts:
                            raise
                        logger.warning(
                            "API-mode %s startup failed on engine %s (attempt %s/%s): %s. Retrying.",
                            stream_mode,
                            selected_engine.container_id[:12],
                            attempt_idx + 1,
                            max_engine_attempts,
                            e.detail,
                        )
                    except AceLegacyApiError as e:
                        last_start_error = HTTPException(status_code=503, detail=str(e))
                        excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                        rollback_reservation(selected_engine.container_id)
                        try:
                            await asyncio.to_thread(client.shutdown)
                        except Exception:
                            pass
                        if attempt_idx + 1 >= max_engine_attempts:
                            raise last_start_error
                        logger.warning(
                            "API-mode %s legacy API error on engine %s (attempt %s/%s): %s. Retrying.",
                            stream_mode,
                            selected_engine.container_id[:12],
                            attempt_idx + 1,
                            max_engine_attempts,
                            e,
                        )

                if not playback_url:
                    if last_start_error:
                        raise last_start_error
                    raise HTTPException(status_code=503, detail=f"Unable to start API-mode {stream_mode} stream")
            else:
                # HTTP mode - simple engine selection
                selected_engine, current_load = select_best_engine()
                reservation_engine_id = selected_engine.container_id
                start_info = {}
                playback_url = None
                legacy_api_client = None

            logger.info(
                f"Client {client_id} connecting to {stream_mode} stream {stream_key} from {client_ip} "
                f"on engine {selected_engine.container_id[:12]}"
            )

            proxy = ProxyManager.get_instance()

            success = proxy.start_stream(
                content_id=stream_key,
                engine_host=selected_engine.host,
                engine_port=selected_engine.port,
                engine_api_port=selected_engine.api_port,
                engine_container_id=selected_engine.container_id,
                existing_session=reusable_monitor_session,
                source_input=input_value,
                source_input_type=input_type,
                file_indexes=normalized_file_indexes,
                seekback=normalized_seekback,
                playback_url=playback_url,
                playback_session_id=start_info.get("playback_session_id"),
                stat_url=start_info.get("stat_url"),
                command_url=start_info.get("command_url"),
                is_live=start_info.get("is_live"),
                ace_api_client=legacy_api_client,
                bitrate=bitrate,
            )

            if not success:
                if legacy_api_client:
                    try:
                        await asyncio.to_thread(legacy_api_client.shutdown)
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=500,
                    detail="Failed to start stream session"
                )

            generator = create_stream_generator(
                content_id=stream_key,
                client_id=client_id,
                client_ip=client_ip,
                client_user_agent=user_agent,
                stream_initializing=(control_mode == PROXY_MODE_API),
                seekback=normalized_seekback
            )

            elapsed = time.perf_counter() - request_started_at
            observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
            observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

            ts_iterator = generator.generate()

            active_task = None

            async def _guarded_ts_stream():
                nonlocal active_task
                try:
                    while True:
                        if await request.is_disconnected():
                            break

                        if active_task is None:
                            active_task = asyncio.create_task(asyncio.to_thread(next, ts_iterator, None))

                        try:
                            chunk = await asyncio.wait_for(asyncio.shield(active_task), timeout=1.0)
                            active_task = None

                            if chunk is None:
                                break
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            active_task = None
                            raise

                        if chunk:
                            yield chunk
                except asyncio.CancelledError:
                    raise
                finally:
                    if active_task and not active_task.done():
                        active_task.cancel()
                    with suppress(Exception):
                        ts_iterator.close()

            return StreamingResponse(
                _guarded_ts_stream(),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Connection": "keep-alive",
                }
            )

    except HTTPException as exc:
        rollback_reservation()
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=False, status_code=exc.status_code)
        raise
    except Exception as e:
        rollback_reservation()
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=False, status_code=500)
        logger.error(f"Unexpected error in ace_getstream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# ---------------------------------------------------------------------------
# /ace/hls/{content_id}/segment/{segment_path:path}
# ---------------------------------------------------------------------------

@router.get(
    "/ace/hls/{content_id}/segment/{segment_path:path}",
    tags=["Proxy"],
    summary="Fetch HLS segment",
    description="Returns a buffered HLS segment for an active AceStream channel.",
    responses={
        200: {"description": "Segment data"},
        404: {"description": "Segment not found"},
        500: {"description": "Segment retrieval error"},
    },
)
async def ace_hls_segment(
    content_id: str,
    segment_path: str,
    request: Request,
):
    """Proxy endpoint for HLS segments."""
    content_id = sanitize_stream_id(content_id)

    from app.proxy.hls_proxy import HLSProxyServer

    logger.debug(f"HLS segment request: content_id={content_id}, segment={segment_path}")
    request_started_at = time.perf_counter()

    try:
        hls_proxy = HLSProxyServer.get_instance()

        client_ip = get_client_ip(request)
        user_agent = request.headers.get('user-agent', 'unknown')
        client_identity = f"{client_ip}:{hashlib.sha1(user_agent.encode('utf-8', errors='ignore')).hexdigest()[:12]}"

        sequence = None
        try:
            seq_match = re.search(r'(\d+)', segment_path)
            if seq_match:
                sequence = int(seq_match.group(1))
        except Exception:
            pass

        buffer = hls_proxy.stream_buffers.get(content_id)
        raw_segment_size = len(buffer[sequence]) if buffer and sequence in buffer else 0

        hls_proxy.record_client_activity(
            content_id,
            client_ip,
            client_id=client_identity,
            user_agent=user_agent,
            request_kind="segment",
            bytes_sent=raw_segment_size,
            chunks_sent=1,
            sequence=sequence,
            buffer_seconds_behind=hls_proxy.get_segment_buffer_seconds_behind(content_id, sequence),
        )
        observe_proxy_egress_bytes("HLS", raw_segment_size)

        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/ace/hls/segment", elapsed, success=True, status_code=200)
        observe_proxy_ttfb("HLS", "/ace/hls/segment", elapsed)

        return StreamingResponse(
            hls_proxy.get_segment_stream(content_id, segment_path),
            media_type="video/MP2T",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Connection": "keep-alive",
            }
        )
    except ValueError as e:
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/ace/hls/segment", elapsed, success=False, status_code=404)
        logger.warning(f"Segment not found: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/ace/hls/segment", elapsed, success=False, status_code=500)
        logger.error(f"Error serving HLS segment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Segment error: {str(e)}")


# ---------------------------------------------------------------------------
# /api/v1/hls/{monitor_id}/{segment_filename}
# ---------------------------------------------------------------------------

@router.get(
    "/api/v1/hls/{monitor_id}/{segment_filename}",
    tags=["Proxy"],
    summary="Serve FFmpeg-generated HLS segment",
    description="Serves local HLS .ts segments produced by the API-mode external segmenter.",
    responses={
        200: {"description": "Segment data"},
        404: {"description": "Segment not found"},
    },
)
async def api_hls_segment_file(monitor_id: str, segment_filename: str, request: Request):
    from ...data_plane.hls_segmenter import hls_segmenter_service

    monitor_id = sanitize_stream_id(monitor_id)

    request_started_at = time.perf_counter()

    try:
        path = hls_segmenter_service.get_segment_file_path(monitor_id, segment_filename)
        if not path or not path.exists() or not path.is_file():
            elapsed = time.perf_counter() - request_started_at
            observe_proxy_request("HLS", "/api/v1/hls/segment", elapsed, success=False, status_code=404)
            raise HTTPException(status_code=404, detail="HLS segment not found")

        hls_segmenter_service.record_activity(monitor_id)
        client_ip = get_client_ip(request)
        user_agent = request.headers.get('user-agent', 'unknown')
        try:
            segment_size = int(path.stat().st_size)
        except OSError:
            segment_size = 0
        client_identity = f"{client_ip}:{hashlib.sha1(user_agent.encode('utf-8', errors='ignore')).hexdigest()[:12]}"

        sequence = None
        try:
            seq_match = re.search(r'(\d+)', segment_filename)
            if seq_match:
                sequence = int(seq_match.group(1))
        except Exception:
            pass

        hls_segmenter_service.record_client_activity(
            monitor_id,
            client_identity,
            client_ip,
            user_agent,
            request_kind="segment",
            bytes_sent=segment_size,
            chunks_sent=1,
            sequence=sequence,
            buffer_seconds_behind=hls_segmenter_service.estimate_manifest_buffer_seconds_behind(monitor_id),
        )

        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/api/v1/hls/segment", elapsed, success=True, status_code=200)
        observe_proxy_ttfb("HLS", "/api/v1/hls/segment", elapsed)

        return StreamingResponse(
            hls_segmenter_service.read_segment_stream(monitor_id, segment_filename),
            media_type="video/MP2T"
        )
    except HTTPException:
        raise
    except Exception as e:
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/api/v1/hls/segment", elapsed, success=False, status_code=500)
        logger.error(f"Error serving API-mode HLS segment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"HLS segment error: {str(e)}")


# ---------------------------------------------------------------------------
# /ace/manifest.m3u8
# ---------------------------------------------------------------------------

@router.get(
    "/ace/manifest.m3u8",
    tags=["Proxy"],
    summary="HLS manifest entrypoint",
    description="Serves HLS manifests for both HTTP and API control modes.",
    responses={
        200: {"description": "HLS manifest"},
        400: {"description": "Invalid request parameters"},
    },
)
async def ace_manifest(
    id: Optional[str] = Query(None, description="AceStream content ID (PID/content_id)"),
    infohash: Optional[str] = Query(None, description="AceStream infohash"),
    torrent_url: Optional[str] = Query(None, description="Direct .torrent URL"),
    direct_url: Optional[str] = Query(None, description="Direct media/magnet URL"),
    raw_data: Optional[str] = Query(None, description="Raw torrent file data"),
    file_indexes: Optional[str] = Query("0", description="Comma-separated torrent file indexes (for example: 0 or 0,2)"),
    seekback: Optional[int] = Query(None, description="Deprecated alias for live_delay"),
    live_delay: Optional[int] = Query(None, description="Optional startup delay in seconds behind live edge"),
    request: Request = None,
):
    """Proxy endpoint for AceStream HLS streams (M3U8)."""
    return await ace_getstream(
        id=id,
        infohash=infohash,
        torrent_url=torrent_url,
        direct_url=direct_url,
        raw_data=raw_data,
        file_indexes=file_indexes,
        seekback=seekback,
        live_delay=live_delay,
        request=request,
    )


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
