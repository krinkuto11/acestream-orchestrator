"""
FastAPI-based HLS Proxy for AceStream.
Rewritten from context/hls_proxy to use FastAPI instead of Django.
Handles HLS manifest proxying, segment buffering, and URL rewriting.
"""

import threading
import logging
import time
import requests
import httpx
import m3u8
import os
import asyncio
import uuid
from typing import Dict, Optional, Set, Any, List
from urllib.parse import urljoin, urlparse
from .config_helper import ConfigHelper
from .hls_utils import get_hls_padding_comment
from .constants import PROXY_MODE_HTTP, normalize_proxy_mode
from .utils import get_logger

logger = get_logger()


class HLSConfig:
    """Configuration for HLS proxy - uses ConfigHelper for environment-based settings"""
    DEFAULT_USER_AGENT = "VLC/3.0.16 LibVLC/3.0.16"
    
    @staticmethod
    def MAX_SEGMENTS():
        """Maximum number of segments to buffer"""
        return ConfigHelper.hls_max_segments()
    
    @staticmethod
    def INITIAL_SEGMENTS():
        """Minimum number of segments before playback"""
        return ConfigHelper.hls_initial_segments()
    
    @staticmethod
    def WINDOW_SIZE():
        """Number of segments in manifest window"""
        return ConfigHelper.hls_window_size()
    
    @staticmethod
    def BUFFER_READY_TIMEOUT():
        """Timeout for initial buffer to be ready"""
        return ConfigHelper.hls_buffer_ready_timeout()
    
    @staticmethod
    def FIRST_SEGMENT_TIMEOUT():
        """Timeout for first segment to be available"""
        return ConfigHelper.hls_first_segment_timeout()
    
    @staticmethod
    def INITIAL_BUFFER_SECONDS():
        """Target duration for initial buffer"""
        return ConfigHelper.hls_initial_buffer_seconds()
    
    @staticmethod
    def MAX_INITIAL_SEGMENTS():
        """Maximum segments to fetch during initial buffering"""
        return ConfigHelper.hls_max_initial_segments()
    
    @staticmethod
    def SEGMENT_FETCH_INTERVAL():
        """Multiplier for manifest fetch interval (relative to target duration)"""
        return ConfigHelper.hls_segment_fetch_interval()


