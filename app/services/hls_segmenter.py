from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SegmenterSession:
    monitor_id: str
    source_mpegts_url: str
    output_dir: Path
    manifest_path: Path
    process: asyncio.subprocess.Process
    started_at: float
    last_activity: float
    playback_session_id: str = ""
    stat_url: str = ""
    command_url: str = ""
    is_live: int = 1
    container_id: str = ""
    engine_host: str = ""
    engine_port: int = 0
    engine_api_port: int = 0
    stream_key_type: str = "content_id"
    file_indexes: str = "0"
    seekback: int = 0
    stream_id: str = ""
    control_client: Optional[object] = None
    legacy_probe_cache: Optional[Dict[str, Any]] = None
    legacy_probe_cache_ts: float = 0.0
    legacy_probe_lock: threading.Lock = field(default_factory=threading.Lock)


class HLSSegmenterService:
    """Manage external FFmpeg HLS segmenters for API-mode playback."""

    def __init__(self, base_dir: str = "/tmp/acestream_hls"):
        self._base_dir = Path(base_dir)
        self._lock = asyncio.Lock()
        self._sessions: Dict[str, SegmenterSession] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # API-mode HLS should detect disconnected clients faster than the generic TS Redis TTL.
        # Default to min(PROXY_CLIENT_TTL, 10s) unless explicitly overridden.
        proxy_client_ttl_s = max(10, self._to_int(os.getenv("PROXY_CLIENT_TTL", "60"), default=60))
        default_api_hls_client_ttl_s = min(proxy_client_ttl_s, 10)
        self._client_record_ttl_s = max(
            6,
            self._to_int(
                os.getenv("API_HLS_CLIENT_TTL", str(default_api_hls_client_ttl_s)),
                default=default_api_hls_client_ttl_s,
            ),
        )
        # Keep probe cadence aligned with collector interval as done in TS StreamManager.
        try:
            self._legacy_probe_cache_ttl_s = max(0.5, float(os.getenv("COLLECT_INTERVAL_S", "1")))
        except Exception:
            self._legacy_probe_cache_ttl_s = 1.0

        # API-mode HLS segmenter tuning. Lower defaults improve startup latency.
        self._hls_segment_time_s = self._to_float(os.getenv("API_HLS_SEGMENT_TIME_S", "3.0"), default=3.0)
        self._hls_segment_time_s = min(10.0, max(1.0, self._hls_segment_time_s))
        self._hls_list_size = max(3, self._to_int(os.getenv("API_HLS_LIST_SIZE", "5"), default=5))
        self._hls_split_by_time = self._to_bool(os.getenv("API_HLS_SPLIT_BY_TIME", "1"), default=True)

        logger.debug(
            "API-HLS segmenter config: segment_time=%.2fs list_size=%s split_by_time=%s client_ttl=%ss",
            self._hls_segment_time_s,
            self._hls_list_size,
            self._hls_split_by_time,
            self._client_record_ttl_s,
        )

    @staticmethod
    def _sanitize_monitor_id(monitor_id: str) -> str:
        raw = str(monitor_id or "").strip()
        if not raw:
            raise ValueError("monitor_id is required")
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", raw)

    def _get_output_dir(self, monitor_id: str) -> Path:
        return self._base_dir / monitor_id

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    def _apply_metadata(self, session: SegmenterSession, metadata: Optional[Dict[str, Any]]) -> None:
        if not metadata:
            return

        if metadata.get("playback_session_id") is not None:
            session.playback_session_id = str(metadata.get("playback_session_id") or "")
        if metadata.get("stat_url") is not None:
            session.stat_url = str(metadata.get("stat_url") or "")
        if metadata.get("command_url") is not None:
            session.command_url = str(metadata.get("command_url") or "")
        if metadata.get("is_live") is not None:
            session.is_live = self._to_int(metadata.get("is_live"), default=1)
        if metadata.get("container_id") is not None:
            session.container_id = str(metadata.get("container_id") or "")
        if metadata.get("engine_host") is not None:
            session.engine_host = str(metadata.get("engine_host") or "")
        if metadata.get("engine_port") is not None:
            session.engine_port = self._to_int(metadata.get("engine_port"), default=0)
        if metadata.get("engine_api_port") is not None:
            session.engine_api_port = self._to_int(metadata.get("engine_api_port"), default=0)
        if metadata.get("stream_key_type") is not None:
            session.stream_key_type = str(metadata.get("stream_key_type") or "content_id")
        if metadata.get("file_indexes") is not None:
            session.file_indexes = str(metadata.get("file_indexes") or "0")
        if metadata.get("seekback") is not None:
            session.seekback = self._to_int(metadata.get("seekback"), default=0)
        if metadata.get("stream_id") is not None:
            session.stream_id = str(metadata.get("stream_id") or "")
        if metadata.get("control_client") is not None and session.control_client is None:
            session.control_client = metadata.get("control_client")

    async def _shutdown_control_client(self, control_client: object) -> None:
        try:
            stop_method = getattr(control_client, "stop_stream", None)
            if callable(stop_method):
                await asyncio.to_thread(stop_method)
        except Exception:
            logger.debug("Failed to stop legacy API stream during segmenter cleanup", exc_info=True)

        try:
            shutdown_method = getattr(control_client, "shutdown", None)
            if callable(shutdown_method):
                await asyncio.to_thread(shutdown_method)
                return
            close_method = getattr(control_client, "close", None)
            if callable(close_method):
                await asyncio.to_thread(close_method)
        except Exception:
            logger.debug("Failed to close legacy API client during segmenter cleanup", exc_info=True)

    def has_session(self, monitor_id: str) -> bool:
        key = self._sanitize_monitor_id(monitor_id)
        return key in self._sessions

    def get_session_metadata(self, monitor_id: str) -> Optional[Dict[str, Any]]:
        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return None
        return {
            "playback_session_id": session.playback_session_id,
            "stat_url": session.stat_url,
            "command_url": session.command_url,
            "is_live": session.is_live,
            "container_id": session.container_id,
            "engine_host": session.engine_host,
            "engine_port": session.engine_port,
            "engine_api_port": session.engine_api_port,
            "stream_key_type": session.stream_key_type,
            "file_indexes": session.file_indexes,
            "seekback": session.seekback,
            "stream_id": session.stream_id,
        }

    def set_session_metadata(self, monitor_id: str, metadata: Dict[str, Any]) -> bool:
        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return False
        self._apply_metadata(session, metadata)
        session.last_activity = time.time()
        return True

    def record_client_activity(
        self,
        monitor_id: str,
        client_id: str,
        client_ip: str,
        user_agent: str,
        request_kind: str = "",
        bytes_sent: Optional[float] = None,
        chunks_sent: Optional[int] = None,
        sequence: Optional[int] = None,
        buffer_seconds_behind: Optional[float] = None,
        now: Optional[float] = None,
    ) -> None:
        from .client_tracker import client_tracking_service

        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return

        ts = now if now is not None else time.time()
        normalized_client_id = str(client_id or client_ip or "unknown")
        normalized_ip = str(client_ip or "unknown")
        normalized_ua = str(user_agent or "unknown")
        try:
            bytes_delta = float(bytes_sent) if bytes_sent is not None else 0.0
        except (TypeError, ValueError):
            bytes_delta = 0.0
        if bytes_delta < 0:
            bytes_delta = 0.0

        try:
            chunks_delta = int(chunks_sent) if chunks_sent is not None else 0
        except (TypeError, ValueError):
            chunks_delta = 0
        if chunks_delta < 0:
            chunks_delta = 0

        client_tracking_service.record_activity(
            client_id=normalized_client_id,
            stream_id=key,
            bytes_delta=bytes_delta,
            protocol="HLS",
            ip_address=normalized_ip,
            user_agent=normalized_ua,
            request_kind=request_kind,
            chunks_delta=chunks_delta,
            sequence=sequence,
            buffer_seconds_behind=buffer_seconds_behind,
            now=ts,
            idle_timeout_s=self._client_record_ttl_s,
            worker_id="api_hls_segmenter",
        )
        session.last_activity = ts

    @staticmethod
    def _manifest_segment_sequences(manifest_content: str) -> List[int]:
        sequences: List[int] = []
        for raw_line in str(manifest_content or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.search(r"(\d+)", line)
            if not match:
                continue
            try:
                sequences.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue
        return sequences

    def _latest_manifest_sequence(self, monitor_id: str) -> Optional[int]:
        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return None

        try:
            if not session.manifest_path.exists():
                return None
            manifest_content = session.manifest_path.read_text("utf-8")
        except Exception:
            return None

        sequences = self._manifest_segment_sequences(manifest_content)
        if not sequences:
            return None
        return max(sequences)

    def estimate_manifest_buffer_seconds_behind(self, monitor_id: str) -> float:
        """Estimate lag based on the FFmpeg HLS playlist window depth."""
        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return 0.0

        try:
            if not session.manifest_path.exists():
                return 0.0
            manifest_content = session.manifest_path.read_text("utf-8")
        except Exception:
            return 0.0

        sequences = self._manifest_segment_sequences(manifest_content)
        if len(sequences) <= 1:
            return 0.0

        lag_segments = max(0, max(sequences) - min(sequences))
        return max(0.0, float(lag_segments) * float(self._hls_segment_time_s))

    def estimate_segment_buffer_seconds_behind(self, monitor_id: str, sequence: Optional[int]) -> float:
        """Estimate per-client lag from requested segment sequence vs latest playlist sequence."""
        if sequence is None:
            return self.estimate_manifest_buffer_seconds_behind(monitor_id)

        latest_sequence = self._latest_manifest_sequence(monitor_id)
        if latest_sequence is None:
            return self.estimate_manifest_buffer_seconds_behind(monitor_id)

        try:
            current_sequence = int(sequence)
        except (TypeError, ValueError):
            return self.estimate_manifest_buffer_seconds_behind(monitor_id)

        lag_segments = max(0, int(latest_sequence) - current_sequence)
        return max(0.0, float(lag_segments) * float(self._hls_segment_time_s))

    def list_clients(self, monitor_id: str, max_idle_seconds: Optional[int] = None) -> List[Dict[str, Any]]:
        from .client_tracker import client_tracking_service

        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return []

        idle_limit = self._client_record_ttl_s if max_idle_seconds is None else self._to_int(max_idle_seconds, default=self._client_record_ttl_s)
        clients = client_tracking_service.get_stream_clients(
            key,
            protocol="HLS",
            worker_id="api_hls_segmenter",
        )
        if idle_limit > 0:
            clients = [c for c in clients if float(c.get("inactive_seconds") or 0.0) <= float(idle_limit)]

        if clients:
            return clients

        legacy_clients = self._list_legacy_session_clients(session, idle_limit=idle_limit, emit_disconnect_metric=False)
        return legacy_clients

    def count_active_clients(self, max_idle_seconds: Optional[int] = None) -> int:
        from .client_tracker import client_tracking_service

        idle_limit = self._client_record_ttl_s if max_idle_seconds is None else self._to_int(max_idle_seconds, default=self._client_record_ttl_s)
        total = 0
        for key in self._sessions.keys():
            stream_clients = client_tracking_service.get_stream_clients(
                key,
                protocol="HLS",
                worker_id="api_hls_segmenter",
            )
            if idle_limit > 0 and stream_clients:
                active_clients: List[Dict[str, Any]] = []
                for row in stream_clients:
                    inactive_seconds = float(row.get("inactive_seconds") or 0.0)
                    if inactive_seconds > float(idle_limit):
                        client_tracking_service.unregister_client(
                            client_id=str(row.get("client_id") or row.get("id") or ""),
                            stream_id=key,
                            protocol="HLS",
                        )
                        continue
                    active_clients.append(row)
                stream_clients = active_clients

            session_total = len(stream_clients)
            if session_total == 0:
                session = self._sessions.get(key)
                if session is not None:
                    session_total = len(
                        self._list_legacy_session_clients(
                            session,
                            idle_limit=idle_limit,
                            emit_disconnect_metric=True,
                        )
                    )

            total += session_total
        return total

    def _list_legacy_session_clients(
        self,
        session: SegmenterSession,
        *,
        idle_limit: int,
        emit_disconnect_metric: bool,
    ) -> List[Dict[str, Any]]:
        legacy_clients = getattr(session, "clients", None)
        if not isinstance(legacy_clients, dict):
            return []

        now = time.time()
        clients: List[Dict[str, Any]] = []
        stale_ids: List[str] = []

        for client_id, details in legacy_clients.items():
            try:
                last_active = float(str((details or {}).get("last_active") or now))
            except (TypeError, ValueError):
                last_active = now

            if idle_limit > 0 and (now - last_active) > idle_limit:
                stale_ids.append(str(client_id))
                continue

            payload = dict(details or {})
            payload["client_id"] = str(payload.get("client_id") or client_id)
            payload["id"] = str(payload.get("id") or payload["client_id"])
            payload["stream_id"] = str(payload.get("stream_id") or session.monitor_id)
            payload["ip_address"] = str(payload.get("ip_address") or payload.get("ip") or "unknown")
            payload["ip"] = str(payload.get("ip") or payload["ip_address"])
            payload["user_agent"] = str(payload.get("user_agent") or payload.get("ua") or "unknown")
            payload["ua"] = str(payload.get("ua") or payload["user_agent"])
            payload["protocol"] = "HLS"
            payload["type"] = "HLS"
            payload["connected_at"] = float(payload.get("connected_at") or last_active)
            payload["last_active"] = last_active
            payload["inactive_seconds"] = max(0.0, now - last_active)
            payload["bps"] = float(payload.get("bps") or 0.0)
            payload["bytes_sent"] = float(payload.get("bytes_sent") or 0.0)
            clients.append(payload)

        if stale_ids:
            for stale_id in stale_ids:
                legacy_clients.pop(stale_id, None)
            if emit_disconnect_metric:
                try:
                    from .metrics import observe_proxy_client_disconnect

                    for _ in stale_ids:
                        observe_proxy_client_disconnect("HLS")
                except Exception:
                    pass

        clients.sort(key=lambda item: float(item.get("last_active") or 0.0), reverse=True)
        return clients

    def collect_legacy_stats_probe(
        self,
        monitor_id: str,
        samples: int = 1,
        per_sample_timeout_s: float = 1.0,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Collect STATUS probe from API-mode HLS control client with TS-style caching semantics."""
        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return None

        # Segmenter is active while process has no returncode.
        if session.process.returncode is not None:
            return session.legacy_probe_cache

        if not session.control_client:
            return session.legacy_probe_cache

        now = time.monotonic()
        if not force and session.legacy_probe_cache is not None:
            if (now - session.legacy_probe_cache_ts) < self._legacy_probe_cache_ttl_s:
                return session.legacy_probe_cache

        locked = session.legacy_probe_lock.acquire(timeout=0.05)
        if not locked:
            return session.legacy_probe_cache

        try:
            control_client = session.control_client
            if not control_client:
                return session.legacy_probe_cache

            collect_method = getattr(control_client, "collect_status_samples", None)
            if not callable(collect_method):
                return session.legacy_probe_cache

            probe = collect_method(
                samples=max(1, int(samples)),
                interval_s=0.0,
                per_sample_timeout_s=max(0.2, float(per_sample_timeout_s)),
            )
            if not isinstance(probe, dict):
                return session.legacy_probe_cache
            session.legacy_probe_cache = probe
            session.legacy_probe_cache_ts = time.monotonic()
            return probe
        except Exception as e:
            logger.debug("Legacy stats probe failed for API-mode HLS stream %s: %s", key, e)
            return session.legacy_probe_cache
        finally:
            session.legacy_probe_lock.release()

    async def get_or_wait_manifest(self, monitor_id: str, timeout_s: float = 15.0) -> Optional[Path]:
        """Return manifest for an existing session, waiting briefly if FFmpeg is still warming up."""
        key = self._sanitize_monitor_id(monitor_id)

        async with self._lock:
            session = self._sessions.get(key)
            if not session:
                return None

            session.last_activity = time.time()
            if session.manifest_path.exists():
                return session.manifest_path

            process_is_running = session.process.returncode is None

        if not process_is_running:
            return None

        try:
            await self._wait_for_manifest(key, timeout_s=timeout_s)
        except (TimeoutError, RuntimeError):
            return None

        session = self._sessions.get(key)
        if not session:
            return None
        if not session.manifest_path.exists():
            return None
        return session.manifest_path

    async def start_segmenter(self, monitor_id: str, source_mpegts_url: str, metadata: Optional[Dict[str, Any]] = None) -> Path:
        """Start FFmpeg HLS segmenter and wait until index.m3u8 exists."""
        key = self._sanitize_monitor_id(monitor_id)
        source = str(source_mpegts_url or "").strip()
        if not source:
            raise ValueError("source_mpegts_url is required")

        now = time.time()
        wait_for_existing = False
        async with self._lock:
            if not self._loop:
                try:
                    self._loop = asyncio.get_running_loop()
                except RuntimeError:
                    self._loop = None

            existing = self._sessions.get(key)
            if existing and existing.process.returncode is None:
                if existing.source_mpegts_url != source:
                    await self._stop_locked(key, emit_stream_ended=True)
                    existing = None
                else:
                    # Keep in-flight segmenter startup stable across retry storms.
                    existing.last_activity = now
                    self._apply_metadata(existing, metadata)
                    if existing.manifest_path.exists():
                        return existing.manifest_path
                    logger.info("Reusing warming external HLS segmenter for stream %s", key)
                    wait_for_existing = True

            if existing and not wait_for_existing:
                await self._stop_locked(key, emit_stream_ended=True)

            if not wait_for_existing:
                out_dir = self._get_output_dir(key)
                await asyncio.to_thread(shutil.rmtree, out_dir, True)
                await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
                manifest_path = out_dir / "index.m3u8"

                hls_flags = "delete_segments+append_list"
                if self._hls_split_by_time:
                    # Avoid waiting for long GOP keyframes before first segment emission.
                    hls_flags += "+split_by_time"

                cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    source,
                    "-c",
                    "copy",
                    "-f",
                    "hls",
                    "-hls_time",
                    str(self._hls_segment_time_s),
                    "-hls_list_size",
                    str(self._hls_list_size),
                    "-hls_flags",
                    hls_flags,
                    str(manifest_path),
                ]

                logger.info("Starting external HLS segmenter for stream %s", key)
                try:
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except FileNotFoundError as e:
                    raise RuntimeError("ffmpeg binary not found in orchestrator runtime image") from e

                session = SegmenterSession(
                    monitor_id=key,
                    source_mpegts_url=source,
                    output_dir=out_dir,
                    manifest_path=manifest_path,
                    process=process,
                    started_at=now,
                    last_activity=now,
                )
                self._apply_metadata(session, metadata)
                self._sessions[key] = session

        await self._wait_for_manifest(key)
        session = self._sessions.get(key)
        if not session:
            raise RuntimeError(f"Segmenter session {key} not found")
        return session.manifest_path

    async def _wait_for_manifest(self, monitor_id: str, timeout_s: float = 15.0) -> None:
        started = time.time()
        while (time.time() - started) < timeout_s:
            session = self._sessions.get(monitor_id)
            if not session:
                raise RuntimeError(f"Segmenter session {monitor_id} not found")

            if session.manifest_path.exists():
                return

            if session.process.returncode is not None:
                stderr = ""
                try:
                    stderr_bytes = await session.process.stderr.read() if session.process.stderr else b""
                    stderr = stderr_bytes.decode("utf-8", errors="ignore").strip()
                except Exception:
                    stderr = ""
                raise RuntimeError(f"FFmpeg exited before manifest was created (code={session.process.returncode}): {stderr}")

            await asyncio.sleep(0.2)

        raise TimeoutError(f"Timed out waiting for HLS manifest for {monitor_id}")

    async def stop_segmenter(self, monitor_id: str, emit_stream_ended: bool = True) -> bool:
        key = self._sanitize_monitor_id(monitor_id)
        async with self._lock:
            return await self._stop_locked(key, emit_stream_ended=emit_stream_ended)

    async def _stop_locked(self, key: str, emit_stream_ended: bool = True) -> bool:
        from .client_tracker import client_tracking_service

        session = self._sessions.pop(key, None)
        if not session:
            return False

        client_tracking_service.unregister_stream(
            stream_id=key,
            protocol="HLS",
            worker_id="api_hls_segmenter",
        )

        logger.info("Stopping external HLS segmenter for stream %s", key)
        process = session.process
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except Exception:
                    pass

        if session.control_client is not None:
            await self._shutdown_control_client(session.control_client)

        if emit_stream_ended and session.stream_id:
            try:
                from ..models.schemas import StreamEndedEvent
                from ..services.internal_events import handle_stream_ended

                await asyncio.to_thread(
                    handle_stream_ended,
                    StreamEndedEvent(
                        container_id=session.container_id or None,
                        stream_id=session.stream_id,
                        reason="api_hls_segmenter_stopped",
                    ),
                )
            except Exception:
                logger.debug("Failed emitting stream ended event for API-mode HLS segmenter %s", key, exc_info=True)

        await asyncio.to_thread(shutil.rmtree, session.output_dir, True)
        return True

    def stop_segmenter_nowait(self, monitor_id: str, emit_stream_ended: bool = True) -> None:
        """Best-effort non-blocking stop for sync call-sites."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.stop_segmenter(monitor_id, emit_stream_ended=emit_stream_ended))
            return
        except RuntimeError:
            pass

        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self.stop_segmenter(monitor_id, emit_stream_ended=emit_stream_ended),
                    self._loop,
                )
            except Exception:
                logger.debug("Failed scheduling async segmenter stop for %s", monitor_id, exc_info=True)

    def record_activity(self, monitor_id: str) -> None:
        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if session:
            session.last_activity = time.time()

    def get_manifest_path(self, monitor_id: str) -> Optional[Path]:
        key = self._sanitize_monitor_id(monitor_id)
        session = self._sessions.get(key)
        if not session:
            return None
        return session.manifest_path

    async def read_manifest(self, monitor_id: str, rewrite: bool = True) -> str:
        key = self._sanitize_monitor_id(monitor_id)
        path = self.get_manifest_path(key)
        if not path:
            raise FileNotFoundError(f"No segmenter session for {key}")
        if not path.exists():
            raise FileNotFoundError(f"Manifest not available for {key}")

        content = await asyncio.to_thread(path.read_text, "utf-8")
        self.record_activity(key)
        if rewrite:
            return self.rewrite_manifest(key, content)
        return content

    def rewrite_manifest(self, monitor_id: str, manifest_content: str) -> str:
        key = self._sanitize_monitor_id(monitor_id)
        lines = []
        for raw_line in manifest_content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                lines.append(raw_line)
                continue

            segment_name = Path(line.split("?", 1)[0]).name
            if not segment_name:
                lines.append(raw_line)
                continue

            lines.append(f"/api/v1/hls/{key}/{segment_name}")
        return "\n".join(lines) + "\n"

    def get_segment_file_path(self, monitor_id: str, segment_filename: str) -> Optional[Path]:
        key = self._sanitize_monitor_id(monitor_id)
        normalized_segment = Path(str(segment_filename or "")).name
        if not normalized_segment or normalized_segment != str(segment_filename):
            return None

        session = self._sessions.get(key)
        if not session:
            return None

        path = (session.output_dir / normalized_segment).resolve()
        try:
            path.relative_to(session.output_dir.resolve())
        except Exception:
            return None
        return path

    async def cleanup_idle_segmenters(self, max_idle_seconds: int) -> int:
        if max_idle_seconds <= 0:
            return 0

        now = time.time()
        stale_ids = []
        for key, session in list(self._sessions.items()):
            if (now - session.last_activity) > max_idle_seconds:
                stale_ids.append(key)

        cleaned = 0
        for key in stale_ids:
            if await self.stop_segmenter(key):
                cleaned += 1
        return cleaned


hls_segmenter_service = HLSSegmenterService()
