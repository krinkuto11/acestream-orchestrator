"""Stream endpoints."""
import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from ...core.config import cfg
from ...api.auth import require_api_key
from ...services.state import state
from ...observability.event_logger import event_logger
from ...proxy.utils import sanitize_stream_id, get_client_ip
from ...models.schemas import StreamState, StreamStatSnapshot, StreamStartedEvent, StreamEndedEvent
from ...api.sse_helpers import _format_sse_message, _validate_sse_api_key

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/streams", response_model=List[StreamState])
def get_streams(
    status: Optional[str] = Query(None, pattern="^(started|ended|pending_failover)$"),
    container_id: Optional[str] = None,
):
    """Get streams. By default, returns all streams."""
    streams = state.list_streams_with_stats(status=status, container_id=container_id)

    from ...data_plane.client_tracker import client_tracking_service

    for stream in streams:
        if stream.status == "started" and getattr(stream, "key", None):
            with suppress(Exception):
                stream.clients = client_tracking_service.get_stream_clients(stream.key)

    return streams


@router.get("/streams/{stream_id}/stats", response_model=List[StreamStatSnapshot])
def get_stream_stats(stream_id: str, since: Optional[datetime] = None):
    stream_id = sanitize_stream_id(stream_id)
    snaps = state.get_stream_stats(stream_id)
    if since:
        snaps = [x for x in snaps if x.ts >= since]
    return snaps


@router.get("/streams/{stream_id}/extended-stats")
async def get_stream_extended_stats(stream_id: str):
    stream_id = sanitize_stream_id(stream_id)
    """Get extended statistics for a stream when stat_url is available (HTTP control mode)."""
    from ...utils.acestream_api import get_stream_extended_stats
    from ...persistence.cache import get_cache

    cache = get_cache()
    cache_key = f"stream_extended_stats:{stream_id}"
    cached_stats = cache.get(cache_key)
    if cached_stats is not None:
        return cached_stats

    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    if not str(getattr(stream, "stat_url", "") or "").strip():
        unavailable = {
            "available": False,
            "reason": "extended_stats_disabled_in_api_mode",
        }
        cache.set(cache_key, unavailable, ttl=300.0)
        return unavailable

    content_cache_key = f"stream_extended_stats:content:{stream.key}"
    cached_content_stats = cache.get(content_cache_key)
    if cached_content_stats is not None:
        cache.set(cache_key, cached_content_stats, ttl=3600.0)
        return cached_content_stats

    extended_stats = await get_stream_extended_stats(stream.stat_url)

    if extended_stats is None:
        unavailable = {"available": False}
        cache.set(cache_key, unavailable, ttl=30.0)
        cache.set(content_cache_key, unavailable, ttl=30.0)
        return unavailable

    cache.set(cache_key, extended_stats, ttl=3600.0)
    cache.set(content_cache_key, extended_stats, ttl=3600.0)

    return extended_stats


