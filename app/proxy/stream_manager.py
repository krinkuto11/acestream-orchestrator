"""
Stream Manager for AceStream connections.
Simplified adaptation from ts_proxy - focuses on AceStream engine API integration.
Sends stream start/stop events to orchestrator for panel visibility.
"""

import threading
import logging
import time
import requests
import os
import uuid
from typing import Optional, Any, Dict
from urllib.parse import unquote, urlparse, urlunparse

from ..core.config import cfg
from .http_streamer import HTTPStreamReader
from .stream_buffer import StreamBuffer
from .client_manager import ClientManager
from .redis_keys import RedisKeys
from .constants import (
    StreamState,
    EventType,
    StreamMetadataField,
    VLC_USER_AGENT,
    PROXY_MODE_API,
    PROXY_MODE_HTTP,
    normalize_proxy_mode,
)
from .config_helper import ConfigHelper, Config
from .ace_api_client import AceLegacyApiClient, AceLegacyApiError
from .utils import get_logger
from ..services.engine_selection import select_best_engine

logger = get_logger()

# Timeout for stream event handlers (in seconds)
# This prevents blocking if internal event handling is slow (e.g., Docker API calls)
STREAM_EVENT_HANDLER_TIMEOUT = 2.0


class StreamManager:
    """Manages connection to AceStream engine and stream health"""
    
    def __init__(
        self,
        content_id,
        engine_host,
        engine_port,
        engine_container_id,
        buffer,
        client_manager,
        engine_api_port=None,
        worker_id=None,
        api_key=None,
        existing_session=None,
        source_input=None,
        source_input_type="content_id",
        file_indexes="0",
        seekback=0,
        playback_url: Optional[str] = None,
        playback_session_id: Optional[str] = None,
        stat_url: Optional[str] = None,
        command_url: Optional[str] = None,
        is_live: Optional[int] = None,
        ace_api_client: Optional[Any] = None,
    ):
        # Basic properties
        self.content_id = content_id
        self.engine_host = engine_host
        self.engine_port = engine_port
        self.engine_api_port = engine_api_port or 62062
        self.engine_container_id = engine_container_id  # Added for events
        self.buffer = buffer
        self.client_manager = client_manager
        self.worker_id = worker_id
        self.api_key = api_key  # API key for orchestrator events
        
        # Stream session info (from AceStream API)
        self.playback_url = playback_url
        self.playback_session_id = playback_session_id
        self.stat_url = stat_url or ""
        self.command_url = command_url or ""
        self.is_live = is_live if is_live is not None else 1
        self.control_mode = normalize_proxy_mode(ConfigHelper.control_mode(), default=PROXY_MODE_HTTP)
        self.ace_api_client = ace_api_client
        self._legacy_api_lock = threading.Lock()
        self.resolved_infohash = None
        self.legacy_status_probe = None
        self._legacy_probe_cache = None
        self._legacy_probe_cache_ts = 0.0
        self._last_request_failure_type = None
        self.existing_session = existing_session or {}
        self.owns_engine_session = True

        normalized_input_type = str(source_input_type or "content_id").strip().lower()
        allowed_input_types = {"content_id", "infohash", "torrent_url", "direct_url", "raw_data"}
        if normalized_input_type not in allowed_input_types:
            normalized_input_type = "content_id"

        self.source_input_type = normalized_input_type
        self.source_input = str(source_input if source_input is not None else content_id)
        normalized_file_indexes = str(file_indexes if file_indexes is not None else "0").strip()
        self.file_indexes = normalized_file_indexes or "0"
        effective_seekback = cfg.ACE_LIVE_EDGE_DELAY if seekback is None else seekback
        try:
            normalized_seekback = int(float(effective_seekback))
        except (TypeError, ValueError):
            normalized_seekback = 0
        self.seekback = max(0, normalized_seekback)
        # Keep probe cadence aligned with collector interval so legacy mode
        # has comparable overhead to stat_url polling mode.
        try:
            self._legacy_probe_cache_ttl_s = max(0.5, float(os.getenv("COLLECT_INTERVAL_S", "1")))
        except Exception:
            self._legacy_probe_cache_ttl_s = 1.0
        
        # Connection state
        self.running = True
        self.connected = False
        self.healthy = True
        self.retry_count = 0
        self.max_retries = ConfigHelper.max_retries()
        
        # HTTP stream reader
        self.http_reader = None
        self.socket = None  # Read end of pipe from http_reader
        self._reader_lock = threading.RLock()
        self._pending_seek_start_info = None
        self._pending_engine_swap_info = None
        
        # Health monitoring
        self.last_data_time = time.time()
        self.health_check_interval = 5
        
        # Orchestrator event tracking
        self.stream_id = None  # Will be set after sending start event
        self._ended_event_sent = False  # Track if we've already sent the ended event
        self._ended_event_stream_id = None
        self._stream_exit_reason = None
        
        logger.info(f"StreamManager initialized for content_id={content_id}")

    def _cleanup_failed_engine_after_transition(self, previous_engine_container_id: Optional[str]) -> None:
        """Best-effort cleanup for a failed source engine after a successful transition."""
        previous_id = str(previous_engine_container_id or "").strip()
        if not previous_id:
            return
        if previous_id == str(self.engine_container_id or "").strip():
            return

        try:
            from ..services.state import state

            active_streams = state.list_streams(status="started", container_id=previous_id)
            if active_streams:
                logger.debug(
                    "Skipping cleanup for previous engine %s; %s active stream(s) still attached",
                    previous_id[:12],
                    len(active_streams),
                )
                return

            old_engine = state.get_engine(previous_id)
            if not old_engine:
                return

            old_vpn = str(old_engine.vpn_container or "").strip()
            vpn_failed_or_draining = False
            if old_vpn:
                vpn_failed_or_draining = state.is_vpn_node_draining(old_vpn)
                if not vpn_failed_or_draining:
                    for node in state.list_vpn_nodes():
                        node_name = str(node.get("container_name") or "").strip()
                        if node_name != old_vpn:
                            continue

                        status = str(node.get("status") or "").strip().lower()
                        condition = str(node.get("condition") or "").strip().lower()
                        healthy = bool(node.get("healthy", True))
                        if (not healthy) or condition == "notready" or status in {"dead", "exited", "down"}:
                            vpn_failed_or_draining = True
                        break

            engine_unhealthy = str(old_engine.health_status or "").strip().lower() == "unhealthy"
            if not vpn_failed_or_draining and not engine_unhealthy:
                logger.debug(
                    "Skipping cleanup for previous engine %s; not marked failed/draining",
                    previous_id[:12],
                )
                return

            from ..services.provisioner import stop_container

            stop_container(previous_id, force=True)
            state.remove_engine(previous_id)

            logger.info(
                "Cleaned up failed source engine after transition: old=%s new=%s",
                previous_id[:12],
                str(self.engine_container_id or "unknown")[:12],
            )
        except Exception as e:
            logger.warning(
                "Failed to cleanup previous engine %s after transition: %s",
                previous_id[:12],
                e,
            )

    def _failover_to_new_engine(self):
        """Dynamically fetch a new healthy engine during a retry/failover."""
        try:
            previous_engine_container_id = str(self.engine_container_id or "").strip()
            penalties = {self.engine_container_id: 999} if self.engine_container_id else None
            new_engine, _ = select_best_engine(additional_load_by_engine=penalties)

            self.engine_host = new_engine.host
            self.engine_port = int(new_engine.port)
            self.engine_api_port = int(new_engine.api_port or 62062)
            self.engine_container_id = new_engine.container_id

            # Force a brand-new session request on the selected engine.
            self.playback_url = None
            self.playback_session_id = None
            self.stat_url = ""
            self.command_url = ""
            self.existing_session = {}
            self.ace_api_client = None
            self.legacy_status_probe = None
            self._legacy_probe_cache = None
            self._legacy_probe_cache_ts = 0.0
            self.owns_engine_session = True

            logger.info(
                "Failover successful: swapped target to engine %s (%s:%s)",
                str(self.engine_container_id or "unknown")[:12],
                self.engine_host,
                self.engine_port,
            )
            self._cleanup_failed_engine_after_transition(previous_engine_container_id)
            return True
        except Exception as e:
            logger.error(f"Failover engine selection failed: {e}")
            return False

    def _build_legacy_http_params(self):
        """Build engine query params for HTTP mode based on source input type."""
        params = {
            "format": "json",
            "pid": str(uuid.uuid4()),
            "file_indexes": self.file_indexes,
        }

        if self.seekback > 0:
            params["seekback"] = str(self.seekback)

        if self.source_input_type in {"content_id", "infohash"}:
            params["id"] = self.source_input
            if self.source_input_type == "infohash":
                params["infohash"] = self.source_input
        elif self.source_input_type == "torrent_url":
            params["torrent_url"] = self.source_input
        elif self.source_input_type == "direct_url":
            # Keep "url" as compatibility alias for engines expecting this key.
            params["direct_url"] = self.source_input
            params["url"] = self.source_input
        elif self.source_input_type == "raw_data":
            params["raw_data"] = self.source_input
        else:
            params["id"] = self.source_input

        return params

    def _apply_existing_session(self):
        """Use a pre-existing monitoring session instead of starting a new engine session."""
        session = self.existing_session.get("session") or {}
        if not session:
            return False

        playback_url = (session.get("playback_url") or "").strip()
        if not playback_url:
            return False

        self.playback_url = self._normalize_playback_url(playback_url)
        self.playback_session_id = (
            session.get("playback_session_id")
            or self.playback_session_id
            or f"reuse-{self.content_id[:16]}-{int(time.time())}"
        )
        self.stat_url = session.get("stat_url") or ""
        self.command_url = session.get("command_url") or ""
        self.is_live = int(session.get("is_live", 1) or 1)
        latest_status = self.existing_session.get("latest_status") or {}
        if latest_status:
            self.legacy_status_probe = latest_status
        self.owns_engine_session = False
        logger.info(
            "Using existing monitored session for content_id=%s monitor_id=%s",
            self.content_id,
            self.existing_session.get("monitor_id"),
        )
        return True
    
    def request_stream_from_engine(self):
        """Request stream from AceStream engine according to selected control mode."""
        self._last_request_failure_type = None

        # If we already have a playback_url, we don't need to request it again
        if self.playback_url:
            logger.info(f"Adopting pre-initialized stream session for content_id={self.content_id}")
            self.connected = False  # Reader will set this to True
            return True

        if self._is_api_mode():
            return self._request_stream_legacy_api()
        return self._request_stream_legacy_http()

    def _is_api_mode(self) -> bool:
        self.control_mode = normalize_proxy_mode(self.control_mode, default=PROXY_MODE_HTTP)
        return self.control_mode == PROXY_MODE_API

    def _request_stream_legacy_http(self):
        """Current JSON-over-HTTP control flow (HTTP mode)."""
        # Check stream mode to determine which endpoint to use
        stream_mode = Config.STREAM_MODE
        
        if stream_mode == 'HLS':
            # HLS mode - use manifest.m3u8 endpoint
            url = f"http://{self.engine_host}:{self.engine_port}/ace/manifest.m3u8"
        else:
            # TS mode (default) - use getstream endpoint
            url = f"http://{self.engine_host}:{self.engine_port}/ace/getstream"
        
        params = self._build_legacy_http_params()

        # Build full URL for logging (define early to avoid NameError in exception handlers)
        full_url = requests.Request("GET", url, params=params).prepare().url or url
        
        try:
            logger.info(f"Requesting stream from AceStream engine in {stream_mode} mode: {url}")
            logger.debug(f"Full request URL: {full_url}")
            logger.debug(
                f"Engine: {self.engine_host}:{self.engine_port}, Stream key: {self.content_id}, "
                f"Input type: {self.source_input_type}, Container: {self.engine_container_id}"
            )
            logger.debug(f"Generated PID: {params.get('pid')}")
            
            response = requests.get(url, params=params, timeout=10)
            
            # Log response details in debug mode
            logger.debug(f"AceStream response status: {response.status_code}")
            logger.debug(f"AceStream response headers: {dict(response.headers)}")
            
            response.raise_for_status()
            
            data = response.json()
            
            # Log full response in debug mode
            logger.debug(f"AceStream response body: {data}")
            
            if data.get("error"):
                error_msg = data['error']
                logger.error(f"AceStream engine returned error: {error_msg}")
                logger.error(
                    f"Error details - Engine: {self.engine_host}:{self.engine_port}, "
                    f"Stream key: {self.content_id}, Input type: {self.source_input_type}, "
                    f"Container: {self.engine_container_id}"
                )
                logger.debug(f"Full error response: {data}")
                raise RuntimeError(f"AceStream engine returned error: {error_msg}")
            
            resp_data = data.get("response", {})
            self.playback_url = resp_data.get("playback_url")
            self.stat_url = resp_data.get("stat_url")
            self.command_url = resp_data.get("command_url")
            self.playback_session_id = resp_data.get("playback_session_id")
            self.is_live = resp_data.get("is_live", 1)
            
            if not self.playback_url:
                logger.error("No playback_url in AceStream response")
                logger.error(
                    f"Error details - Engine: {self.engine_host}:{self.engine_port}, "
                    f"Stream key: {self.content_id}, Input type: {self.source_input_type}"
                )
                logger.debug(f"Response data: {resp_data}")
                raise RuntimeError("No playback_url in AceStream response")
            
            logger.info(f"AceStream session started: playback_session_id={self.playback_session_id}")
            logger.info(f"Playback URL: {self.playback_url}")
            logger.debug(f"Stat URL: {self.stat_url}")
            logger.debug(f"Command URL: {self.command_url}")
            logger.debug(f"Is Live: {self.is_live}")
            
            return True
            
        except Exception as e:
            self._last_request_failure_type = "request_failed"
            # Log detailed error information for both request and general exceptions
            logger.error(f"Failed to request stream from AceStream engine: {e}")
            logger.error(
                f"Request details - URL: {full_url}, Engine: {self.engine_host}:{self.engine_port}, "
                f"Stream key: {self.content_id}, Input type: {self.source_input_type}"
            )
            logger.debug(f"Exception details: {e}", exc_info=True)
            return False

    def _request_stream_legacy_api(self):
        """Telnet-style AceStream API control flow (API mode)."""
        client = None
        try:
            logger.info(
                f"Requesting stream from AceStream legacy API: {self.engine_host}:{self.engine_api_port}"
            )
            client = AceLegacyApiClient(
                host=self.engine_host,
                port=self.engine_api_port,
                connect_timeout=10,
                response_timeout=10,
            )
            client.connect()
            client.authenticate()

            loadresp, resolved_mode = client.resolve_content(
                self.source_input,
                session_id="0",
                mode=self.source_input_type,
            )
            
            status_code = loadresp.get("status")
            if status_code not in (1, 2) and resolved_mode != "direct_url":
                message = loadresp.get("message") or "content unavailable"
                raise AceLegacyApiError(f"LOADASYNC status={status_code}: {message}")
            
            self.resolved_infohash = loadresp.get("infohash") or None
            
            # If we have an infohash, always prefer it for START to skip engine-side resolution
            if self.resolved_infohash:
                start_mode = "infohash"
                start_payload = self.resolved_infohash
            else:
                start_mode = resolved_mode
                start_payload = self.source_input

            start_info = client.start_stream(
                start_payload,
                mode=start_mode,
                file_indexes=self.file_indexes,
                seekback=self.seekback,
            )
            self.legacy_status_probe = client.collect_status_samples(samples=1, interval_s=0.0, per_sample_timeout_s=1.0)

            playback_url = start_info.get("url")
            if not playback_url:
                raise AceLegacyApiError("Legacy API START did not return playback URL")

            self.playback_url = self._normalize_playback_url(playback_url)
            self.stat_url = ""
            self.command_url = ""
            self.playback_session_id = start_info.get("playback_session_id", f"legacy-{int(time.time())}")
            self.is_live = int(start_info.get("stream", 1) or 1)
            self.ace_api_client = client

            logger.info(f"AceStream legacy API session started: playback_session_id={self.playback_session_id}")
            logger.info(f"Playback URL: {self.playback_url}")
            return True
        except Exception as e:
            if not self._last_request_failure_type:
                self._last_request_failure_type = "request_failed"
            logger.error(f"Failed to request stream from AceStream legacy API: {e}")
            logger.error(
                f"Request details - API: {self.engine_host}:{self.engine_api_port}, "
                f"Stream key: {self.content_id}, Input type: {self.source_input_type}"
            )
            logger.debug(f"Exception details: {e}", exc_info=True)
            try:
                if self.ace_api_client:
                    self.ace_api_client.shutdown()
                elif client:
                    client.shutdown()
            except Exception:
                pass
            self.ace_api_client = None
            return False

    def _request_stream_session_http_for_engine(self, engine_host: str, engine_port: int) -> Dict[str, Any]:
        """Request a new HTTP-mode AceStream session from a specific engine."""
        stream_mode = Config.STREAM_MODE
        if stream_mode == 'HLS':
            url = f"http://{engine_host}:{engine_port}/ace/manifest.m3u8"
        else:
            url = f"http://{engine_host}:{engine_port}/ace/getstream"

        params = self._build_legacy_http_params()
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        if data.get("error"):
            raise RuntimeError(f"AceStream engine returned error: {data.get('error')}")

        resp_data = data.get("response", {})
        playback_url = resp_data.get("playback_url")
        if not playback_url:
            raise RuntimeError("No playback_url in AceStream response")

        return {
            "playback_url": self._normalize_playback_url(str(playback_url), engine_host=engine_host),
            "playback_session_id": resp_data.get("playback_session_id"),
            "stat_url": resp_data.get("stat_url") or "",
            "command_url": resp_data.get("command_url") or "",
            "is_live": int(resp_data.get("is_live", 1) or 1),
            "resolved_infohash": self.resolved_infohash,
            "ace_api_client": None,
        }

    def _request_stream_session_api_for_engine(self, engine_host: str, engine_api_port: int) -> Dict[str, Any]:
        """Request a new API-mode AceStream session from a specific engine."""
        client = AceLegacyApiClient(
            host=engine_host,
            port=engine_api_port,
            connect_timeout=10,
            response_timeout=10,
        )
        client.connect()
        client.authenticate()

        try:
            loadresp, resolved_mode = client.resolve_content(
                self.source_input,
                session_id="0",
                mode=self.source_input_type,
            )

            status_code = loadresp.get("status")
            if status_code not in (1, 2) and resolved_mode != "direct_url":
                message = loadresp.get("message") or "content unavailable"
                raise AceLegacyApiError(f"LOADASYNC status={status_code}: {message}")

            resolved_infohash = loadresp.get("infohash") or None
            if resolved_infohash:
                start_mode = "infohash"
                start_payload = resolved_infohash
            else:
                start_mode = resolved_mode
                start_payload = self.source_input

            start_info = client.start_stream(
                start_payload,
                mode=start_mode,
                file_indexes=self.file_indexes,
                seekback=self.seekback,
            )

            playback_url = start_info.get("url")
            if not playback_url:
                raise AceLegacyApiError("Legacy API START did not return playback URL")

            return {
                "playback_url": self._normalize_playback_url(str(playback_url), engine_host=engine_host),
                "playback_session_id": start_info.get("playback_session_id", f"legacy-{int(time.time())}"),
                "stat_url": "",
                "command_url": "",
                "is_live": int(start_info.get("stream", 1) or 1),
                "resolved_infohash": resolved_infohash,
                "ace_api_client": client,
            }
        except Exception:
            try:
                client.shutdown()
            except Exception:
                pass
            raise

    def hot_swap_engine(self, new_host: str, new_port: int, new_api_port: int, new_container_id: str) -> Dict[str, Any]:
        """Queue a hot engine swap while preserving the same proxy buffer and stream identity."""
        if not self.running:
            raise RuntimeError("stream_manager_not_running")

        if not new_host or not new_container_id:
            raise RuntimeError("invalid_swap_target")

        if new_container_id == self.engine_container_id:
            return {
                "swapped": False,
                "reason": "already_on_target_engine",
                "old_container_id": self.engine_container_id,
                "new_container_id": new_container_id,
            }

        if self._is_api_mode():
            session = self._request_stream_session_api_for_engine(new_host, int(new_api_port or 62062))
        else:
            session = self._request_stream_session_http_for_engine(new_host, int(new_port))

        pending_payload = {
            "engine_host": str(new_host),
            "engine_port": int(new_port),
            "engine_api_port": int(new_api_port or 62062),
            "engine_container_id": str(new_container_id),
            "playback_url": str(session.get("playback_url") or "").strip(),
            "playback_session_id": session.get("playback_session_id"),
            "stat_url": str(session.get("stat_url") or ""),
            "command_url": str(session.get("command_url") or ""),
            "is_live": int(session.get("is_live", 1) or 1),
            "resolved_infohash": session.get("resolved_infohash"),
            "ace_api_client": session.get("ace_api_client"),
        }

        if not pending_payload["playback_url"]:
            raise RuntimeError("swap_session_has_no_playback_url")

        with self._reader_lock:
            self._pending_engine_swap_info = pending_payload

        logger.info(
            "Queued TS hot swap for content_id=%s old_engine=%s new_engine=%s",
            self.content_id,
            self.engine_container_id,
            new_container_id,
        )

        return {
            "swapped": True,
            "old_container_id": self.engine_container_id,
            "new_container_id": str(new_container_id),
            "playback_session_id": pending_payload.get("playback_session_id"),
            "stat_url": pending_payload.get("stat_url"),
            "command_url": pending_payload.get("command_url"),
            "is_live": pending_payload.get("is_live"),
        }

    def _normalize_playback_url(self, url: str, engine_host: Optional[str] = None) -> str:
        """Rewrite localhost playback URLs so proxy can always reach the selected engine."""
        target_engine_host = str(engine_host or self.engine_host)
        try:
            normalized_url = (url or "").strip()

            # Legacy API START can return percent-encoded URL strings such as
            # http%3A//172.19.0.2%3A19000/content/.... Decode before parsing.
            if "%" in normalized_url:
                decoded_candidate = unquote(normalized_url)
                if decoded_candidate.startswith(("http://", "https://")):
                    normalized_url = decoded_candidate

            parsed = urlparse(normalized_url)
            if parsed.hostname in {"127.0.0.1", "localhost"}:
                netloc = f"{target_engine_host}:{parsed.port}" if parsed.port else target_engine_host
                return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
            if parsed.scheme and parsed.netloc:
                return normalized_url
        except Exception:
            pass
        return url
    
    def _send_stream_started_event(self):
        """Send stream started event to orchestrator in background (non-blocking)
        
        Runs in a background daemon thread to avoid blocking stream initialization.
        The stream_id is set to a temporary value immediately so the proxy can proceed.
        """
        def _send_event():
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
                        key_type=self.source_input_type,
                        key=self.content_id,
                        file_indexes=self.file_indexes,
                        seekback=self.seekback,
                        live_delay=self.seekback,
                        control_mode=normalize_proxy_mode(self.control_mode, default=PROXY_MODE_HTTP),
                    ),
                    session=SessionInfo(
                        playback_session_id=self.playback_session_id or f"fallback-{self.content_id[:16]}-{int(time.time())}",
                        stat_url=self.stat_url,
                        command_url=self.command_url,
                        is_live=self.is_live
                    ),
                    labels={
                        "source": "proxy",
                        "worker_id": self.worker_id or "unknown",
                        "proxy.control_mode": self.control_mode,
                        "stream.input_type": self.source_input_type,
                        "stream.file_indexes": self.file_indexes,
                        "stream.seekback": str(self.seekback),
                        "stream.live_delay": str(self.seekback),
                        "stream.resolved_infohash": str(self.resolved_infohash or ""),
                        "host.api_port": str(self.engine_api_port or "")
                    }
                )

                if self.legacy_status_probe:
                    status_text = self.legacy_status_probe.get("status_text")
                    peers = self.legacy_status_probe.get("peers")
                    http_peers = self.legacy_status_probe.get("http_peers")
                    progress = self.legacy_status_probe.get("progress")
                    if status_text is not None:
                        event.labels["stream.status_text"] = str(status_text)
                    if peers is not None:
                        event.labels["stream.peers"] = str(peers)
                    if http_peers is not None:
                        event.labels["stream.http_peers"] = str(http_peers)
                    if progress is not None:
                        event.labels["stream.progress"] = str(progress)
                
                # Call internal handler directly (no HTTP request)
                try:
                    result = handle_stream_started(event)
                    if result:
                        self.stream_id = result.id
                        logger.info(f"Sent stream started event to orchestrator: stream_id={self.stream_id}")
                    else:
                        logger.warning(f"Stream started event handler returned no result")
                        self.stream_id = f"temp-ts-{self.content_id[:16]}-{int(time.time())}"
                except Exception as e:
                    logger.warning(f"Event handler error: {e}")
                    self.stream_id = f"error-ts-{self.content_id[:16]}-{int(time.time())}"
                
            except Exception as e:
                logger.warning(f"Failed to send stream started event to orchestrator: {e}")
                logger.debug(f"Exception details: {e}", exc_info=True)
                # Generate a fallback stream_id
                self.stream_id = f"fallback-ts-{self.content_id[:16]}-{int(time.time())}"
        
        # Generate a temporary stream_id immediately so proxy can proceed
        self.stream_id = f"temp-ts-{self.content_id[:16]}-{int(time.time())}"
        self._ended_event_sent = False
        self._ended_event_stream_id = None
        
        # Send event in background thread (non-blocking, no join)
        handler_thread = threading.Thread(
            target=_send_event,
            name=f"TS-StartEvent-{self.content_id[:8]}",
            daemon=True
        )
        handler_thread.start()
        # No join() - fire and forget for non-blocking behavior
    
    def _send_stream_ended_event(self, reason="normal"):
        """Send stream ended event to orchestrator using internal handler (no HTTP)"""
        # Check if we've already sent the ended event
        if self._ended_event_sent and self._ended_event_stream_id == self.stream_id:
            logger.debug(f"Stream ended event already sent for stream_id={self.stream_id}, skipping")
            return
        
        # Check if we have a stream_id to send
        if not self.stream_id:
            logger.warning(f"No stream_id available for content_id={self.content_id}, cannot send ended event")
            return
        
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
            handle_stream_ended(event)
            
            # Mark as sent
            self._ended_event_sent = True
            self._ended_event_stream_id = self.stream_id
            
            logger.info(f"Sent stream ended event to orchestrator: stream_id={self.stream_id}, reason={reason}")
            
        except Exception as e:
            logger.warning(f"Failed to send stream ended event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
    
    def start_stream(self):
        """Start streaming from AceStream engine"""
        try:
            logger.debug(f"Starting HTTP stream reader for playback URL: {self.playback_url}")
            logger.debug(f"Chunk size: {ConfigHelper.chunk_size()}")
            
            # Create HTTP stream reader with VLC user agent for better compatibility
            # Some AceStream engines may behave differently based on the user agent
            with self._reader_lock:
                self.http_reader = HTTPStreamReader(
                    url=self.playback_url,
                    user_agent=VLC_USER_AGENT,
                    chunk_size=ConfigHelper.chunk_size()
                )
            
            # Start reader and get pipe
            self.socket = self.http_reader.start()
            
            # Wrap socket in file object for reading
            self.socket = os.fdopen(self.socket, 'rb', buffering=0)
            
            self.connected = True
            logger.info(f"Stream started for content_id={self.content_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start stream: {e}")
            logger.error(f"Details - Playback URL: {self.playback_url}, Content ID: {self.content_id}")
            logger.debug(f"Exception details: {e}", exc_info=True)
            return False

    def seek_stream(self, target_timestamp: int):
        """Issue LIVESEEK for an active API-mode stream."""
        if not self._is_api_mode():
            raise RuntimeError("LIVESEEK is only available when control_mode is api")
        if not self.running:
            raise RuntimeError("Stream is not running")

        locked = self._legacy_api_lock.acquire(timeout=2.0)
        if not locked:
            raise RuntimeError("Legacy API client is busy, please retry")

        try:
            if not self.ace_api_client:
                raise RuntimeError("Legacy API session is not active")

            issued = self.ace_api_client.seek_stream(int(target_timestamp))
        finally:
            self._legacy_api_lock.release()

        if not issued:
            raise RuntimeError("LIVESEEK command was not accepted")

        self._legacy_probe_cache = None
        self._legacy_probe_cache_ts = 0.0

        return {
            "status": "seek_issued",
            "target_timestamp": int(target_timestamp),
        }

    def _set_runtime_pause_state(self, paused: bool):
        status_value = "pause" if paused else "dl"

        if isinstance(self.legacy_status_probe, dict):
            self.legacy_status_probe["paused"] = bool(paused)
            self.legacy_status_probe["status"] = status_value
            self.legacy_status_probe["status_text"] = status_value

        if isinstance(self._legacy_probe_cache, dict):
            self._legacy_probe_cache["paused"] = bool(paused)
            self._legacy_probe_cache["status"] = status_value
            self._legacy_probe_cache["status_text"] = status_value

    def pause_stream(self):
        """Issue PAUSE for an active API-mode stream."""
        if not self._is_api_mode():
            raise RuntimeError("PAUSE is only available when control_mode is api")
        if not self.running:
            raise RuntimeError("Stream is not running")

        locked = self._legacy_api_lock.acquire(timeout=2.0)
        if not locked:
            raise RuntimeError("Legacy API client is busy, please retry")

        try:
            if not self.ace_api_client:
                raise RuntimeError("Legacy API session is not active")
            issued = self.ace_api_client.pause_stream()
        finally:
            self._legacy_api_lock.release()

        if not issued:
            raise RuntimeError("PAUSE command was not accepted")

        self._set_runtime_pause_state(True)
        return {"status": "paused"}

    def resume_stream(self):
        """Issue RESUME for an active API-mode stream."""
        if not self._is_api_mode():
            raise RuntimeError("RESUME is only available when control_mode is api")
        if not self.running:
            raise RuntimeError("Stream is not running")

        locked = self._legacy_api_lock.acquire(timeout=2.0)
        if not locked:
            raise RuntimeError("Legacy API client is busy, please retry")

        try:
            if not self.ace_api_client:
                raise RuntimeError("Legacy API session is not active")
            issued = self.ace_api_client.resume_stream()
        finally:
            self._legacy_api_lock.release()

        if not issued:
            raise RuntimeError("RESUME command was not accepted")

        self._set_runtime_pause_state(False)
        return {"status": "resumed"}

    def save_stream(self, infohash: Optional[str] = None, index: int = 0, path: str = ""):
        """Issue SAVE for an active API-mode stream."""
        if not self._is_api_mode():
            raise RuntimeError("SAVE is only available when control_mode is api")
        if not self.running:
            raise RuntimeError("Stream is not running")

        target_infohash = str(infohash or "").strip()
        if not target_infohash:
            target_infohash = str(self.resolved_infohash or "").strip()
        if not target_infohash and self.source_input_type == "infohash":
            target_infohash = str(self.source_input or "").strip()
        if not target_infohash:
            raise RuntimeError("No resolved infohash available for SAVE")

        locked = self._legacy_api_lock.acquire(timeout=2.0)
        if not locked:
            raise RuntimeError("Legacy API client is busy, please retry")

        try:
            if not self.ace_api_client:
                raise RuntimeError("Legacy API session is not active")
            issued = self.ace_api_client.save_stream(target_infohash, index=index, path=path)
        finally:
            self._legacy_api_lock.release()

        if not issued:
            raise RuntimeError("SAVE command was not accepted")

        return {
            "status": "save_issued",
            "infohash": target_infohash,
            "index": int(index),
            "path": str(path),
        }

    def _apply_pending_seek_switch(self):
        """Switch HTTP reader to a newly queued playback source without tearing down stream state."""
        pending_engine_swap = None
        with self._reader_lock:
            if self._pending_engine_swap_info:
                pending_engine_swap = dict(self._pending_engine_swap_info)
                self._pending_engine_swap_info = None

        if pending_engine_swap:
            old_engine_host = self.engine_host
            old_engine_port = self.engine_port
            old_engine_api_port = self.engine_api_port
            old_engine_container_id = self.engine_container_id
            old_playback_url = self.playback_url
            old_playback_session_id = self.playback_session_id
            old_stat_url = self.stat_url
            old_command_url = self.command_url
            old_is_live = self.is_live
            old_resolved_infohash = self.resolved_infohash
            old_ace_api_client = self.ace_api_client

            with self._reader_lock:
                old_reader = self.http_reader
                old_socket = self.socket
                self.connected = False

                if old_reader:
                    try:
                        old_reader.stop()
                    except Exception as e:
                        logger.debug("Failed to stop previous HTTP reader during hot swap: %s", e)

                if old_socket:
                    try:
                        old_socket.close()
                    except Exception:
                        pass

                self.engine_host = str(pending_engine_swap.get("engine_host") or old_engine_host)
                self.engine_port = int(pending_engine_swap.get("engine_port") or old_engine_port)
                self.engine_api_port = int(pending_engine_swap.get("engine_api_port") or old_engine_api_port)
                self.engine_container_id = str(pending_engine_swap.get("engine_container_id") or old_engine_container_id)
                self.playback_url = str(pending_engine_swap.get("playback_url") or "").strip() or old_playback_url
                self.playback_session_id = pending_engine_swap.get("playback_session_id") or old_playback_session_id
                self.stat_url = str(pending_engine_swap.get("stat_url") or old_stat_url or "")
                self.command_url = str(pending_engine_swap.get("command_url") or old_command_url or "")
                self.is_live = int(pending_engine_swap.get("is_live", old_is_live or 1) or 1)
                self.resolved_infohash = pending_engine_swap.get("resolved_infohash") or old_resolved_infohash
                self.ace_api_client = pending_engine_swap.get("ace_api_client")

            if not self.start_stream():
                new_client = pending_engine_swap.get("ace_api_client")
                if new_client and new_client is not old_ace_api_client:
                    try:
                        new_client.shutdown()
                    except Exception:
                        pass

                with self._reader_lock:
                    self.engine_host = old_engine_host
                    self.engine_port = old_engine_port
                    self.engine_api_port = old_engine_api_port
                    self.engine_container_id = old_engine_container_id
                    self.playback_url = old_playback_url
                    self.playback_session_id = old_playback_session_id
                    self.stat_url = old_stat_url
                    self.command_url = old_command_url
                    self.is_live = old_is_live
                    self.resolved_infohash = old_resolved_infohash
                    self.ace_api_client = old_ace_api_client

                if not self.start_stream():
                    raise RuntimeError("Failed to apply hot swap and failed to restore previous stream reader")
                raise RuntimeError("Failed to restart HTTP stream reader after hot swap")

            if old_ace_api_client and old_ace_api_client is not self.ace_api_client:
                try:
                    with self._legacy_api_lock:
                        old_ace_api_client.stop_stream()
                        old_ace_api_client.shutdown()
                except Exception as e:
                    logger.debug("Failed to shutdown old legacy API session after hot swap: %s", e)

            self.last_data_time = time.time()
            logger.info(
                "Applied TS hot swap for content_id=%s new_engine=%s",
                self.content_id,
                self.engine_container_id,
            )
            return True

        pending = None
        with self._reader_lock:
            if self._pending_seek_start_info:
                pending = dict(self._pending_seek_start_info)
                self._pending_seek_start_info = None

        if not pending:
            return False

        next_url = pending.get("url")
        if not next_url:
            return False

        logger.info(
            "Applying LIVESEEK playback switch for content_id=%s target_url=%s",
            self.content_id,
            next_url,
        )

        with self._reader_lock:
            old_reader = self.http_reader
            old_socket = self.socket

            self.connected = False

            if old_reader:
                try:
                    old_reader.stop()
                except Exception as e:
                    logger.debug("Failed to stop previous HTTP reader during LIVESEEK switch: %s", e)

            if old_socket:
                try:
                    old_socket.close()
                except Exception:
                    pass

            self.playback_url = next_url
            if pending.get("playback_session_id"):
                self.playback_session_id = pending.get("playback_session_id")

        if not self.start_stream():
            raise RuntimeError("Failed to restart HTTP stream reader after LIVESEEK")

        self.last_data_time = time.time()
        return True
    
    def run(self):
        """Main execution loop with resilient hot-failover support"""
        stream_end_reason = "normal"
        self.retry_count = 0
        
        try:
            # Start health monitor (Once)
            health_thread = threading.Thread(target=self._monitor_health, daemon=True)
            health_thread.start()
            
            
            while self.running and self.retry_count < self.max_retries:
                try:
                    logger.info(f"Connecting to stream (Attempt {self.retry_count + 1}/{self.max_retries}) for content_id={self.content_id}")
                    self._stream_exit_reason = None
                    
                    # Request stream from engine (if not connected/reconnecting)
                    # We always request a new session on reconnect to get updated URLs
                    if not self.connected:
                        # On retry attempts, proactively select a new engine target.
                        if self.retry_count > 0 and not self._failover_to_new_engine():
                            raise RuntimeError("Failover engine selection failed")

                        reused_existing = self._apply_existing_session()
                        if not reused_existing:
                            if not self.request_stream_from_engine():
                                logger.error("Failed to request stream from engine")
                                raise RuntimeError("Engine request failed")
                        
                        # Send stream started event to orchestrator
                        self._send_stream_started_event()
                    
                    # Start streaming
                    if not self.start_stream():
                        logger.error("Failed to start stream")
                        raise RuntimeError("Stream start failed")
                    
                    # Reset retry count on successful stream start & initial read
                    # Wait, we let _process_stream_data do the reading
                    
                    # Process stream data (blocks until EOF or error)
                    self._process_stream_data()
                    
                    # If we exit _process_stream_data and are still running, it's a dropout
                    if self.running:
                        if self._stream_exit_reason == "eof":
                            active_clients = 0
                            try:
                                active_clients = int(self.client_manager.get_total_client_count())
                            except Exception:
                                active_clients = 0

                            if active_clients <= 0:
                                logger.info("Stream ended with EOF and no active clients; skipping failover.")
                                break

                        logger.warning("Stream read loop exited prematurely. Triggering failover.")
                        raise RuntimeError("Stream dropout")
                    else:
                        break # Normal exit
                    
                except Exception as e:
                    self.retry_count += 1
                    logger.error(f"Stream error on attempt {self.retry_count}/{self.max_retries}: {e}")

                    if self._last_request_failure_type == "preflight_failed":
                        logger.warning(
                            "Preflight rejected stream; aborting without further retries: "
                            f"content_id={self.content_id}"
                        )
                        stream_end_reason = "preflight_failed"
                        break
                    
                    if self.retry_count >= self.max_retries:
                        logger.error("Max retries reached, aborting stream")
                        stream_end_reason = "error"
                        break
                    
                    # Exponential Backoff
                    backoff = min(2 * self.retry_count, 10)
                    logger.info(f"Backoff for {backoff}s before reconnecting...")
                    
                    # Cleanup before retry
                    self._cleanup_for_retry()
                    time.sleep(backoff)
                    
        except Exception as e:
            logger.error(f"Fatal stream manager error: {e}", exc_info=True)
            stream_end_reason = "error"
        finally:
            # Send stream ended event
            self._send_stream_ended_event(reason=stream_end_reason)
            self._cleanup()
    
    def _process_stream_data(self):
        """Read from stream and feed to buffer with optimized non-blocking I/O"""
        from ..services.performance_metrics import Timer, performance_metrics
        
        chunk_count = 0

        def _set_socket_non_blocking(sock_obj):
            # Set socket to non-blocking mode for better performance.
            try:
                import fcntl
                import os as os_module
                fd = sock_obj.fileno()
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os_module.O_NONBLOCK)
                logger.debug(f"Set socket to non-blocking mode for content_id={self.content_id}")
            except Exception as e:
                logger.warning(f"Could not set socket to non-blocking mode: {e}, using blocking mode with short timeout")

        _set_socket_non_blocking(self.socket)
        
        while self.running and self.connected:
            try:
                if self._apply_pending_seek_switch():
                    _set_socket_non_blocking(self.socket)

                # Use select with short timeout for responsive shutdown and health checks
                # Reduced from 5.0s to 0.5s for better responsiveness
                import select
                ready, _, _ = select.select([self.socket], [], [], 0.5)
                
                if not ready:
                    # Timeout - no data available, loop continues quickly
                    continue
                
                # Socket is ready for reading - measure read performance
                with Timer(performance_metrics, 'mpegts_chunk_read', {'content_id': self.content_id[:16]}):
                    chunk = self.socket.read(ConfigHelper.chunk_size())
                
                if not chunk:
                    # EOF - stream ended
                    logger.info("Stream ended (EOF)")
                    self._stream_exit_reason = "eof"
                    break
                
                # Add to buffer
                success = self.buffer.add_chunk(chunk)
                if success:
                    self.last_data_time = time.time()
                    chunk_count += 1
                    
                    if chunk_count % 1000 == 0:
                        logger.debug(f"Processed {chunk_count} chunks for content_id={self.content_id}")
                
            except BlockingIOError:
                # Non-blocking socket has no data, this is expected
                continue
            except Exception as e:
                logger.error(f"Error processing stream data: {e}")
                self._stream_exit_reason = "error"
                break
        
        logger.info(f"Stream processing ended for content_id={self.content_id}")

    def _maybe_publish_legacy_stats(self):
        """Deprecated: legacy stats are now gathered by the collector service."""
        return

    def collect_legacy_stats_probe(self, samples: int = 1, per_sample_timeout_s: float = 1.0, force: bool = False):
        """Return a status probe for the active legacy API session, if available."""
        if not self._is_api_mode() or not self.ace_api_client or not self.running:
            return None

        now = time.monotonic()
        if not force and self._legacy_probe_cache is not None:
            if (now - self._legacy_probe_cache_ts) < self._legacy_probe_cache_ttl_s:
                return self._legacy_probe_cache

        # Avoid piling up blocking STATUS requests under load.
        locked = self._legacy_api_lock.acquire(timeout=0.05)
        if not locked:
            return self._legacy_probe_cache

        try:
            if not self.ace_api_client:
                return self._legacy_probe_cache
            probe = self.ace_api_client.collect_status_samples(
                samples=max(1, int(samples)),
                interval_s=0.0,
                per_sample_timeout_s=max(0.2, float(per_sample_timeout_s)),
            )
            self._legacy_probe_cache = probe
            self._legacy_probe_cache_ts = time.monotonic()
            return probe
        except Exception as e:
            logger.debug(f"Legacy stats probe failed for content_id={self.content_id}: {e}")
            return self._legacy_probe_cache
        finally:
            self._legacy_api_lock.release()
    
    def _monitor_health(self):
        """Monitor stream health"""
        while self.running:
            try:
                now = time.time()
                inactivity_duration = now - self.last_data_time
                timeout_threshold = ConfigHelper.connection_timeout()
                
                if inactivity_duration > timeout_threshold and self.connected:
                    if self.healthy:
                        logger.warning(f"Stream unhealthy - no data for {inactivity_duration:.1f}s")
                        self.healthy = False
                elif self.connected and not self.healthy:
                    logger.info("Stream health restored")
                    self.healthy = True
                
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")
            
            time.sleep(self.health_check_interval)
    
    def stop(self):
        """Stop the stream manager"""
        logger.info(f"Stopping stream manager for content_id={self.content_id}")
        self.running = False
        
        if self.http_reader:
            self.http_reader.stop()
        
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        
        # Send stop command only when this proxy owns the engine session.
        if not self.owns_engine_session:
            logger.info(
                "Skipping engine stop for content_id=%s because session is owned by monitoring",
                self.content_id,
            )
        elif self._is_api_mode() and self.ace_api_client:
            try:
                with self._legacy_api_lock:
                    if self.ace_api_client:
                        self.ace_api_client.stop_stream()
                        self.ace_api_client.shutdown()
                        self.ace_api_client = None
                logger.info("Sent STOP/SHUTDOWN to AceStream legacy API")
            except Exception as e:
                logger.warning(f"Failed to send legacy API stop command: {e}")
        elif self.command_url:
            try:
                requests.get(f"{self.command_url}?method=stop", timeout=5)
                logger.info("Sent stop command to AceStream engine")
            except Exception as e:
                logger.warning(f"Failed to send stop command: {e}")
        
        # Send ended event (will check if already sent)
        self._send_stream_ended_event(reason="stopped")
    
    def _cleanup_for_retry(self):
        """Cleanup resources for retry, sending ended event for current session ID"""
        self.connected = False
        
        # Send ended event for the current session ID so it doesn't dangle in orchestrator UI
        self._send_stream_ended_event(reason="failover")
        self._ended_event_sent = False  # Reset for the next reconnect attempt
        
        if self.http_reader:
            try:
                self.http_reader.stop()
            except Exception as e:
                logger.debug(f"Error stopping http_reader during retry cleanup: {e}")
                
        if self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logger.debug(f"Error closing socket during retry cleanup: {e}")

        if self.ace_api_client:
            try:
                with self._legacy_api_lock:
                    if self.ace_api_client:
                        self.ace_api_client.shutdown()
            except Exception as e:
                logger.debug(f"Error closing legacy API client during retry cleanup: {e}")
            self.ace_api_client = None
        self.legacy_status_probe = None
        self._legacy_probe_cache = None
        self._legacy_probe_cache_ts = 0.0
        self._pending_seek_start_info = None
        self._pending_engine_swap_info = None
        self.playback_url = None
        self.playback_session_id = None
        self.stat_url = ""
        self.command_url = ""
        self.existing_session = {}
        self.owns_engine_session = True

    def _cleanup(self):
        """Cleanup resources"""
        self.connected = False
        
        if self.http_reader:
            self.http_reader.stop()
        
        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        if self.ace_api_client:
            try:
                with self._legacy_api_lock:
                    if self.ace_api_client:
                        self.ace_api_client.shutdown()
            except Exception:
                pass
            self.ace_api_client = None
        self.legacy_status_probe = None
        self._legacy_probe_cache = None
        self._legacy_probe_cache_ts = 0.0
        
        # Update stream state in Redis
        if hasattr(self.buffer, 'redis_client') and self.buffer.redis_client:
            try:
                metadata_key = RedisKeys.stream_metadata(self.content_id)
                update_data = {
                    StreamMetadataField.STATE: StreamState.STOPPED,
                    StreamMetadataField.STATE_CHANGED_AT: str(time.time())
                }
                self.buffer.redis_client.hset(metadata_key, mapping=update_data)
            except Exception as e:
                logger.error(f"Failed to update stream state in Redis: {e}")
        
        logger.info(f"Stream manager cleanup complete for content_id={self.content_id}")