class ClientManager:
    """Manages HLS client activity using the central client tracker service."""

    def __init__(self, stream_id: str = ""):
        self.stream_id = str(stream_id or f"__hls_local_{id(self)}")
        self.worker_id = f"hls_proxy:{id(self)}"
        self.last_activity: Dict[str, float] = {}
        self.clients: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _sync_last_activity_from_clients(self, clients: List[Dict[str, Any]]) -> None:
        rebuilt_activity: Dict[str, float] = {}
        rebuilt_clients: Dict[str, Dict[str, Any]] = {}
        now = time.time()
        for payload in clients:
            ip = str(payload.get("ip_address") or payload.get("ip") or "unknown")
            client_id = str(payload.get("client_id") or payload.get("id") or ip)
            ts = self._safe_float(payload.get("last_active"), default=now)
            previous = rebuilt_activity.get(ip)
            if previous is None or ts > previous:
                rebuilt_activity[ip] = ts
            rebuilt_clients[client_id] = dict(payload)
        self.last_activity = rebuilt_activity
        self.clients = rebuilt_clients
    def record_client_activity(
        self,
        client_ip: Optional[str] = None,
        client_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        bytes_sent: Optional[float] = None,
        chunks_sent: Optional[int] = None,
        request_kind: Optional[str] = None,
        sequence: Optional[int] = None,
        buffer_seconds_behind: Optional[float] = None,
        is_prebuffering: Optional[bool] = None,
        bitrate: int = 0,
        now: Optional[float] = None,
    ):
        """Record client activity and transfer counters in the central tracker."""
        from ..services.client_tracker import client_tracking_service

        ts = now if now is not None else time.time()
        normalized_ip = str(client_ip or "unknown")
        normalized_client_id = str(client_id or normalized_ip)
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

        tracked = client_tracking_service.record_activity(
            client_id=normalized_client_id,
            stream_id=self.stream_id,
            bytes_delta=bytes_delta,
            protocol="HLS",
            ip_address=normalized_ip,
            user_agent=normalized_ua,
            request_kind=request_kind,
            chunks_delta=chunks_delta,
            sequence=sequence,
            buffer_seconds_behind=buffer_seconds_behind,
            now=ts,
            is_prebuffering=is_prebuffering,
            worker_id=self.worker_id,
            bitrate=bitrate,
        )

        with self.lock:
            previous_ip_activity = self.last_activity.get(normalized_ip)
            self.last_activity[normalized_ip] = ts if previous_ip_activity is None else max(previous_ip_activity, ts)
            client_payload = dict(self.clients.get(normalized_client_id) or {})
            client_payload.update({
                "client_id": normalized_client_id,
                "ip_address": normalized_ip,
                "user_agent": normalized_ua,
                "last_active": ts,
                "last_request_kind": str(request_kind or "").strip().lower(),
                "buffer_seconds_behind": max(
                    0.0,
                    self._safe_float(
                        buffer_seconds_behind,
                        default=client_payload.get("buffer_seconds_behind", 0.0),
                    ),
                ),
            })
            self.clients[normalized_client_id] = client_payload

        if tracked.get("requests_total") == 1:
            logger.info(f"New client connected: {normalized_ip} ({normalized_client_id})")
        else:
            logger.debug(f"Client activity: {normalized_ip} ({normalized_client_id})")
                
    def cleanup_inactive(self, timeout: float) -> bool:
        """Remove inactive clients and return True if no clients remain"""
        from ..services.client_tracker import client_tracking_service

        if timeout <= 0:
            client_tracking_service.unregister_stream(
                stream_id=self.stream_id,
                protocol="HLS",
                worker_id=self.worker_id,
            )
            with self.lock:
                self.last_activity = {}
            return True

        now = time.time()
        client_tracking_service.prune_stale_clients(timeout)

        clients = client_tracking_service.get_stream_clients(
            self.stream_id,
            protocol="HLS",
            worker_id=self.worker_id,
        )
        with self.lock:
            self._sync_last_activity_from_clients(clients)

        if clients:
            oldest = min(now - self._safe_float(p.get("last_active"), default=now) for p in clients)
            logger.debug(f"Active clients: {len(clients)}, oldest activity: {oldest:.1f}s ago")

        return len(clients) == 0

    def list_clients(self, max_idle_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
        """Return active clients enriched with transfer counters."""
        from ..services.client_tracker import client_tracking_service

        if max_idle_seconds is not None and max_idle_seconds > 0:
            client_tracking_service.prune_stale_clients(max_idle_seconds)

        clients = client_tracking_service.get_stream_clients(
            self.stream_id,
            protocol="HLS",
            worker_id=self.worker_id,
        )
        with self.lock:
            self._sync_last_activity_from_clients(clients)
        return clients

    def count_active_clients(self) -> int:
        from ..services.client_tracker import client_tracking_service

        return client_tracking_service.count_active_clients(
            stream_id=self.stream_id,
            protocol="HLS",
            worker_id=self.worker_id,
        )
    
    def has_clients(self) -> bool:
        """Check if there are any active clients"""
        return self.count_active_clients() > 0


class StreamBuffer:
    """Thread-safe buffer for HLS segments with optimized read performance
    
    Uses a combination of a regular lock for writes and allows concurrent reads
    through careful state management. In practice, reads are much more frequent
    than writes, so we optimize for read performance.
    """
    
    def __init__(self):
        self.buffer: Dict[int, bytes] = {}
        self.lock: threading.RLock = threading.RLock()  # RLock allows recursive locking
    
    def __getitem__(self, key: int) -> Optional[bytes]:
        """Get segment data by sequence number (read operation)"""
        # For reads, we still need a lock but RLock is more efficient for this pattern
        with self.lock:
            return self.buffer.get(key)
    
    def __setitem__(self, key: int, value: bytes):
        """Store segment data by sequence number (write operation)"""
        with self.lock:
            self.buffer[key] = value
            # Cleanup old segments if we exceed MAX_SEGMENTS
            if len(self.buffer) > HLSConfig.MAX_SEGMENTS():
                keys = sorted(self.buffer.keys())
                to_remove = keys[:-HLSConfig.MAX_SEGMENTS()]
                for k in to_remove:
                    del self.buffer[k]
    
    def __contains__(self, key: int) -> bool:
        """Check if sequence number exists in buffer (read operation)"""
        with self.lock:
            return key in self.buffer
    
    def keys(self):
        """Get list of available sequence numbers (read operation)"""
        with self.lock:
            return list(self.buffer.keys())


class StreamManager:
    """Manages HLS stream state and fetching"""
    
    def __init__(self, playback_url: str, channel_id: str, engine_host: str, engine_port: int, 
                 engine_container_id: str, session_info: Dict[str, Any], api_key: Optional[str] = None,
                 seekback: int = 0,
                 engine_api_port: Optional[int] = None,
                 event_loop: Optional[asyncio.AbstractEventLoop] = None,
                 bitrate: int = 0):
        self.playback_url = playback_url
        self.channel_id = channel_id
        self.engine_host = engine_host
        self.engine_port = engine_port
        self.engine_api_port = int(engine_api_port or 62062)
        self.engine_container_id = engine_container_id
        self.stream_key_type = (stream_key_type or "content_id").strip().lower()
        normalized_file_indexes = str(file_indexes if file_indexes is not None else "0").strip()
        self.file_indexes = normalized_file_indexes or "0"
        # HTTP mode HLS does not support liveseek/seekback.
        self.seekback = 0
        self.bitrate = bitrate
        self.control_mode = normalize_proxy_mode(ConfigHelper.control_mode(), default=PROXY_MODE_HTTP)
        self.running = True
        
        # Session info from AceStream API
        self.playback_session_id = session_info.get('playback_session_id')
        self.stat_url = session_info.get('stat_url')
        self.command_url = session_info.get('command_url')
        self.is_live = session_info.get('is_live', 1)
        self.owns_engine_session = bool(session_info.get('owns_engine_session', True))
        
        # API key for orchestrator events
        self.api_key = api_key
        
        # Reference to main event loop for thread-safe async task scheduling
        self._event_loop = event_loop
        
        # Sequence tracking
        self.next_sequence = 0
        self.segment_durations: Dict[int, float] = {}
        self.segment_sources: Dict[int, str] = {}
        
        # Manifest info
        self.target_duration = 10.0
        self.manifest_version = 3
        
        # Buffer state
        self.buffer_ready = threading.Event()
        self.initial_buffering = True
        self.buffered_duration = 0.0
        self.last_manifest_buffer_seconds_behind = 0.0
        self.total_bytes_fetched = 0
        self.null_cc = 0
        
        # Orchestrator event tracking
        self.stream_id = None  # Will be set after sending start event
        self._ended_event_sent = False  # Track if we've already sent the ended event
        
        # Client management (for activity-based cleanup)
        self.client_manager: Optional[ClientManager] = None
        self.cleanup_thread: Optional[threading.Thread] = None
        self.cleanup_running = False

        self._swap_lock = threading.RLock()
        self._legacy_api_lock = threading.Lock()
        self.ace_api_client: Optional[AceLegacyApiClient] = None
        
        logger.info(f"Initialized HLS stream manager for channel {channel_id} bitrate={self.bitrate} bps")
        
        # Start telemetry heartbeat if in an async context
        if self._event_loop and self._event_loop.is_running():
            asyncio.run_coroutine_threadsafe(self._telemetry_heartbeat_loop(), self._event_loop)

    def _is_api_mode(self) -> bool:
        return False  # HLSProxyServer is only for HTTP mode (integrated segmenter)

    def collect_legacy_stats_probe(self, force: bool = False):
        """Fetch current live position from AceStream engine if using API control."""
        if not self._is_api_mode() or not self.ace_api_client or not self.running:
            return None

        # Simplified probe for HLS failover position alignment
        try:
            with self._legacy_api_lock:
                if not self.ace_api_client:
                    return None
                return self.ace_api_client.collect_status_samples(samples=1, interval_s=0.0, per_sample_timeout_s=1.0)
        except Exception as e:
            logger.debug(f"HLS legacy stats probe failed: {e}")
            return None

    def get_playback_context(self) -> Dict[str, str]:
        """Return playback URL and engine identity atomically for fetch operations."""
        with self._swap_lock:
            return {
                "playback_url": str(self.playback_url),
                "engine_container_id": str(self.engine_container_id or ""),
            }

    def record_segment_metadata(self, sequence: int, duration: float, source_engine_id: str):
        """Store duration and source engine metadata for a buffered segment."""
        self.segment_durations[sequence] = duration
        self.segment_sources[sequence] = str(source_engine_id or self.engine_container_id or "")

        # Keep metadata bounded to avoid unbounded growth for long-lived streams.
        max_metadata_entries = max(HLSConfig.MAX_SEGMENTS() * 3, HLSConfig.WINDOW_SIZE() * 4)
        if len(self.segment_durations) > max_metadata_entries:
            oldest = sorted(self.segment_durations.keys())[:-max_metadata_entries]
            for seq in oldest:
                self.segment_durations.pop(seq, None)
                self.segment_sources.pop(seq, None)

    def update_dynamic_bitrate(self, segment_size_bytes: int, segment_duration: float):
        """Update dynamic bitrate using per-segment measurement and EMA."""
        if segment_duration <= 0 or segment_size_bytes <= 0:
            return

        # Instantaneous bitrate (bps)
        instant_bitrate = int((segment_size_bytes * 8) / segment_duration)
        
        # If initial bitrate is 0, set it directly
        if self.bitrate <= 0:
            self.bitrate = instant_bitrate
        else:
            # EMA smoothing factor: 0.25 new value, 0.75 old value
            self.bitrate = int((instant_bitrate * 0.25) + (self.bitrate * 0.75))
            
        logger.debug(
            "Updated dynamic HLS bitrate for %s: %s bps (size: %s, duration: %s)", 
            self.channel_id, self.bitrate, segment_size_bytes, segment_duration
        )

    def _build_engine_stream_params(self) -> Dict[str, str]:
        params: Dict[str, str] = {
            "format": "json",
            "pid": str(uuid.uuid4()),
            "file_indexes": self.file_indexes,
        }

        if self.seekback > 0:
            params["seekback"] = str(self.seekback)

        if self.stream_key_type in {"content_id", "infohash"}:
            params["id"] = self.channel_id
            if self.stream_key_type == "infohash":
                params["infohash"] = self.channel_id
        elif self.stream_key_type == "torrent_url":
            params["torrent_url"] = self.channel_id
        elif self.stream_key_type == "direct_url":
            params["direct_url"] = self.channel_id
            params["url"] = self.channel_id
        elif self.stream_key_type == "raw_data":
            params["raw_data"] = self.channel_id
        else:
            params["id"] = self.channel_id

        return params


    def _request_stream_session_http_for_engine(self, engine_host: str, engine_port: int) -> Dict[str, Any]:
        """Request a new HTTP-mode AceStream HLS session from a specific engine."""
        try:
            hls_url = f"http://{engine_host}:{int(engine_port)}/ace/manifest.m3u8"
            params = self._build_engine_stream_params()
            
            response = requests.get(hls_url, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()

            if payload.get("error"):
                raise RuntimeError(f"AceStream engine returned error: {payload.get('error')}")

            response_data = payload.get("response", {})
            playback_url = str(response_data.get("playback_url") or "").strip()
            if not playback_url:
                raise RuntimeError("No playback_url in HLS swap response")

            # Rewrite localhost URLs
            parsed_url = urlparse(playback_url)
            if parsed_url.hostname in {"127.0.0.1", "localhost"}:
                port_part = f":{parsed_url.port}" if parsed_url.port else ""
                playback_url = f"{parsed_url.scheme}://{engine_host}{port_part}{parsed_url.path}"
                if parsed_url.query:
                    playback_url = f"{playback_url}?{parsed_url.query}"

            return {
                "playback_url": playback_url,
                "playback_session_id": response_data.get("playback_session_id"),
                "stat_url": response_data.get("stat_url") or "",
                "command_url": response_data.get("command_url") or "",
                "is_live": int(response_data.get("is_live", 1) or 1),
                "bitrate": int(response_data.get("bitrate") or 0),
            }
        except Exception as e:
            logger.error(f"Failed to request HTTP HLS session: {e}", exc_info=True)
            raise

    async def _telemetry_heartbeat_loop(self):
        """Periodic heartbeat to keep UI stats fresh even between HLS segment polls."""
        logger.debug(f"Starting telemetry heartbeat loop for channel {self.channel_id}")
        last_run = time.time()
        interval = 5.0 # Align with TS generator interval
        
        try:
            while self.running:
                now = time.time()
                elapsed = now - last_run
                if elapsed >= interval:
                    if self.client_manager and self.client_manager.has_clients():
                        clients = self.client_manager.list_clients()
                        for client in clients:
                            # Record activity with 0 delta to trigger a refresh check in tracker.
                            # The tracker will use its EMA to report the current smoothed BPS.
                            client_id = client.get("client_id") or client.get("id")
                            if not client_id:
                                continue
                                
                            self.client_manager.record_client_activity(
                                client_id=client_id,
                                client_ip=client.get("ip_address"),
                                user_agent=client.get("user_agent"),
                                bytes_sent=0,
                                chunks_sent=0,
                                request_kind="heartbeat",
                                buffer_seconds_behind=self.last_manifest_buffer_seconds_behind,
                                now=now,
                                is_prebuffering=self.initial_buffering
                            )
                    last_run = now
                
                # Sleep briefly to avoid tight loop but stay responsive to self.running change
                await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Telemetry heartbeat loop failed for {self.channel_id}: {e}")
        finally:
            logger.debug(f"Telemetry heartbeat loop stopped for {self.channel_id}")

    def hot_swap_engine(
        self,
        new_host: str,
        new_port: int,
        new_api_port: int,
        new_container_id: str,
    ) -> Dict[str, Any]:
        """Request a new HLS backend session and switch this channel to the new engine."""
        if not self.running:
            raise RuntimeError("hls_channel_not_running")

        target_container_id = str(new_container_id or "").strip()
        if not new_host or not target_container_id:
            raise RuntimeError("invalid_swap_target")

        if target_container_id == self.engine_container_id:
            return {
                "swapped": False,
                "reason": "already_on_target_engine",
                "old_container_id": self.engine_container_id,
                "new_container_id": target_container_id,
            }

        # --- Failover behavior for HTTP HLS: Always resume at live edge ---
        # Position alignment via seekback is not possible in HTTP mode.
        self.seekback = 0
        # -----------------------------------------------------------------

        session_updates = self._request_stream_session_http_for_engine(new_host, int(new_port))

        with self._swap_lock:
            old_container_id = self.engine_container_id
            self.engine_host = str(new_host)
            self.engine_port = int(new_port)
            self.engine_api_port = int(new_api_port or 62062)
            self.engine_container_id = target_container_id
            self.playback_url = session_updates["playback_url"]
            self.playback_session_id = session_updates["playback_session_id"] or self.playback_session_id
            self.stat_url = session_updates["stat_url"] or self.stat_url
            self.command_url = session_updates["command_url"] or self.command_url
            self.is_live = int(session_updates["is_live"] or self.is_live or 1)
            self.bitrate = int(session_updates["bitrate"] or self.bitrate or 0)

        logger.info(
            "Applied HLS hot swap for channel=%s old_engine=%s new_engine=%s bitrate=%s bps",
            self.channel_id,
            old_container_id,
            target_container_id,
            self.bitrate
        )

        return {
            "swapped": True,
            "old_container_id": old_container_id,
            "new_container_id": target_container_id,
            "playback_session_id": self.playback_session_id,
            "stat_url": self.stat_url,
            "command_url": self.command_url,
            "is_live": self.is_live,
            "bitrate": self.bitrate,
        }
    
    def stop(self):
        """Stop the stream manager"""
        self.running = False
        self.cleanup_running = False
        logger.info(f"Stopping stream manager for channel {self.channel_id}")
        
        # Send stop command only when this HLS proxy owns the engine session.
        if not self.owns_engine_session:
            logger.info(
                "Skipping engine stop for channel %s because session is owned by monitoring",
                self.channel_id,
            )
        elif self.command_url:
            try:
                requests.get(f"{self.command_url}?method=stop", timeout=5)
                logger.info("Sent stop command to AceStream engine")
            except Exception as e:
                logger.warning(f"Failed to send stop command: {e}")
    
    def _schedule_async_task(self, coro, task_name: str, fallback_warning: str):
        """Schedule an async task in a thread-safe manner.
        
        This helper handles both async and thread contexts by:
        1. Using create_task if in an async context (has running loop)
        2. Using run_coroutine_threadsafe if in a thread context (uses stored event loop)
        
        Args:
            coro: The coroutine to schedule
            task_name: Name for the async task (for debugging)
            fallback_warning: Warning message if event loop is not available
        """
        try:
            # Try to get the running loop (works in async context)
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context, use create_task
                asyncio.create_task(coro, name=task_name)
            except RuntimeError:
                # No running loop in current thread - use stored event loop if available
                if self._event_loop and self._event_loop.is_running():
                    # Schedule on the main loop from this thread
                    asyncio.run_coroutine_threadsafe(coro, self._event_loop)
                else:
                    # Fallback for sync contexts (tests/manual calls): run to completion.
                    logger.warning(fallback_warning)
                    asyncio.run(coro)
        except Exception as e:
            logger.error(f"Failed to schedule async task {task_name}: {e}")
    
    def _send_stream_started_event(self):
        """Send stream started event to orchestrator using async task (non-blocking)
        
        Runs asynchronously in a background task to avoid blocking initialize_channel().
        This ensures the UI remains responsive during stream initialization.
        
        Note: stream_id is set to a temporary value immediately, then updated by the async
        task when the event handler completes. This is intentional for fire-and-forget async.
        
        Thread-safe: Can be called from both async and thread contexts.
        """
        async def _send_event():
            try:
                # Import here to avoid circular dependencies
                from ..models.schemas import StreamStartedEvent, StreamKey, EngineAddress, SessionInfo
                from ..services.internal_events import handle_stream_started
                
                # Build event object
                event = StreamStartedEvent(
                    container_id=self.engine_container_id,
                    engine=EngineAddress(
                        host=self.engine_host,
                        port=self.engine_port
                    ),
                    stream=StreamKey(
                        key_type=self.stream_key_type,
                        key=self.channel_id,
                        file_indexes=self.file_indexes,
                        seekback=self.seekback,
                        live_delay=self.seekback,
                        control_mode=self.control_mode,
                    ),
                    session=SessionInfo(
                        playback_session_id=self.playback_session_id,
                        stat_url=self.stat_url,
                        command_url=self.command_url,
                        is_live=self.is_live
                    ),
                    labels={
                        "source": "hls_proxy",
                        "stream_mode": "HLS",
                        "stream.input_type": self.stream_key_type,
                        "stream.file_indexes": self.file_indexes,
                        "stream.seekback": str(self.seekback),
                        "stream.live_delay": str(self.seekback),
                    }
                )
                
                # Call internal handler directly (no HTTP request)
                # Run in thread pool since handle_stream_started is synchronous
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, handle_stream_started, event)
                
                # Update stream_id from result
                if result:
                    self.stream_id = result.id
                    logger.info(f"Sent HLS stream started event to orchestrator: stream_id={self.stream_id}")
                else:
                    logger.warning(f"HLS stream started event handler returned no result")
                    self.stream_id = f"temp-hls-{self.channel_id[:16]}-{int(time.time())}"
                
            except Exception as e:
                logger.warning(f"Failed to send HLS stream started event to orchestrator: {e}")
                logger.debug(f"Exception details: {e}", exc_info=True)
                # Generate a fallback stream_id
                self.stream_id = f"fallback-hls-{self.channel_id[:16]}-{int(time.time())}"
        
        # Generate a temporary stream_id immediately so HLS proxy can proceed
        # This will be updated by the async task once the event is processed
        self.stream_id = f"temp-hls-{self.channel_id[:16]}-{int(time.time())}"
        
        # Send event in background using thread-safe helper
        self._schedule_async_task(
            _send_event(),
            task_name=f"HLS-StartEvent-{self.channel_id[:8]}",
            fallback_warning="Event loop not available, cannot send stream started event"
        )
    
    def _send_stream_ended_event(self, reason="normal"):
        """Send stream ended event to orchestrator using async task (non-blocking)
        
        Thread-safe: Can be called from both async and thread contexts.
        Uses run_coroutine_threadsafe when called from a thread.
        """
        # Check if we've already sent the ended event
        if self._ended_event_sent:
            logger.debug(f"HLS stream ended event already sent for stream_id={self.stream_id}, skipping")
            return
        
        # Check if we have a stream_id to send
        if not self.stream_id:
            logger.warning(f"No stream_id available for channel_id={self.channel_id}, cannot send ended event")
            return
        
        async def _send_event():
            try:
                # Import here to avoid circular dependencies
                from ..models.schemas import StreamEndedEvent
                from ..services.internal_events import handle_stream_ended
                
                # Build event object
                event = StreamEndedEvent(
                    container_id=self.engine_container_id,
                    stream_id=self.stream_id,
                    reason=reason
                )
                
                # Call internal handler directly (no HTTP request)
                # Run in thread pool since handle_stream_ended is synchronous
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, handle_stream_ended, event)
                
                # Mark as sent
                self._ended_event_sent = True
                
                logger.info(f"Sent HLS stream ended event to orchestrator: stream_id={self.stream_id}, reason={reason}")
                
            except Exception as e:
                logger.warning(f"Failed to send HLS stream ended event to orchestrator: {e}")
                logger.debug(f"Exception details: {e}", exc_info=True)
        
        # Send event in background using thread-safe helper
        self._schedule_async_task(
            _send_event(),
            task_name=f"HLS-EndEvent-{self.channel_id[:8]}",
            fallback_warning=f"Event loop not available, cannot send stream ended event for {self.stream_id}"
        )
    
    def start_cleanup_monitoring(self, proxy_server):
        """Start background thread for client inactivity monitoring (adapted from context/hls_proxy)"""
        def cleanup_loop():
            # Use a small initial delay to allow first client to connect
            time.sleep(2)
            
            # Monitor client activity
            while self.cleanup_running and self.running:
                try:
                    # Skip cleanup during initial buffering to avoid premature timeout
                    # Initial buffering can take significant time due to network delays
                    if self.initial_buffering:
                        logger.debug(f"Channel {self.channel_id}: Skipping cleanup during initial buffering")
                        time.sleep(5)
                        continue
                    
                    # Unified HLS Idle Shutdown: Respect ConfigHelper.hls_client_idle_timeout()
                    # Derived from Phase 15 harmonization (20s default).
                    idle_timeout = float(ConfigHelper.hls_client_idle_timeout())
                    
                    if self.client_manager and self.client_manager.cleanup_inactive(idle_timeout):
                        logger.info(f"Channel {self.channel_id}: All clients inactive for {idle_timeout:.1f}s (unified HLS cleanup threshold)")
                        # Stop the channel via proxy server
                        proxy_server.stop_channel(self.channel_id)
                        break
                except Exception as e:
                    logger.error(f"Cleanup error for channel {self.channel_id}: {e}")
                
                # Check every few seconds
                time.sleep(5)
        
        if not self.cleanup_running:
            self.cleanup_running = True
            self.cleanup_thread = threading.Thread(
                target=cleanup_loop,
                name=f"HLS-Cleanup-{self.channel_id[:8]}",
                daemon=True
            )
            self.cleanup_thread.start()
            logger.info(f"Started cleanup monitoring for HLS channel {self.channel_id}")


