from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

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


class HLSSegmenterService:
    """Manage external FFmpeg HLS segmenters for API-mode playback."""

    def __init__(self, base_dir: str = "/tmp/acestream_hls"):
        self._base_dir = Path(base_dir)
        self._lock = asyncio.Lock()
        self._sessions: Dict[str, SegmenterSession] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @staticmethod
    def _sanitize_monitor_id(monitor_id: str) -> str:
        raw = str(monitor_id or "").strip()
        if not raw:
            raise ValueError("monitor_id is required")
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", raw)

    def _get_output_dir(self, monitor_id: str) -> Path:
        return self._base_dir / monitor_id

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

    async def start_segmenter(self, monitor_id: str, source_mpegts_url: str) -> Path:
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
                # Keep in-flight segmenter startup stable across retry storms.
                existing.last_activity = now
                if existing.manifest_path.exists():
                    return existing.manifest_path
                logger.info("Reusing warming external HLS segmenter for stream %s", key)
                wait_for_existing = True

            if existing and not wait_for_existing:
                await self._stop_locked(key)

            if not wait_for_existing:
                out_dir = self._get_output_dir(key)
                await asyncio.to_thread(shutil.rmtree, out_dir, True)
                await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
                manifest_path = out_dir / "index.m3u8"

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
                    "6",
                    "-hls_list_size",
                    "5",
                    "-hls_flags",
                    "delete_segments+append_list",
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

    async def stop_segmenter(self, monitor_id: str) -> bool:
        key = self._sanitize_monitor_id(monitor_id)
        async with self._lock:
            return await self._stop_locked(key)

    async def _stop_locked(self, key: str) -> bool:
        session = self._sessions.pop(key, None)
        if not session:
            return False

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

        await asyncio.to_thread(shutil.rmtree, session.output_dir, True)
        return True

    def stop_segmenter_nowait(self, monitor_id: str) -> None:
        """Best-effort non-blocking stop for sync call-sites."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.stop_segmenter(monitor_id))
            return
        except RuntimeError:
            pass

        if self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self.stop_segmenter(monitor_id), self._loop)
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