@router.get("/streams/{stream_id}/livepos")
async def get_stream_livepos(stream_id: str):
    stream_id = sanitize_stream_id(stream_id)
    """Get live position data for a stream from stat URL or API-mode probe."""

    def _to_int(value):
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _build_livepos_response(livepos, is_live, status_payload=None):
        if not livepos:
            return {
                "has_livepos": False,
                "is_live": bool(is_live),
            }

        normalized = {
            "pos": livepos.get("pos"),
            "live_first": livepos.get("live_first") or livepos.get("first_ts") or livepos.get("first"),
            "live_last": livepos.get("live_last") or livepos.get("last_ts") or livepos.get("last"),
            "first_ts": livepos.get("first_ts") or livepos.get("first"),
            "last_ts": livepos.get("last_ts") or livepos.get("last"),
            "buffer_pieces": livepos.get("buffer_pieces"),
        }

        pos_i = _to_int(normalized.get("pos"))
        first_i = _to_int(normalized.get("live_first"))
        last_i = _to_int(normalized.get("live_last"))

        live_delay_seconds = None
        dvr_window_seconds = None
        playback_offset_seconds = None
        if pos_i is not None and last_i is not None:
            live_delay_seconds = max(0, last_i - pos_i)
        if first_i is not None and last_i is not None:
            dvr_window_seconds = max(0, last_i - first_i)
        if pos_i is not None and first_i is not None:
            playback_offset_seconds = max(0, pos_i - first_i)

        performance = {
            "live_delay_seconds": live_delay_seconds,
            "dvr_window_seconds": dvr_window_seconds,
            "playback_offset_seconds": playback_offset_seconds,
        }

        if status_payload:
            performance.update(
                {
                    "status": status_payload.get("status_text") or status_payload.get("status"),
                    "peers": status_payload.get("peers"),
                    "http_peers": status_payload.get("http_peers"),
                    "speed_down": status_payload.get("speed_down"),
                    "http_speed_down": status_payload.get("http_speed_down"),
                    "speed_up": status_payload.get("speed_up"),
                    "downloaded": status_payload.get("downloaded"),
                    "http_downloaded": status_payload.get("http_downloaded"),
                    "uploaded": status_payload.get("uploaded"),
                    "total_progress": status_payload.get("total_progress"),
                    "immediate_progress": status_payload.get("immediate_progress"),
                }
            )

        return {
            "has_livepos": True,
            "is_live": bool(is_live),
            "livepos": normalized,
            "performance": performance,
        }

    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    if stream.stat_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(stream.stat_url)
                response.raise_for_status()
                data = response.json()

                payload = data.get("response")
                if not payload:
                    raise HTTPException(status_code=503, detail="No response data from stat URL")

                return _build_livepos_response(
                    payload.get("livepos"),
                    payload.get("is_live", 0) == 1,
                    status_payload=payload,
                )
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch livepos for stream {stream_id}: {e}")
            raise HTTPException(status_code=503, detail=f"Failed to fetch livepos data: {str(e)}")
        except Exception as e:
            logger.error(f"Error processing livepos for stream {stream_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error processing livepos data: {str(e)}")

    try:
        from ...proxy.server import ProxyServer
        from ...data_plane.hls_segmenter import hls_segmenter_service
        from ...data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service

        proxy = ProxyServer.get_instance()
        manager = proxy.stream_managers.get(stream.key) if proxy else None
        probe = None

        if manager:
            probe = await asyncio.to_thread(
                manager.collect_legacy_stats_probe,
                1,
                1.0,
                True,
            )

        if not probe:
            probe = await asyncio.to_thread(
                hls_segmenter_service.collect_legacy_stats_probe,
                stream.key,
                1,
                1.0,
                True,
            )

        if not probe:
            reusable = await legacy_stream_monitoring_service.get_reusable_session_for_content(stream.key)
            if reusable:
                probe = reusable.get("latest_status") or None

        if not probe:
            raise HTTPException(status_code=503, detail="No legacy probe data available")

        livepos = probe.get("livepos") or {}
        return _build_livepos_response(
            livepos,
            is_live=(stream.is_live is True),
            status_payload=probe,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing legacy livepos for stream {stream_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing livepos data: {str(e)}")


@router.post("/events/stream_started", response_model=StreamState, dependencies=[Depends(require_api_key)])
def ev_stream_started(evt: StreamStartedEvent):
    result = state.on_stream_started(evt)
    event_logger.log_event(
        event_type="stream",
        category="started",
        message=f"Stream started: {evt.stream.key_type}={evt.stream.key[:16]}...",
        details={
            "key_type": evt.stream.key_type,
            "key": evt.stream.key,
            "engine_port": evt.engine.port,
            "is_live": bool(evt.session.is_live),
        },
        container_id=evt.container_id,
        stream_id=result.id,
    )
    return result


@router.post("/events/stream_ended", dependencies=[Depends(require_api_key)])
def ev_stream_ended(evt: StreamEndedEvent, bg: BackgroundTasks):
    st = state.on_stream_ended(evt)
    if st:
        event_logger.log_event(
            event_type="stream",
            category="ended",
            message=f"Stream ended: {st.id[:16]}... (reason: {evt.reason or 'unknown'})",
            details={
                "reason": evt.reason,
                "key_type": st.key_type,
                "key": st.key,
            },
            container_id=st.container_id,
            stream_id=st.id,
        )

    if cfg.AUTO_DELETE and st:

        def _auto():
            from ...control_plane.provisioner import stop_container
            from ...control_plane.health import list_managed
            from ...control_plane.provisioner import HOST_LABEL_HTTP
            from ...control_plane.autoscaler import can_stop_engine, ensure_minimum
            import urllib.parse

            cid = st.container_id

            bypass_grace = cfg.ENGINE_GRACE_PERIOD_S <= 5

            if can_stop_engine(cid, bypass_grace_period=bypass_grace):
                stopped_container_id = None
                for i in range(3):
                    try:
                        stop_container(cid)
                        stopped_container_id = cid
                        break
                    except Exception:
                        try:
                            for c in list_managed():
                                if (c.labels or {}).get("stream_id") == st.id:
                                    if can_stop_engine(c.id, bypass_grace_period=bypass_grace):
                                        stop_container(c.id)
                                        stopped_container_id = c.id
                                        break
                                pu = urllib.parse.urlparse(st.stat_url)
                                host_port = pu.port
                                if (c.labels or {}).get(HOST_LABEL_HTTP) == str(host_port):
                                    if can_stop_engine(c.id, bypass_grace_period=bypass_grace):
                                        stop_container(c.id)
                                        stopped_container_id = c.id
                                        break
                        except Exception:
                            pass
                        import time as _time

                        _time.sleep(1 * (i + 1))

                if stopped_container_id:
                    state.remove_engine(stopped_container_id)
                    ensure_minimum()
            else:
                logger.debug(f"Engine {cid[:12]} cannot be stopped, deferring shutdown")

        bg.add_task(_auto)
    return {"updated": bool(st), "stream": st}


@router.delete("/streams/{stream_id}", dependencies=[Depends(require_api_key)])
async def stop_stream(stream_id: str):
    stream_id = sanitize_stream_id(stream_id)
    """Stop a stream by calling its command URL with method=stop."""
    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    if stream.status != "started":
        raise HTTPException(status_code=400, detail=f"Stream is not active (status: {stream.status})")

    if not stream.command_url:
        raise HTTPException(status_code=400, detail="Stream has no command URL")

    stop_url = f"{stream.command_url}?method=stop"
    logger.info(f"Stopping stream {stream_id} via command URL: {stop_url}")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(stop_url)
            if response.status_code >= 300:
                logger.warning(f"Stop command returned non-success status {response.status_code} for stream {stream_id}")
    except Exception as e:
        logger.warning(f"Failed to send stop command for stream {stream_id}: {e}")

    logger.info(f"Ending stream {stream_id} (reason: manual_stop_via_api)")
    state.on_stream_ended(
        StreamEndedEvent(
            container_id=stream.container_id,
            stream_id=stream_id,
            reason="manual_stop_via_api",
        )
    )

    return {"message": "Stream stopped successfully", "stream_id": stream_id}


@router.post("/streams/batch-stop", dependencies=[Depends(require_api_key)])
async def batch_stop_streams(command_urls: List[str]):
    """Batch stop multiple streams by calling their command URLs with method=stop."""
    results = []

    for command_url in command_urls:
        result = {
            "command_url": command_url,
            "success": False,
            "message": "",
            "stream_id": None,
        }

        try:
            stream = None
            for s in state.list_streams():
                if s.command_url == command_url:
                    stream = s
                    break

            if not stream:
                result["message"] = "Stream not found"
                results.append(result)
                continue

            result["stream_id"] = stream.id

            if stream.status != "started":
                result["message"] = f"Stream is not active (status: {stream.status})"
                results.append(result)
                continue

            stop_url = f"{command_url}?method=stop"
            logger.info(f"Batch stopping stream {stream.id} via command URL: {stop_url}")

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(stop_url)
                    if response.status_code >= 300:
                        logger.warning(
                            f"Stop command returned non-success status {response.status_code} for stream {stream.id}"
                        )
            except Exception as e:
                logger.warning(f"Failed to send stop command for stream {stream.id}: {e}")

            logger.info(f"Ending stream {stream.id} (reason: batch_stop_via_api)")
            state.on_stream_ended(
                StreamEndedEvent(
                    container_id=stream.container_id,
                    stream_id=stream.id,
                    reason="batch_stop_via_api",
                )
            )

            result["success"] = True
            result["message"] = "Stream stopped successfully"

        except Exception as e:
            logger.error(f"Error stopping stream with command URL {command_url}: {e}")
            result["message"] = f"Error: {str(e)}"

        results.append(result)

    success_count = sum(1 for r in results if r["success"])

    return {
        "total": len(command_urls),
        "success_count": success_count,
        "failure_count": len(command_urls) - success_count,
        "results": results,
    }


@router.get("/stream-loop-detection/config")
def get_stream_loop_detection_config():
    """Get current stream loop detection configuration."""
    return {
        "enabled": cfg.STREAM_LOOP_DETECTION_ENABLED,
        "threshold_seconds": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S,
        "threshold_minutes": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S / 60,
        "threshold_hours": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S / 3600,
        "check_interval_seconds": cfg.STREAM_LOOP_CHECK_INTERVAL_S,
        "retention_minutes": cfg.STREAM_LOOP_RETENTION_MINUTES,
    }


@router.post("/stream-loop-detection/config", dependencies=[Depends(require_api_key)])
async def update_stream_loop_detection_config(
    enabled: bool,
    threshold_seconds: int,
    check_interval_seconds: Optional[int] = None,
    retention_minutes: Optional[int] = None,
):
    """Update stream loop detection configuration."""
    from ...data_plane.stream_loop_detector import stream_loop_detector
    from ...data_plane.looping_streams import looping_streams_tracker

    if threshold_seconds < 60:
        raise HTTPException(status_code=400, detail="Threshold must be at least 60 seconds")

    if check_interval_seconds is not None and check_interval_seconds < 5:
        raise HTTPException(status_code=400, detail="Check interval must be at least 5 seconds")

    if retention_minutes is not None and retention_minutes < 0:
        raise HTTPException(status_code=400, detail="Retention minutes must be 0 or greater")

    cfg.STREAM_LOOP_DETECTION_ENABLED = enabled
    cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = threshold_seconds

    if check_interval_seconds is not None:
        cfg.STREAM_LOOP_CHECK_INTERVAL_S = check_interval_seconds

    if retention_minutes is not None:
        cfg.STREAM_LOOP_RETENTION_MINUTES = retention_minutes
        looping_streams_tracker.set_retention_minutes(retention_minutes)

    if enabled:
        await stream_loop_detector.stop()
        await stream_loop_detector.start()
        logger.info(
            f"Stream loop detection restarted with threshold {threshold_seconds}s, check_interval {cfg.STREAM_LOOP_CHECK_INTERVAL_S}s"
        )
    else:
        await stream_loop_detector.stop()
        logger.info("Stream loop detection disabled")

    from ...persistence.settings_persistence import SettingsPersistence

    config_to_save = {
        "enabled": enabled,
        "threshold_seconds": threshold_seconds,
        "check_interval_seconds": cfg.STREAM_LOOP_CHECK_INTERVAL_S,
        "retention_minutes": cfg.STREAM_LOOP_RETENTION_MINUTES,
    }
    if SettingsPersistence.save_loop_detection_config(config_to_save):
        logger.info("Loop detection configuration persisted to JSON file")

    return {
        "message": "Stream loop detection configuration updated and persisted",
        "enabled": enabled,
        "threshold_seconds": threshold_seconds,
        "threshold_minutes": threshold_seconds / 60,
        "threshold_hours": threshold_seconds / 3600,
        "check_interval_seconds": cfg.STREAM_LOOP_CHECK_INTERVAL_S,
        "retention_minutes": cfg.STREAM_LOOP_RETENTION_MINUTES,
    }


@router.get("/looping-streams")
def get_looping_streams():
    """Get list of AceStream IDs that have been detected as looping."""
    from ...data_plane.looping_streams import looping_streams_tracker

    return {
        "stream_ids": list(looping_streams_tracker.get_looping_stream_ids()),
        "streams": looping_streams_tracker.get_looping_streams(),
        "retention_minutes": looping_streams_tracker.get_retention_minutes() or 0,
    }


@router.delete("/looping-streams/{stream_id}", dependencies=[Depends(require_api_key)])
def remove_looping_stream(stream_id: str):
    """Manually remove a stream ID from the looping streams list."""
    from ...data_plane.looping_streams import looping_streams_tracker

    if looping_streams_tracker.remove_looping_stream(stream_id):
        return {"message": f"Stream {stream_id} removed from looping list"}
    else:
        raise HTTPException(status_code=404, detail=f"Stream {stream_id} not found in looping list")


@router.post("/looping-streams/clear", dependencies=[Depends(require_api_key)])
def clear_all_looping_streams():
    """Clear all looping streams from the tracker."""
    from ...data_plane.looping_streams import looping_streams_tracker

    looping_streams_tracker.clear_all()
    return {"message": "All looping streams cleared"}


@router.get("/api/v1/streams/{stream_id}/details/stream")
async def stream_stream_details(
    request: Request,
    stream_id: str,
    since_seconds: int = Query(3600, ge=60, le=86400),
    interval_seconds: float = Query(2.0, ge=0.5, le=10.0),
    api_key: Optional[str] = Query(None),
):
    stream_id = sanitize_stream_id(stream_id)
    """SSE endpoint that streams detail payloads for a single stream row."""
    _validate_sse_api_key(request, api_key)

    from ...data_plane.client_tracker import client_tracking_service
    from ...proxy.config_helper import Config as ProxyConfig
    from ...main import _prune_client_tracker_if_due

    async def _event_generator():
        last_digest: Optional[str] = None
        cached_extended_stats: Optional[Dict[str, Any]] = None
        next_extended_refresh_monotonic = 0.0
        extended_refresh_task: Optional[asyncio.Task] = None

        while True:
            if await request.is_disconnected():
                break

            stream_state = state.get_stream(stream_id)
            if not stream_state:
                message = {
                    "type": "stream_details_error",
                    "payload": {
                        "stream_id": stream_id,
                        "status_code": 404,
                        "detail": "stream_not_found",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="stream_details_error")
                break

            stat_url = str(getattr(stream_state, "stat_url", "") or "").strip()
            supports_extended_stats = bool(stat_url)

            now_monotonic = time.monotonic()
            if (
                supports_extended_stats
                and now_monotonic >= next_extended_refresh_monotonic
                and (extended_refresh_task is None or extended_refresh_task.done())
            ):
                extended_refresh_task = asyncio.create_task(get_stream_extended_stats(stream_id))
                next_extended_refresh_monotonic = now_monotonic + max(10.0, interval_seconds * 2.0)
            elif not supports_extended_stats:
                if cached_extended_stats is None:
                    cached_extended_stats = {
                        "available": False,
                        "reason": "extended_stats_disabled_in_api_mode",
                    }
                if extended_refresh_task is not None and not extended_refresh_task.done():
                    extended_refresh_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await extended_refresh_task
                    extended_refresh_task = None

            if extended_refresh_task is not None and extended_refresh_task.done():
                try:
                    cached_extended_stats = extended_refresh_task.result()
                except HTTPException as exc:
                    if exc.status_code != 404:
                        logger.debug(
                            f"Extended stats refresh failed for stream {stream_id[:12]}: {exc.detail}"
                        )
                except Exception as exc:
                    logger.debug(f"Extended stats refresh failed for stream {stream_id[:12]}: {exc}")
                finally:
                    extended_refresh_task = None

            cutoff = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
            stream_stats = [snap for snap in state.get_stream_stats(stream_id) if snap.ts >= cutoff]

            clients: List[Dict[str, Any]] = []
            stream_key = str(getattr(stream_state, "key", "") or "")
            if stream_key:
                with suppress(Exception):
                    _prune_client_tracker_if_due(ttl_s=float(ProxyConfig.CLIENT_RECORD_TTL), min_interval_s=3.0)
                    tracker_payload = client_tracking_service.get_stream_clients_payload(stream_key)
                    clients = tracker_payload.get("clients", [])
                    if not clients and getattr(stream_state, "status", None) == "started":
                        logger.debug(
                            f"[Telemetry:SSE] No clients found in tracker for active stream {stream_id[:12]} using key {stream_key}"
                        )

            payload = jsonable_encoder(
                {
                    "stream_id": stream_id,
                    "status": getattr(stream_state, "status", None),
                    "stats": stream_stats,
                    "extended_stats": cached_extended_stats,
                    "clients": clients,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            digest = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
            if digest != last_digest:
                last_digest = digest
                message = {
                    "type": "stream_details_snapshot",
                    "payload": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(
                    message,
                    event_name="stream_details_snapshot",
                    event_id=str(int(time.time() * 1000)),
                )
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(interval_seconds)

        if extended_refresh_task is not None and not extended_refresh_task.done():
            extended_refresh_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await extended_refresh_task

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)
