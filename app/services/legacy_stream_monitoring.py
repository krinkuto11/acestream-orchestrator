from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..proxy.ace_api_client import AceLegacyApiClient
from .state import state
from ..core.config import cfg

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

    @staticmethod
    def _utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _serialize_session(self, monitor_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "monitor_id": monitor_id,
            "content_id": raw.get("content_id"),
            "status": raw.get("status"),
            "interval_s": raw.get("interval_s"),
            "run_seconds": raw.get("run_seconds"),
            "started_at": raw.get("started_at"),
            "last_collected_at": raw.get("last_collected_at"),
            "ended_at": raw.get("ended_at"),
            "sample_count": raw.get("sample_count", 0),
            "last_error": raw.get("last_error"),
            "engine": raw.get("engine") or {},
            "session": raw.get("session") or {},
            "latest_status": raw.get("latest_status") or {},
            "recent_status": list(raw.get("recent_status") or []),
        }

    def _pick_engine(self, requested_container_id: Optional[str]) -> Dict[str, Any]:
        engines = state.list_engines()
        if not engines:
            raise RuntimeError("No engines available")

        active_streams = state.list_streams(status="started")
        active_by_engine: Dict[str, int] = {}
        for stream in active_streams:
            active_by_engine[stream.container_id] = active_by_engine.get(stream.container_id, 0) + 1

        monitor_by_engine: Dict[str, int] = {}
        for session in self._sessions.values():
            if session.get("status") in {"starting", "running", "reconnecting"}:
                engine = session.get("engine") or {}
                container_id = engine.get("container_id")
                if container_id:
                    monitor_by_engine[container_id] = monitor_by_engine.get(container_id, 0) + 1

        if requested_container_id:
            selected = next((e for e in engines if e.container_id == requested_container_id), None)
            if not selected:
                raise RuntimeError(f"Engine '{requested_container_id}' not found")
            return {
                "container_id": selected.container_id,
                "host": selected.host,
                "port": selected.port,
                "api_port": selected.api_port or 62062,
                "forwarded": bool(selected.forwarded),
            }

        # Keep balancing consistent with stream startup, but include monitor load too.
        max_streams = cfg.MAX_STREAMS_PER_ENGINE
        available = [
            e
            for e in engines
            if (active_by_engine.get(e.container_id, 0) + monitor_by_engine.get(e.container_id, 0)) < max_streams
        ]
        if not available:
            raise RuntimeError(
                f"All engines at maximum capacity ({max_streams} streams/monitors per engine)"
            )

        selected = sorted(
            available,
            key=lambda e: (
                active_by_engine.get(e.container_id, 0) + monitor_by_engine.get(e.container_id, 0),
                not e.forwarded,
            ),
        )[0]

        return {
            "container_id": selected.container_id,
            "host": selected.host,
            "port": selected.port,
            "api_port": selected.api_port or 62062,
            "forwarded": bool(selected.forwarded),
        }

    async def start_monitor(
        self,
        content_id: str,
        interval_s: float = 1.0,
        run_seconds: int = 0,
        per_sample_timeout_s: float = 1.0,
        engine_container_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_content_id = (content_id or "").strip().lower()
        if not normalized_content_id:
            raise ValueError("content_id is required")

        interval_value = max(0.5, float(interval_s))
        timeout_value = max(0.2, float(per_sample_timeout_s))
        runtime_limit = max(0, int(run_seconds))

        async with self._lock:
            engine = self._pick_engine(engine_container_id)
            monitor_id = str(uuid.uuid4())
            stop_event = asyncio.Event()
            self._stop_events[monitor_id] = stop_event
            self._sessions[monitor_id] = {
                "content_id": normalized_content_id,
                "status": "starting",
                "interval_s": interval_value,
                "run_seconds": runtime_limit,
                "started_at": self._utc_iso(),
                "last_collected_at": None,
                "ended_at": None,
                "sample_count": 0,
                "last_error": None,
                "engine": engine,
                "session": {},
                "latest_status": {},
                "recent_status": [],
            }
            task = asyncio.create_task(
                self._run_monitor(
                    monitor_id=monitor_id,
                    content_id=normalized_content_id,
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

    async def _append_sample(self, monitor_id: str, sample: Dict[str, Any]):
        async with self._lock:
            session = self._sessions.get(monitor_id)
            if not session:
                return
            sample_history = session.setdefault("recent_status", [])
            sample_history.append(sample)
            if len(sample_history) > 120:
                del sample_history[:-120]

            session["latest_status"] = sample
            session["last_collected_at"] = sample.get("ts")
            session["sample_count"] = int(session.get("sample_count", 0)) + 1

    async def _run_monitor(
        self,
        monitor_id: str,
        content_id: str,
        interval_s: float,
        run_seconds: int,
        per_sample_timeout_s: float,
    ):
        client: Optional[AceLegacyApiClient] = None
        started_monotonic = time.monotonic()
        stop_event = self._stop_events[monitor_id]
        stream_started = False

        async def _shutdown_client():
            nonlocal client, stream_started
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

        try:
            await self._update_session(monitor_id, status="starting", last_error=None)

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
                            await self._update_session(
                                monitor_id,
                                status="reconnecting",
                                last_error=f"LOADASYNC status={status_code}: {message}",
                            )
                            await _shutdown_client()
                            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                            continue

                        resolved_infohash = loadresp.get("infohash") or content_id
                        start_info = await asyncio.to_thread(client.start_stream, resolved_infohash, "infohash")
                        stream_started = True

                        await self._update_session(
                            monitor_id,
                            status="running",
                            last_error=None,
                            session={
                                "playback_session_id": start_info.get("playback_session_id"),
                                "playback_url": start_info.get("url"),
                                "resolved_infohash": resolved_infohash,
                            },
                        )

                    probe = await asyncio.to_thread(
                        client.collect_status_samples,
                        1,
                        0.0,
                        per_sample_timeout_s,
                    )
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
                    await self._append_sample(monitor_id, sample)

                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                    except asyncio.TimeoutError:
                        pass
                except asyncio.TimeoutError:
                    # wait_for(stop_event) timeout path above
                    pass
                except Exception as e:
                    await self._update_session(
                        monitor_id,
                        status="reconnecting",
                        last_error=str(e),
                    )
                    logger.warning(
                        "Legacy monitor %s reconnecting after error: %s",
                        monitor_id,
                        e,
                    )
                    await _shutdown_client()
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=min(2.0, interval_s))
                    except asyncio.TimeoutError:
                        pass

            await self._update_session(monitor_id, status="stopped", ended_at=self._utc_iso())
        finally:
            await _shutdown_client()
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
            except Exception:
                pass

        async with self._lock:
            self._tasks.pop(monitor_id, None)
            self._stop_events.pop(monitor_id, None)
            session = self._sessions.get(monitor_id)
            if session and not session.get("ended_at"):
                session["ended_at"] = self._utc_iso()
                session["status"] = "stopped"
        return True

    async def stop_all(self) -> int:
        async with self._lock:
            monitor_ids = list(self._stop_events.keys())
        stopped = 0
        for monitor_id in monitor_ids:
            if await self.stop_monitor(monitor_id):
                stopped += 1
        return stopped

    async def get_monitor(self, monitor_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            raw = self._sessions.get(monitor_id)
            if not raw:
                return None
            return self._serialize_session(monitor_id, raw)

    async def list_monitors(self) -> List[Dict[str, Any]]:
        async with self._lock:
            return [
                self._serialize_session(monitor_id, raw)
                for monitor_id, raw in self._sessions.items()
            ]


legacy_stream_monitoring_service = LegacyStreamMonitoringService()