class StreamFetcher:
    """Fetches HLS segments from the source URL using async HTTP"""
    
    def __init__(self, manager: StreamManager, buffer: StreamBuffer):
        self.manager = manager
        self.buffer = buffer
        # Use httpx AsyncClient for non-blocking HTTP requests with connection pooling
        self.client: Optional[httpx.AsyncClient] = None
        self.downloaded_segments: Set[str] = set()
    
    async def fetch_loop(self):
        """Main fetch loop for downloading segments (async version)"""
        retry_delay = 1
        max_retry_delay = 8
        
        # Create async HTTP client with connection pooling
        async with httpx.AsyncClient(
            timeout=10.0,
            headers={'User-Agent': HLSConfig.DEFAULT_USER_AGENT},
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        ) as client:
            self.client = client
            
            while self.manager.running:
                try:
                    playback_context = self.manager.get_playback_context()
                    playback_url = playback_context["playback_url"]
                    source_engine_id = playback_context["engine_container_id"]

                    # Fetch manifest (non-blocking)
                    response = await client.get(playback_url)
                    response.raise_for_status()
                    
                    manifest = m3u8.loads(response.text)
                    
                    # Update manifest info
                    if manifest.target_duration:
                        self.manager.target_duration = float(manifest.target_duration)
                    if manifest.version:
                        self.manager.manifest_version = manifest.version
                    
                    if not manifest.segments:
                        logger.warning(f"No segments in manifest for channel {self.manager.channel_id}")
                        await asyncio.sleep(retry_delay)
                        continue
                    
                    # Initial buffering - fetch multiple segments
                    if self.manager.initial_buffering:
                        await self._fetch_initial_segments(
                            manifest,
                            str(response.url),
                            source_engine_id=source_engine_id,
                        )
                        continue
                    
                    # Normal operation - fetch latest segment
                    await self._fetch_latest_segment(
                        manifest,
                        str(response.url),
                        source_engine_id=source_engine_id,
                    )
                    
                    # Wait before next manifest fetch (non-blocking)
                    await asyncio.sleep(self.manager.target_duration * HLSConfig.SEGMENT_FETCH_INTERVAL())
                    
                    # Reset retry delay on success
                    retry_delay = 1
                    
                except Exception as e:
                    # Only log if manager is still running (expected errors when stopping)
                    if self.manager.running:
                        logger.error(f"Fetch loop error for channel {self.manager.channel_id}: {e}")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
    
    async def _fetch_initial_segments(self, manifest: m3u8.M3U8, base_url: str, source_engine_id: str):
        """Fetch initial segments for buffering (async version)"""
        segments_to_fetch = []
        current_duration = 0.0
        
        # Start from the end of the manifest
        for segment in reversed(manifest.segments):
            current_duration += float(segment.duration)
            segments_to_fetch.append(segment)
            
            if (current_duration >= HLSConfig.INITIAL_BUFFER_SECONDS() or 
                len(segments_to_fetch) >= HLSConfig.MAX_INITIAL_SEGMENTS()):
                break
        
        # Reverse to chronological order
        segments_to_fetch.reverse()
        
        # Download segments (async)
        successful_downloads = 0
        for segment in segments_to_fetch:
            try:
                segment_url = urljoin(base_url, segment.uri)
                segment_data = await self._download_segment(segment_url)
                
                if segment_data and len(segment_data) > 0:
                    seq = self.manager.next_sequence
                    self.buffer[seq] = segment_data
                    duration = float(segment.duration)
                    self.manager.record_segment_metadata(seq, duration, source_engine_id=source_engine_id)
                    self.manager.buffered_duration += duration
                    self.manager.next_sequence += 1
                    successful_downloads += 1
                    self.downloaded_segments.add(segment.uri)
                    
                    # Rework bitrate calculation using per-segment size
                    self.manager.update_dynamic_bitrate(len(segment_data), duration)
                    
                    logger.debug(f"Buffered initial segment {seq} (duration: {duration}s)")
            except Exception as e:
                logger.error(f"Error downloading initial segment: {e}")
        
        # Mark buffer ready if we got some segments
        if successful_downloads > 0:
            self.manager.initial_buffering = False
            self.manager.buffer_ready.set()
            logger.info(f"Initial buffer ready with {successful_downloads} segments "
                       f"({self.manager.buffered_duration:.1f}s of content)")
    
    async def _fetch_latest_segment(self, manifest: m3u8.M3U8, base_url: str, source_engine_id: str):
        """Fetch the latest segment if not already downloaded (async version)"""
        latest_segment = manifest.segments[-1]
        
        if latest_segment.uri in self.downloaded_segments:
            return
        
        try:
            segment_url = urljoin(base_url, latest_segment.uri)
            segment_data = await self._download_segment(segment_url)
            
            if segment_data and len(segment_data) > 0:
                seq = self.manager.next_sequence
                self.buffer[seq] = segment_data
                duration = float(latest_segment.duration)
                self.manager.record_segment_metadata(seq, duration, source_engine_id=source_engine_id)
                self.manager.next_sequence += 1
                self.downloaded_segments.add(latest_segment.uri)
                
                # Rework bitrate calculation using per-segment size
                self.manager.update_dynamic_bitrate(len(segment_data), duration)
                
                logger.debug(f"Buffered segment {seq} (duration: {duration}s)")
        except Exception as e:
            logger.error(f"Error downloading latest segment: {e}")
    
    async def _download_segment(self, url: str) -> Optional[bytes]:
        """Download a single segment (async version with performance tracking)"""
        from ..services.performance_metrics import Timer, performance_metrics
        from ..services.metrics import observe_proxy_ingress_bytes
        
        # Check if manager is still running before downloading
        if not self.manager.running:
            logger.debug(f"Stream manager stopped, skipping segment download from {url}")
            return None
        
        with Timer(performance_metrics, 'hls_segment_fetch', {'channel_id': self.manager.channel_id[:16]}):
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                payload = response.content
                observe_proxy_ingress_bytes("HLS", len(payload))
                
                # Update per-stream ingress tracking for topology metrics
                self.manager.total_bytes_fetched += len(payload)
                
                return payload
            except Exception as e:
                # Only log if manager is still running (expected errors when stopping)
                if self.manager.running:
                    logger.error(f"Failed to download segment from {url}: {e}")
                return None


