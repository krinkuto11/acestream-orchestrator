from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import os
from urllib.parse import urlparse

from ..proxy.ace_api_client import AceLegacyApiClient
from ..proxy.http_streamer import HTTPStreamReader
from ..proxy.constants import VLC_USER_AGENT
from .state import state
from ..core.config import cfg
from .engine_selection import select_best_engine
from .hls_segmenter import hls_segmenter_service

logger = logging.getLogger(__name__)


class LegacyStreamMonitoringService:
    """Background STATUS-only monitoring for legacy API streams.

    Flow per monitor session:
    1) Connect/authenticate against AceStream legacy API.
    2) Resolve content with LOADASYNC.
    3) START stream once, but never consume playback URL.
    4) Poll STATUS every interval and keep in-memory snapshots.
    5) STOP/SHUTDOWN on stop or service shutdown.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._tasks: Dict[str, asyncio.Task] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._stop_events: Dict[str, asyncio.Event] = {}
        self._stuck_no_movement_seconds = 20.0

    @staticmethod
    def _utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            text = str(value).strip()
            if text == "":
                return None
            return int(float(text))
        except Exception:
            return None

    def _build_livepos_movement(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        samples = list(raw.get("recent_status") or [])
        if not samples:
            return {
                "is_moving": False,
                "direction": "unknown",
                "pos_delta": None,
                "last_ts_delta": None,
                "downloaded_delta": None,
                "sample_points": 0,
                "movement_events": 0,
            }

        pos_points: List[int] = []
        last_ts_points: List[int] = []
        downloaded_points: List[int] = []

        for sample in samples:
            livepos = sample.get("livepos") or {}
            pos_val = self._to_int(livepos.get("pos"))
            ts_val = self._to_int(livepos.get("last_ts") or livepos.get("live_last"))
            dl_val = self._to_int(sample.get("downloaded") or sample.get("http_downloaded"))

            if pos_val is not None:
                pos_points.append(pos_val)
            if ts_val is not None:
                last_ts_points.append(ts_val)
            if dl_val is not None:
                downloaded_points.append(dl_val)

        pos_delta = None
        if len(pos_points) >= 2:
            pos_delta = pos_points[-1] - pos_points[0]

        last_ts_delta = None
        if len(last_ts_points) >= 2:
            last_ts_delta = last_ts_points[-1] - last_ts_points[0]

        downloaded_delta = None
        if len(downloaded_points) >= 2:
            downloaded_delta = downloaded_points[-1] - downloaded_points[0]

        movement_events = 0
        movement_events += sum(1 for prev, curr in zip(pos_points, pos_points[1:]) if curr != prev)
        movement_events += sum(1 for prev, curr in zip(last_ts_points, last_ts_points[1:]) if curr != prev)

        is_moving = bool(
            (pos_delta is not None and pos_delta > 0)
            or (last_ts_delta is not None and last_ts_delta > 0)
        )

        direction = "unknown"
        if pos_delta is not None:
            if pos_delta > 0:
                direction = "forward"
            elif pos_delta < 0:
                direction = "backward"
            else:
                direction = "stable"
        elif last_ts_delta is not None:
            if last_ts_delta > 0:
                direction = "forward"
            elif last_ts_delta < 0:
                direction = "backward"
            else:
                direction = "stable"

        latest_livepos = (samples[-1].get("livepos") or {}) if samples else {}

        return {
            "is_moving": is_moving,
            "direction": direction,
            "current_pos": self._to_int(latest_livepos.get("pos")),
            "current_last_ts": self._to_int(latest_livepos.get("last_ts") or latest_livepos.get("live_last")),
            "pos_delta": pos_delta,
            "last_ts_delta": last_ts_delta,
            "downloaded_delta": downloaded_delta,
            "sample_points": len(samples),
            "movement_events": movement_events,
        }

    def _serialize_session(
        self,
        monitor_id: str,
        raw: Dict[str, Any],
        include_recent_status: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "monitor_id": monitor_id,
            "content_id": raw.get("content_id"),
            "stream_name": raw.get("stream_name"),
            "live_delay": raw.get("live_delay", 0),
            "status": raw.get("status"),
            "interval_s": raw.get("interval_s"),
            "run_seconds": raw.get("run_seconds"),
            "started_at": raw.get("started_at"),
            "last_collected_at": raw.get("last_collected_at"),
            "ended_at": raw.get("ended_at"),
            "sample_count": raw.get("sample_count", 0),
            "last_error": raw.get("last_error"),
            "dead_reason": raw.get("dead_reason"),
            "reconnect_attempts": raw.get("reconnect_attempts", 0),
            "engine": raw.get("engine") or {},
            "session": raw.get("session") or {},
            "latest_status": raw.get("latest_status") or {},
            "livepos_movement": self._build_livepos_movement(raw),
        }

        if include_recent_status:
            payload["recent_status"] = list(raw.get("recent_status") or [])

        return payload

    def _publish_session_state(self, monitor_id: str, raw: Dict[str, Any]) -> None:
        try:
            state.upsert_monitor_session(monitor_id, self._serialize_session(monitor_id, raw))
        except Exception:
            logger.debug("Failed to publish monitor session state for %s", monitor_id, exc_info=True)

    @staticmethod
    def _is_timeout_or_connect_error(message: str) -> bool:
        text = (message or "").strip().lower()
        if not text:
            return False
        patterns = [
            "timeout",
            "timed out",
            "connection closed",
            "socket",
            "not connected",
            "connect",
        ]
        return any(p in text for p in patterns)

    def _is_session_stuck(self, raw: Dict[str, Any]) -> bool:
        samples = list(raw.get("recent_status") or [])
        if len(samples) < 2:
            return False

        try:
            interval_s = float(raw.get("interval_s") or 1.0)
        except Exception:
            interval_s = 1.0
        interval_s = max(0.5, interval_s)

        # Require approximately 20 seconds worth of non-movement before marking dead.
        required_samples = max(2, int(self._stuck_no_movement_seconds / interval_s) + 1)
        if len(samples) < required_samples:
            return False

        window = samples[-required_samples:]
        pos_values: List[int] = []
        last_ts_values: List[int] = []
        downloaded_values: List[int] = []
        livepos_missing_samples = 0

        for sample in window:
            livepos = sample.get("livepos") or {}
            pos_val = self._to_int(livepos.get("pos"))
            ts_val = self._to_int(livepos.get("last_ts") or livepos.get("live_last"))
            dl_val = self._to_int(sample.get("downloaded") or sample.get("http_downloaded"))

            if pos_val is None and ts_val is None:
                livepos_missing_samples += 1

            if pos_val is not None:
                pos_values.append(pos_val)
            if ts_val is not None:
                last_ts_values.append(ts_val)
            if dl_val is not None:
                downloaded_values.append(dl_val)

        # If livepos is consistently absent for the full window, classify as stuck.
        if livepos_missing_samples == len(window):
            return True

        # If neither pos nor last_ts has enough points and livepos is intermittently missing,
        # we still cannot classify as stuck.
        if len(pos_values) < 2 and len(last_ts_values) < 2:
            return False

        pos_static = len(pos_values) >= 2 and all(v == pos_values[0] for v in pos_values)
        ts_static = len(last_ts_values) >= 2 and all(v == last_ts_values[0] for v in last_ts_values)

        downloaded_growth = 0
        if len(downloaded_values) >= 2:
            downloaded_growth = downloaded_values[-1] - downloaded_values[0]

        # Consider stream stuck when timeline doesn't move and there is no payload growth.
        return bool((pos_static or ts_static) and downloaded_growth <= 0)

    def _pick_engine(self, requested_container_id: Optional[str]) -> Dict[str, Any]:
        monitor_by_engine: Dict[str, int] = {}
        for session in self._sessions.values():
            if session.get("status") in {"starting", "running", "stuck", "reconnecting"}:
                engine = session.get("engine") or {}
                container_id = engine.get("container_id")
                if container_id:
                    monitor_by_engine[container_id] = monitor_by_engine.get(container_id, 0) + 1

        try:
            selected, _ = select_best_engine(
                requested_container_id=requested_container_id,
                additional_load_by_engine=monitor_by_engine,
                reserve_pending=False,
                not_found_error=f"Engine '{requested_container_id}' not found" if requested_container_id else "engine_not_found",
            )
        except Exception as e:
            raise RuntimeError(str(getattr(e, "detail", str(e))))

        return {
            "container_id": selected.container_id,
            "host": selected.host,
            "port": selected.port,
            "api_port": selected.api_port or 62062,
            "forwarded": bool(selected.forwarded),
        }

    def _pick_engine_excluding(self, excluded_container_id: Optional[str]) -> Optional[Dict[str, Any]]:
        engines = [
            e
            for e in state.list_engines()
            if e.container_id != excluded_container_id and not state.is_engine_draining(e.container_id)
        ]
        if not engines:
            return None

        active_streams = state.list_streams(status="started")
        engine_loads: Dict[str, int] = {}
        for stream in active_streams:
            cid = stream.container_id
            engine_loads[cid] = engine_loads.get(cid, 0) + 1

        monitor_loads = state.get_active_monitor_load_by_engine()
        for cid, monitor_count in monitor_loads.items():
            engine_loads[cid] = engine_loads.get(cid, 0) + monitor_count

        max_streams = cfg.MAX_STREAMS_PER_ENGINE
        available = [e for e in engines if engine_loads.get(e.container_id, 0) < max_streams]
        if not available:
            return None

        selected = sorted(
            available,
            key=lambda e: (engine_loads.get(e.container_id, 0), not e.forwarded),
        )[0]

        return {
            "container_id": selected.container_id,
            "host": selected.host,
            "port": selected.port,
            "api_port": selected.api_port or 62062,
            "forwarded": bool(selected.forwarded),
        }

    async def _failover_retry_dead_monitor(self, monitor_id: str, reason: str, error_text: str) -> bool:
        async with self._lock:
            session = self._sessions.get(monitor_id)
            if not session:
                return False

            attempts = int(session.get("reconnect_attempts") or 0)

            current_engine = session.get("engine") or {}
            failover_engine = self._pick_engine_excluding(current_engine.get("container_id"))
            if not failover_engine:
                return False

            session["reconnect_attempts"] = attempts + 1
            session["status"] = "reconnecting"
            session["dead_reason"] = None
            session["last_error"] = f"{reason}: {error_text}"
            session["engine"] = failover_engine
            session["session"] = {}
            self._publish_session_state(monitor_id, session)
            return True

    async def start_monitor(
        self,
        content_id: str,
        stream_name: Optional[str] = None,
        live_delay: int = 0,
        interval_s: float = 1.0,
        run_seconds: int = 0,
        per_sample_timeout_s: float = 1.0,
        engine_container_id: Optional[str] = None,
        monitor_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_content_id = (content_id or "").strip().lower()
        if not normalized_content_id:
            raise ValueError("content_id is required")

        interval_value = max(0.5, float(interval_s))
        timeout_value = max(0.2, float(per_sample_timeout_s))
        runtime_limit = max(0, int(run_seconds))
        try:
            live_delay_value = max(0, int(float(live_delay)))
        except (TypeError, ValueError):
            live_delay_value = 0
        normalized_stream_name = (stream_name or "").strip() or None
        requested_monitor_id = (monitor_id or "").strip() or None

        async with self._lock:
            existing_for_content: Optional[str] = None
            for existing_monitor_id, raw in self._sessions.items():
                if (raw.get("content_id") or "").strip().lower() != normalized_content_id:
                    continue
                if raw.get("status") not in {"starting", "running", "stuck", "reconnecting"}:
                    continue
                existing_for_content = existing_monitor_id
                break

            if existing_for_content:
                return self._serialize_session(existing_for_content, self._sessions[existing_for_content])

            if requested_monitor_id and requested_monitor_id in self._sessions:
                return self._serialize_session(requested_monitor_id, self._sessions[requested_monitor_id])

            engine = self._pick_engine(engine_container_id)
            monitor_id = requested_monitor_id or str(uuid.uuid4())
            stop_event = asyncio.Event()
            self._stop_events[monitor_id] = stop_event
            self._sessions[monitor_id] = {
                "content_id": normalized_content_id,
                "stream_name": normalized_stream_name,
                "live_delay": live_delay_value,
                "status": "starting",
                "interval_s": interval_value,
                "run_seconds": runtime_limit,
                "started_at": self._utc_iso(),
                "last_collected_at": None,
                "ended_at": None,
                "sample_count": 0,
                "last_error": None,
                "dead_reason": None,
                "reconnect_attempts": 0,
                "engine": engine,
                "session": {},
                "latest_status": {},
                "recent_status": [],
            }
            self._publish_session_state(monitor_id, self._sessions[monitor_id])
            task = asyncio.create_task(
                self._run_monitor(
                    monitor_id=monitor_id,
                    content_id=normalized_content_id,
                    live_delay=live_delay_value,
                    interval_s=interval_value,
                    run_seconds=runtime_limit,
                    per_sample_timeout_s=timeout_value,
                )
            )
            self._tasks[monitor_id] = task

            return self._serialize_session(monitor_id, self._sessions[monitor_id])

    async def _update_session(self, monitor_id: str, **kwargs):
        async with self._lock:
            session = self._sessions.get(monitor_id)
            if not session:
                return
            session.update(kwargs)
            self._publish_session_state(monitor_id, session)

        if session.get("status") in {"dead", "stopped", "deleted"}:
            hls_segmenter_service.stop_segmenter_nowait(monitor_id)

    async def _append_sample(self, monitor_id: str, sample: Dict[str, Any]):
        async with self._lock:
            session = self._sessions.get(monitor_id)
            if not session:
                return False
            sample_history = session.setdefault("recent_status", [])
            sample_history.append(sample)
            if len(sample_history) > 120:
                del sample_history[:-120]

            session["latest_status"] = sample
            session["last_collected_at"] = sample.get("ts")
            session["sample_count"] = int(session.get("sample_count", 0)) + 1
            self._publish_session_state(monitor_id, session)
            return self._is_session_stuck(session)

    async def _run_monitor(
        self,
        monitor_id: str,
        content_id: str,
        live_delay: int,
        interval_s: float,
        run_seconds: int,
        per_sample_timeout_s: float,
    ):
        client: Optional[AceLegacyApiClient] = None
        started_monotonic = time.monotonic()
        stop_event = self._stop_events[monitor_id]
        stream_started = False

        dummy_reader_task: Optional[asyncio.Task] = None
        dummy_server: Optional[asyncio.base_events.Server] = None
        proxy_clients: set[asyncio.StreamWriter] = set()
        relay_url: Optional[str] = None

        async def _shutdown_client():
            nonlocal client, stream_started, dummy_reader_task

            if dummy_reader_task:
                dummy_reader_task.cancel()
                try:
                    await dummy_reader_task
                except asyncio.CancelledError:
                    pass
                dummy_reader_task = None

            if not client:
                return
            try:
                if stream_started:
                    await asyncio.to_thread(client.stop_stream)
                    stream_started = False
            except Exception:
                pass
            try:
                await asyncio.to_thread(client.shutdown)
            except Exception:
                pass
            client = None

        async def handle_proxy_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=5.0)
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: video/mp2t\r\n"
                    b"Connection: keep-alive\r\n\r\n"
                )
                await writer.drain()

                proxy_clients.add(writer)

                while not stop_event.is_set():
                    if writer.is_closing() or reader.at_eof():
                        break
                    await asyncio.sleep(1)
            except Exception:
                pass
            finally:
                proxy_clients.discard(writer)
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        async def _engine_consumer(url: str):
            try:
                while not stop_event.is_set():
                    http_reader = None
                    fd = None
                    try:
                        http_reader = HTTPStreamReader(
                            url=url,
                            user_agent=VLC_USER_AGENT,
                            chunk_size=65536,
                        )
                        pipe_fd = await asyncio.to_thread(http_reader.start)
                        fd = os.fdopen(pipe_fd, 'rb', buffering=0)

                        while not stop_event.is_set():
                            chunk = await asyncio.to_thread(fd.read, 65536)
                            if not chunk:
                                break

                            dead_clients: List[asyncio.StreamWriter] = []
                            for client_writer in list(proxy_clients):
                                try:
                                    client_writer.write(chunk)
                                    await asyncio.wait_for(client_writer.drain(), timeout=2.0)
                                except Exception:
                                    dead_clients.append(client_writer)

                            for dead in dead_clients:
                                proxy_clients.discard(dead)
                                try:
                                    dead.close()
                                except Exception:
                                    pass
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug(f"Relay engine consumer error: {e}")
                        if not stop_event.is_set():
                            await asyncio.sleep(2.0)
                    finally:
                        if fd:
                            try:
                                fd.close()
                            except Exception:
                                pass
                        if http_reader:
                            try:
                                http_reader.stop()
                            except Exception:
                                pass
            except asyncio.CancelledError:
                pass

        try:
            await self._update_session(monitor_id, status="starting", last_error=None)

            relay_host = "127.0.0.2"
            try:
                dummy_server = await asyncio.start_server(handle_proxy_client, relay_host, 0)
            except OSError:
                relay_host = "127.0.0.1"
                logger.warning(
                    "Monitor %s could not bind relay on 127.0.0.2; falling back to %s",
                    monitor_id,
                    relay_host,
                )
                dummy_server = await asyncio.start_server(handle_proxy_client, relay_host, 0)
            dummy_port = dummy_server.sockets[0].getsockname()[1]
            relay_url = f"http://{relay_host}:{dummy_port}/"

            while not stop_event.is_set():
                if run_seconds > 0 and (time.monotonic() - started_monotonic) >= run_seconds:
                    break

                try:
                    if client is None:
                        async with self._lock:
                            session = self._sessions.get(monitor_id) or {}
                            engine = session.get("engine") or {}

                        host = str(engine.get("host") or "").strip()
                        if not host:
                            raise RuntimeError("Selected engine has no host")

                        try:
                            api_port = int(engine.get("api_port") or 62062)
                        except Exception:
                            api_port = 62062

                        client = AceLegacyApiClient(
                            host=host,
                            port=api_port,
                            connect_timeout=8.0,
                            response_timeout=8.0,
                        )
                        await asyncio.to_thread(client.connect)
                        await asyncio.to_thread(client.authenticate)

                        loadresp, _ = await asyncio.to_thread(client.resolve_content, content_id, "0")
                        status_code = loadresp.get("status")
                        if status_code not in (1, 2):
                            message = loadresp.get("message") or "content unavailable"
                            dead_reason = f"LOADASYNC status={status_code}: {message}"
                            did_failover = await self._failover_retry_dead_monitor(monitor_id, "loadasync_error", dead_reason)
                            await _shutdown_client()
                            if did_failover:
                                logger.warning(
                                    "Legacy monitor %s failed LOADASYNC on current engine; retrying on different engine",
                                    monitor_id,
                                )
                                continue
                            await self._update_session(
                                monitor_id,
                                status="dead",
                                dead_reason=dead_reason,
                                last_error=dead_reason,
                                ended_at=self._utc_iso(),
                            )
                            break

                        resolved_infohash = loadresp.get("infohash") or content_id
                        start_info = await asyncio.to_thread(
                            client.start_stream,
                            resolved_infohash,
                            "infohash",
                            "output_format=http",
                            "0",
                            live_delay,
                        )
                        stream_started = True

                        playback_session_id = start_info.get("playback_session_id")
                        if not playback_session_id:
                            playback_session_id = f"legacy-monitor-{monitor_id[:8]}-{int(time.time())}"

                        real_playback_url = start_info.get("url")
                        if not real_playback_url:
                            raise RuntimeError("start_stream returned empty playback URL")

                        await self._update_session(
                            monitor_id,
                            status="running",
                            last_error=None,
                            session={
                                "playback_session_id": playback_session_id,
                                "playback_url": relay_url,
                                "resolved_infohash": resolved_infohash,
                            },
                        )

                        dummy_reader_task = asyncio.create_task(_engine_consumer(real_playback_url))

                    probe = await asyncio.to_thread(
                        client.collect_status_samples,
                        1,
                        0.0,
                        per_sample_timeout_s,
                    )
                    download_stopped_event = await asyncio.to_thread(client.consume_download_stopped_event)

                    if download_stopped_event:
                        parsed_reason = (download_stopped_event.get("reason") or "").strip() or "download_stopped"
                        did_failover = await self._failover_retry_dead_monitor(
                            monitor_id,
                            "download_stopped",
                            parsed_reason,
                        )
                        await _shutdown_client()
                        if did_failover:
                            logger.warning(
                                "Legacy monitor %s got EVENT download_stopped (%s); retrying on different engine",
                                monitor_id,
                                parsed_reason,
                            )
                            continue

                        await self._update_session(
                            monitor_id,
                            status="dead",
                            dead_reason="download_stopped",
                            last_error=parsed_reason,
                            ended_at=self._utc_iso(),
                        )
                        logger.warning(
                            "Legacy monitor %s marked dead after EVENT download_stopped: %s",
                            monitor_id,
                            parsed_reason,
                        )
                        break

                    sample = {
                        "ts": self._utc_iso(),
                        "status_text": probe.get("status_text") or probe.get("status"),
                        "status": probe.get("status"),
                        "progress": probe.get("progress"),
                        "total_progress": probe.get("total_progress"),
                        "immediate_progress": probe.get("immediate_progress"),
                        "speed_down": probe.get("speed_down"),
                        "http_speed_down": probe.get("http_speed_down"),
                        "speed_up": probe.get("speed_up"),
                        "peers": probe.get("peers"),
                        "http_peers": probe.get("http_peers"),
                        "downloaded": probe.get("downloaded"),
                        "http_downloaded": probe.get("http_downloaded"),
                        "uploaded": probe.get("uploaded"),
                        "livepos": probe.get("livepos"),
                    }
                    session_stuck = await self._append_sample(monitor_id, sample)
                    async with self._lock:
                        previous_status = (self._sessions.get(monitor_id) or {}).get("status")

                    if session_stuck:
                        await self._update_session(
                            monitor_id,
                            status="stuck",
                            dead_reason=None,
                            last_error="livepos did not move and payload did not grow",
                        )
                        if previous_status != "stuck":
                            logger.warning(
                                "Legacy monitor %s marked stuck: livepos did not move",
                                monitor_id,
                            )
                    else:
                        await self._update_session(
                            monitor_id,
                            status="running",
                            dead_reason=None,
                            last_error=None,
                        )
                        if previous_status == "stuck":
                            logger.info(
                                "Legacy monitor %s recovered from stuck state",
                                monitor_id,
                            )

                    async with self._lock:
                        current_session = self._sessions.get(monitor_id) or {}
                        current_engine = current_session.get("engine") or {}
                    engine_container_id = str(current_engine.get("container_id") or "")
                    if engine_container_id and state.is_engine_draining(engine_container_id):
                        logger.info(
                            "Monitor %s detected draining engine %s. Preemptively failing over.",
                            monitor_id,
                            engine_container_id,
                        )
                        did_failover = await self._failover_retry_dead_monitor(
                            monitor_id,
                            "graceful_drain",
                            "Engine is draining",
                        )
                        if did_failover:
                            await _shutdown_client()
                            continue
                        logger.warning(
                            "Monitor %s could not fail over from draining engine %s (no eligible target)",
                            monitor_id,
                            engine_container_id,
                        )

                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                    except asyncio.TimeoutError:
                        pass
                except asyncio.TimeoutError:
                    did_failover = await self._failover_retry_dead_monitor(
                        monitor_id,
                        "timeout_or_connect_error",
                        "api timeout",
                    )
                    await _shutdown_client()
                    if did_failover:
                        logger.warning(
                            "Legacy monitor %s timed out; retrying on different engine",
                            monitor_id,
                        )
                        continue
                    await self._update_session(
                        monitor_id,
                        status="dead",
                        dead_reason="timeout_or_connect_error",
                        last_error="api timeout",
                        ended_at=self._utc_iso(),
                    )
                    logger.warning(
                        "Legacy monitor %s marked dead after API timeout",
                        monitor_id,
                    )
                    break
                except Exception as e:
                    error_text = str(e)
                    dead_reason = "timeout_or_connect_error" if self._is_timeout_or_connect_error(error_text) else "runtime_error"
                    did_failover = await self._failover_retry_dead_monitor(monitor_id, dead_reason, error_text)
                    await _shutdown_client()
                    if did_failover:
                        logger.warning(
                            "Legacy monitor %s failed (%s); retrying on different engine",
                            monitor_id,
                            dead_reason,
                        )
                        continue
                    await self._update_session(
                        monitor_id,
                        status="dead",
                        dead_reason=dead_reason,
                        last_error=error_text,
                        ended_at=self._utc_iso(),
                    )
                    logger.warning(
                        "Legacy monitor %s marked dead after error: %s",
                        monitor_id,
                        e,
                    )
                    break

            async with self._lock:
                final_state = self._sessions.get(monitor_id, {}).get("status")
            if final_state not in {"dead", "deleted"}:
                await self._update_session(monitor_id, status="stopped", ended_at=self._utc_iso())
        finally:
            await _shutdown_client()

            if dummy_server:
                dummy_server.close()
                try:
                    await dummy_server.wait_closed()
                except Exception:
                    pass

            for writer in list(proxy_clients):
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            proxy_clients.clear()

            await self._update_session(monitor_id, ended_at=self._utc_iso())

    async def stop_monitor(self, monitor_id: str) -> bool:
        async with self._lock:
            stop_event = self._stop_events.get(monitor_id)
            task = self._tasks.get(monitor_id)
            if not stop_event:
                return False
            stop_event.set()

        if task:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
            except Exception:
                pass

        async with self._lock:
            self._tasks.pop(monitor_id, None)
            self._stop_events.pop(monitor_id, None)
            session = self._sessions.get(monitor_id)
            if session and not session.get("ended_at"):
                session["ended_at"] = self._utc_iso()
                session["status"] = "stopped"
        hls_segmenter_service.stop_segmenter_nowait(monitor_id)
        return True

    async def stop_all(self) -> int:
        async with self._lock:
            monitor_ids = list(self._stop_events.keys())
        stopped = 0
        for monitor_id in monitor_ids:
            if await self.stop_monitor(monitor_id):
                stopped += 1
        return stopped

    async def delete_monitor(self, monitor_id: str) -> bool:
        monitor_exists = False
        async with self._lock:
            monitor_exists = monitor_id in self._sessions or monitor_id in self._stop_events
        if not monitor_exists:
            return False

        await self.stop_monitor(monitor_id)

        async with self._lock:
            self._sessions.pop(monitor_id, None)
            self._tasks.pop(monitor_id, None)
            self._stop_events.pop(monitor_id, None)
        state.remove_monitor_session(monitor_id)
        hls_segmenter_service.stop_segmenter_nowait(monitor_id)

        return True

    async def get_monitor(
        self,
        monitor_id: str,
        include_recent_status: bool = True,
    ) -> Optional[Dict[str, Any]]:
        async with self._lock:
            raw = self._sessions.get(monitor_id)
            if not raw:
                return None
            return self._serialize_session(
                monitor_id,
                raw,
                include_recent_status=include_recent_status,
            )

    async def list_monitors(self, include_recent_status: bool = True) -> List[Dict[str, Any]]:
        async with self._lock:
            return [
                self._serialize_session(
                    monitor_id,
                    raw,
                    include_recent_status=include_recent_status,
                )
                for monitor_id, raw in self._sessions.items()
            ]

    async def get_reusable_session_for_content(self, content_id: str) -> Optional[Dict[str, Any]]:
        normalized = (content_id or "").strip().lower()
        if not normalized:
            return None

        async with self._lock:
            candidates: List[Dict[str, Any]] = []
            for monitor_id, raw in self._sessions.items():
                if (raw.get("content_id") or "").strip().lower() != normalized:
                    continue
                if raw.get("status") not in {"running", "stuck"}:
                    continue

                session = raw.get("session") or {}
                engine = raw.get("engine") or {}
                playback_url = (session.get("playback_url") or "").strip()
                if not playback_url or not engine.get("container_id"):
                    continue

                candidates.append({
                    "monitor_id": monitor_id,
                    "status": raw.get("status"),
                    "last_collected_at": raw.get("last_collected_at") or "",
                    "engine": dict(engine),
                    "session": dict(session),
                    "latest_status": dict(raw.get("latest_status") or {}),
                })

        if not candidates:
            return None

        candidates.sort(key=lambda c: c.get("last_collected_at") or "", reverse=True)
        return candidates[0]


legacy_stream_monitoring_service = LegacyStreamMonitoringService()