class HLSProxyServer:
    """FastAPI-compatible HLS Proxy Server"""
    
    _instance: Optional['HLSProxyServer'] = None
    
    @classmethod
    def get_instance(cls) -> 'HLSProxyServer':
        """Get singleton instance"""
        if cls._instance is None:
            cls._instance = HLSProxyServer()
        return cls._instance
    
    def __init__(self):
        self.stream_managers: Dict[str, StreamManager] = {}
        self.stream_buffers: Dict[str, StreamBuffer] = {}
        self.client_managers: Dict[str, ClientManager] = {}  # Track client activity per channel
        self.fetch_tasks: Dict[str, asyncio.Task] = {}  # Changed from threads to async tasks
        self.fetchers: Dict[str, StreamFetcher] = {}
        self.lock = threading.Lock()  # Lock for thread-safe operations
        
        # Store reference to the main event loop for thread-safe event scheduling
        # This will be set when initialize_channel is first called from an async context (FastAPI endpoint).
        # Cleanup threads use this reference to schedule async events via run_coroutine_threadsafe.
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop is running yet - will be set when initialize_channel is first called
            self._main_loop = None
        
        logger.info("HLS ProxyServer initialized")
    
    def has_channel(self, channel_id: str) -> bool:
        """Check if a channel already exists.
        
        Args:
            channel_id: The channel ID to check
            
        Returns:
            True if the channel exists, False otherwise
        """
        with self.lock:
            return channel_id in self.stream_managers
    
    def initialize_channel(self, channel_id: str, playback_url: str, engine_host: str, 
                          engine_port: int, engine_container_id: str, session_info: Dict[str, Any],
                          engine_api_port: Optional[int] = None,
                          api_key: Optional[str] = None, stream_key_type: str = "content_id",
                          file_indexes: str = "0", seekback: int = 0, bitrate: int = 0):
        """Initialize a new HLS channel.
        
        This method should only be called for new channels. Existing channels should be
        detected using has_channel() before calling this method.
        """
        with self.lock:
            # Safety check - this should not happen if caller uses has_channel() properly
            if channel_id in self.stream_managers:
                logger.warning(f"HLS channel {channel_id} already exists, skipping initialization")
                return
            
            logger.info(f"Initializing HLS channel {channel_id} with URL {playback_url} bitrate={bitrate} bps")
            
            # Ensure we have the main event loop reference
            # In normal FastAPI operation, initialize_channel is always called from an async endpoint,
            # so get_running_loop() will succeed. The _main_loop is stored for use by cleanup threads.
            if not self._main_loop:
                try:
                    self._main_loop = asyncio.get_running_loop()
                except RuntimeError:
                    # No loop is running - this shouldn't happen in production with FastAPI
                    # but may occur in tests. Event sending from threads will be skipped.
                    logger.warning("No running event loop when initializing HLS channel - thread-safe event sending will be disabled")
            
            # Create manager, buffer, and client manager
            manager = StreamManager(
                playback_url=playback_url,
                channel_id=channel_id,
                engine_host=engine_host,
                engine_port=engine_port,
                engine_container_id=engine_container_id,
                session_info=session_info,
                api_key=api_key,
                stream_key_type=stream_key_type,
                file_indexes=file_indexes,
                seekback=seekback,
                engine_api_port=engine_api_port,
                event_loop=self._main_loop,  # Pass event loop reference for thread-safe event sending
                bitrate=bitrate
            )
            buffer = StreamBuffer()
            client_manager = ClientManager(stream_id=channel_id)
            
            # Link client manager to stream manager
            manager.client_manager = client_manager
            
            self.stream_managers[channel_id] = manager
            self.stream_buffers[channel_id] = buffer
            self.client_managers[channel_id] = client_manager
            
            # Send stream started event to orchestrator
            manager._send_stream_started_event()
            
            # Start fetcher as async task (non-blocking, with connection pooling)
            fetcher = StreamFetcher(manager, buffer)
            self.fetchers[channel_id] = fetcher
            task: Optional[asyncio.Task] = None
            try:
                running_loop = asyncio.get_running_loop()
                task = running_loop.create_task(
                    fetcher.fetch_loop(),
                    name=f"HLS-Fetcher-{channel_id[:8]}"
                )
            except RuntimeError:
                if self._main_loop and self._main_loop.is_running():
                    try:
                        task = self._main_loop.create_task(
                            fetcher.fetch_loop(),
                            name=f"HLS-Fetcher-{channel_id[:8]}"
                        )
                    except Exception:
                        task = None

            if task is not None:
                self.fetch_tasks[channel_id] = task
            else:
                logger.warning(f"No running event loop; fetcher task not started for HLS channel {channel_id}")
            
            # Start cleanup monitoring
            manager.start_cleanup_monitoring(self)
            
            logger.info(f"HLS channel {channel_id} initialized bitrate={bitrate} bps")
    
    def record_client_activity(
        self,
        channel_id: str,
        client_ip: str,
        client_id: str = "",
        user_agent: str = "unknown",
        request_kind: str = "",
        bytes_sent: Optional[float] = None,
        chunks_sent: Optional[int] = None,
        sequence: Optional[int] = None,
        buffer_seconds_behind: Optional[float] = None,
        is_prebuffering: Optional[bool] = None,
    ):
        """Record client activity for a channel (called on each manifest/segment request)"""
        if channel_id in self.client_managers:
            manager = self.stream_managers.get(channel_id)
            effective_prebuffering = is_prebuffering
            if effective_prebuffering is None and manager:
                effective_prebuffering = getattr(manager, "initial_buffering", False)

            self.client_managers[channel_id].record_client_activity(
                client_ip=client_ip,
                client_id=client_id,
                user_agent=user_agent,
                request_kind=request_kind,
                bytes_sent=bytes_sent,
                chunks_sent=chunks_sent,
                sequence=sequence,
                buffer_seconds_behind=buffer_seconds_behind,
                is_prebuffering=effective_prebuffering,
                bitrate=getattr(manager, "bitrate", 0) if manager else 0,
            )

    def get_manifest_buffer_seconds_behind(self, channel_id: str) -> float:
        """Return latest manifest window lag estimate for a channel."""
        manager = self.stream_managers.get(channel_id)
        if not manager:
            return 0.0
        try:
            return max(0.0, float(getattr(manager, "last_manifest_buffer_seconds_behind", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def get_segment_buffer_seconds_behind(self, channel_id: str, sequence: Optional[int]) -> float:
        """Estimate per-client HLS runway from requested segment vs current head."""
        manager = self.stream_managers.get(channel_id)
        buffer = self.stream_buffers.get(channel_id)
        if not manager or not buffer:
            return self.get_manifest_buffer_seconds_behind(channel_id)

        if sequence is None:
            return self.get_manifest_buffer_seconds_behind(channel_id)

        try:
            requested_seq = int(sequence)
        except (TypeError, ValueError):
            return self.get_manifest_buffer_seconds_behind(channel_id)

        available = buffer.keys()
        if not available:
            return 0.0

        latest_seq = max(available)
        lag_segments = max(0, int(latest_seq) - requested_seq)
        return max(0.0, float(lag_segments) * float(manager.target_duration or 0.0))
    
    def stop_stream_by_key(self, channel_id: str):
        """
        Public method to stop an HLS stream by its channel ID (stream key).
        This is called when a stream ends in the orchestrator state to ensure
        HLS proxy sessions are cleaned up synchronously.
        
        :param channel_id: The AceStream content ID (infohash or content key)
        :type channel_id: str
        """
        if channel_id not in self.stream_managers:
            logger.debug(f"No active HLS channel for channel_id={channel_id}, nothing to clean up")
            return
        
        logger.info(f"Stopping HLS channel {channel_id} (called from state synchronization)")
        self.stop_channel(channel_id, reason="stream_ended_in_state")
    
    def stop_channel(self, channel_id: str, reason: str = "normal"):
        """Stop and cleanup a channel"""
        with self.lock:
            if channel_id not in self.stream_managers:
                logger.debug(f"HLS channel {channel_id} already stopped")
                return
            
            # Check if there are still active clients
            if channel_id in self.client_managers and self.client_managers[channel_id].has_clients():
                logger.info(f"Cancelling stop for HLS channel {channel_id} - clients still active")
                return
            
            logger.info(f"Stopping HLS channel {channel_id}")
            manager = self.stream_managers[channel_id]
            
            # Send stream ended event before stopping
            manager._send_stream_ended_event(reason=reason)
            
            # Stop the manager (sets running=False)
            manager.stop()
            
            # Cancel async fetch task (non-blocking)
            # The task will clean up when it sees manager.running=False
            if channel_id in self.fetch_tasks:
                task = self.fetch_tasks[channel_id]
                if not task.done():
                    task.cancel()
            
            # Cleanup
            del self.stream_managers[channel_id]
            del self.stream_buffers[channel_id]
            if channel_id in self.client_managers:
                del self.client_managers[channel_id]
            if channel_id in self.fetch_tasks:
                del self.fetch_tasks[channel_id]
            if channel_id in self.fetchers:
                del self.fetchers[channel_id]
            
            logger.info(f"HLS channel {channel_id} stopped and cleaned up")

    def migrate_stream(self, channel_id: str, new_engine) -> Dict[str, Any]:
        """Hot-swap an active HLS channel to a new backend engine."""
        with self.lock:
            manager = self.stream_managers.get(channel_id)
            if not manager:
                return {
                    "migrated": False,
                    "reason": "hls_channel_not_found",
                    "stream_type": "HLS",
                }

            old_container_id = str(manager.engine_container_id or "")
            target_container_id = str(getattr(new_engine, "container_id", "") or "")
            if not target_container_id:
                return {
                    "migrated": False,
                    "reason": "invalid_target_engine",
                    "stream_type": "HLS",
                    "old_container_id": old_container_id,
                }

            if old_container_id and old_container_id == target_container_id:
                return {
                    "migrated": False,
                    "reason": "already_on_target_engine",
                    "stream_type": "HLS",
                    "old_container_id": old_container_id,
                    "new_container_id": target_container_id,
                }

            swap_result = manager.hot_swap_engine(
                new_host=str(getattr(new_engine, "host", "") or ""),
                new_port=int(getattr(new_engine, "port", 0) or 0),
                new_api_port=int(getattr(new_engine, "api_port", 62062) or 62062),
                new_container_id=target_container_id,
            )

            fetcher = self.fetchers.get(channel_id)
            if fetcher:
                fetcher.downloaded_segments.clear()

            return {
                "migrated": bool(swap_result.get("swapped", False)),
                "reason": str(swap_result.get("reason") or ""),
                "stream_type": "HLS",
                "old_container_id": old_container_id,
                "new_container_id": target_container_id,
                "session_updates": {
                    "playback_session_id": swap_result.get("playback_session_id"),
                    "stat_url": swap_result.get("stat_url"),
                    "command_url": swap_result.get("command_url"),
                    "is_live": swap_result.get("is_live"),
                },
            }
    
    def get_manifest(self, channel_id: str) -> str:
        """Generate HLS manifest for a channel (synchronous version for backward compatibility)
        
        DEPRECATED: Use get_manifest_async() instead. This synchronous version
        blocks the calling thread and should only be used for compatibility.
        """
        if channel_id not in self.stream_managers:
            raise ValueError(f"Channel {channel_id} not found")
        
        manager = self.stream_managers[channel_id]
        buffer = self.stream_buffers[channel_id]
        
        # Wait for initial buffer
        if not manager.buffer_ready.wait(HLSConfig.BUFFER_READY_TIMEOUT()):
            raise TimeoutError("Timeout waiting for initial buffer")
        
        # Wait for first segment
        start_time = time.time()
        while True:
            available = buffer.keys()
            if available:
                break
            
            if time.time() - start_time > HLSConfig.FIRST_SEGMENT_TIMEOUT():
                raise TimeoutError("Timeout waiting for first segment")
            
            time.sleep(0.1)
        
        # Build manifest
        return self._build_manifest(channel_id, manager, buffer)
    
    async def get_manifest_async(self, channel_id: str) -> str:
        """Generate HLS manifest for a channel (async version - non-blocking)
        
        This async version uses asyncio.sleep() instead of time.sleep() to avoid
        blocking the event loop during waits.
        """
        from ..services.performance_metrics import Timer, performance_metrics
        
        with Timer(performance_metrics, 'hls_manifest_generation', {'channel_id': channel_id[:16]}):
            if channel_id not in self.stream_managers:
                raise ValueError(f"Channel {channel_id} not found")
            
            manager = self.stream_managers[channel_id]
            buffer = self.stream_buffers[channel_id]
            
            # Wait for initial buffer (non-blocking)
            timeout = HLSConfig.BUFFER_READY_TIMEOUT()
            start_wait = time.time()
            while not manager.buffer_ready.is_set():
                if time.time() - start_wait > timeout:
                    raise TimeoutError("Timeout waiting for initial buffer")
                await asyncio.sleep(0.05)  # Non-blocking wait
            
            # Wait for first segment (non-blocking)
            start_time = time.time()
            while True:
                available = buffer.keys()
                if available:
                    break
                
                if time.time() - start_time > HLSConfig.FIRST_SEGMENT_TIMEOUT():
                    raise TimeoutError("Timeout waiting for first segment")
                
            # Build manifest
            return self._build_manifest(channel_id, manager, buffer)

    async def get_manifest_stream(self, channel_id: str):
        """Streaming async generator for HLS manifest with keep-alive comments."""
        if channel_id not in self.stream_managers:
            raise ValueError(f"Channel {channel_id} not found")
        
        manager = self.stream_managers[channel_id]
        buffer = self.stream_buffers[channel_id]
        
        # 1. Immediate Header
        # Player MUST see #EXTM3U first.
        yield b"#EXTM3U\n"
        yield b"# ACESTREAM HLS PREBUFFER KEEPALIVE\n"
        
        # 3. Prebuffer Hold (Hoarding Rescue)
        target_prebuffer = ConfigHelper.initial_buffer_seconds()
        if target_prebuffer > 0 and manager.is_hoarding:
            logger.info(f"[HLS:{channel_id}] Parking client at manifest level for {target_prebuffer}s prebuffer")
            start_wait = time.time()
            last_padding = start_wait
            timeout = max(15.0, float(target_prebuffer) + 30.0)
            
            while True:
                # Refresh buffer state
                buffer = manager.get_buffer()
                available = sorted(buffer.keys())
                
                # Buffer Ceiling Logic:
                # If target_prebuffer=30 but the manifest window only allows 15s (5 segments * 3s),
                # we must release as soon as the manifest is 'full' to avoid infinite parking.
                manifest_is_full = len(available) >= HLSConfig.WINDOW_SIZE()

                # Check for hoarding exit or ceiling
                if not manager.is_hoarding or manifest_is_full:
                   if manifest_is_full and manager.is_hoarding:
                       logger.info(f"[HLS:{channel_id}] Reached manifest ceiling (%d segments) before target (%ds). Releasing.", len(available), target_prebuffer)
                   break
                   
                now = time.time()
                if now - start_wait > timeout:
                    logger.warning(f"[HLS:{channel_id}] Prebuffer hold timed out at manifest level")
                    break
                    
                if now - last_padding >= 0.5:
                    # HLS-compliant comment padding
                    yield b"# ACESTREAM HLS PREBUFFER KEEPALIVE\n"
                    last_padding = now
                    
                await asyncio.sleep(0.1)

        # 4. Wait only for the first segment to exist (safety check)
        start_wait = time.time()
        timeout = 10.0
        while True:
            buffer = manager.get_buffer()
            available = list(buffer.keys())
            if available:
                break
            if time.time() - start_wait > timeout:
                yield b"# ERROR: Timeout waiting for first segment\n"
                return
            await asyncio.sleep(0.2)

        # 5. Final Manifest (body only)
        manifest_content = self._build_manifest(channel_id, manager, buffer)
        # Strip duplicate header if present
        if manifest_content.startswith("#EXTM3U\n"):
            manifest_content = manifest_content[len("#EXTM3U\n"):]
        elif manifest_content.startswith("#EXTM3U\r\n"):
            manifest_content = manifest_content[len("#EXTM3U\r\n"):]
            
        yield manifest_content.encode("utf-8")
    
    def _build_manifest(self, channel_id: str, manager: 'StreamManager', buffer: 'StreamBuffer') -> str:
        """Build manifest from buffer state (fast, non-blocking operation)"""
        # Build manifest
        available = sorted(buffer.keys())
        max_seq = max(available)
        
        if len(available) <= HLSConfig.INITIAL_SEGMENTS():
            min_seq = min(available)
        else:
            min_seq = max(min(available), max_seq - HLSConfig.WINDOW_SIZE() + 1)

        manager.last_manifest_buffer_seconds_behind = max(
            0.0,
            float(max_seq - min_seq) * float(manager.target_duration or 0.0),
        )
        
        # Generate manifest lines
        manifest_lines = [
            '#EXTM3U',
            f'#EXT-X-VERSION:{manager.manifest_version}',
            f'#EXT-X-MEDIA-SEQUENCE:{min_seq}',
            f'#EXT-X-TARGETDURATION:{int(manager.target_duration)}',
        ]
        
        # Add segments within window
        window_segments = [s for s in available if min_seq <= s <= max_seq]
        previous_source_engine = ""
        for seq in window_segments:
            current_source_engine = str(manager.segment_sources.get(seq) or "")
            if previous_source_engine and current_source_engine and current_source_engine != previous_source_engine:
                manifest_lines.append('#EXT-X-DISCONTINUITY')

            duration = manager.segment_durations.get(seq, 10.0)
            manifest_lines.append(f'#EXTINF:{duration},')
            manifest_lines.append(f'/ace/hls/{channel_id}/segment/{seq}.ts')

            if current_source_engine:
                previous_source_engine = current_source_engine
        
        manifest_content = '\n'.join(manifest_lines)
        logger.debug(f"Generated manifest for channel {channel_id} with segments {min_seq}-{max_seq}")
        
        return manifest_content
    
    async def get_segment_stream(self, channel_id: str, segment_name: str):
        """Streaming async generator for HLS segments with Headers-First prebuffer hold."""
        if channel_id not in self.stream_buffers:
            raise ValueError(f"Channel {channel_id} not found")
        
        try:
            segment_id = int(segment_name.split('.')[0])
        except ValueError:
            raise ValueError(f"Invalid segment name: {segment_name}")
        
        manager = self.stream_managers[channel_id]
        buffer = self.stream_buffers[channel_id]
        
        # Deliver the full segment immediately. Hold happens at manifest level now.
        segment_data = buffer.get(segment_id)
        if segment_data is None:
            raise ValueError(f"Segment {segment_id} not found in buffer")
        yield segment_data
