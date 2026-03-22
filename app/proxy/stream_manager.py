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
from typing import Optional
from urllib.parse import unquote, urlparse, urlunparse

from .http_streamer import HTTPStreamReader
from .stream_buffer import StreamBuffer
from .client_manager import ClientManager
from .redis_keys import RedisKeys
from .constants import StreamState, EventType, StreamMetadataField, VLC_USER_AGENT
from .config_helper import ConfigHelper, Config
from .ace_api_client import AceLegacyApiClient, AceLegacyApiError
from .utils import get_logger

logger = get_logger()

# Timeout for stream event handlers (in seconds)
# This prevents blocking if internal event handling is slow (e.g., Docker API calls)
STREAM_EVENT_HANDLER_TIMEOUT = 2.0


class StreamManager:
    """Manages connection to AceStream engine and stream health"""
    
    def __init__(self, content_id, engine_host, engine_port, engine_container_id, buffer, client_manager, engine_api_port=None, worker_id=None, api_key=None):
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
        self.playback_url = None
        self.stat_url = None
        self.command_url = None
        self.playback_session_id = None
        self.is_live = None
        self.control_mode = (ConfigHelper.control_mode() or "LEGACY_HTTP").upper()
        self.ace_api_client = None
        self._legacy_api_lock = threading.Lock()
        self.resolved_infohash = None
        self.legacy_status_probe = None
        self._last_legacy_stats_publish = 0.0
        self._legacy_stats_publish_interval_s = 2.0
        
        # Connection state
        self.running = True
        self.connected = False
        self.healthy = True
        self.retry_count = 0
        self.max_retries = ConfigHelper.max_retries()
        
        # HTTP stream reader
        self.http_reader = None
        self.socket = None  # Read end of pipe from http_reader
        
        # Health monitoring
        self.last_data_time = time.time()
        self.health_check_interval = 5
        
        # Orchestrator event tracking
        self.stream_id = None  # Will be set after sending start event
        self._ended_event_sent = False  # Track if we've already sent the ended event
        
        logger.info(f"StreamManager initialized for content_id={content_id}")
    
    def request_stream_from_engine(self):
        """Request stream from AceStream engine according to selected control mode."""
        if self.control_mode == "LEGACY_API":
            return self._request_stream_legacy_api()
        return self._request_stream_legacy_http()

    def _request_stream_legacy_http(self):
        """Current JSON-over-HTTP control flow (default)."""
        # Check stream mode to determine which endpoint to use
        stream_mode = Config.STREAM_MODE
        
        if stream_mode == 'HLS':
            # HLS mode - use manifest.m3u8 endpoint
            url = f"http://{self.engine_host}:{self.engine_port}/ace/manifest.m3u8"
        else:
            # TS mode (default) - use getstream endpoint
            url = f"http://{self.engine_host}:{self.engine_port}/ace/getstream"
        
        # Generate unique PID to prevent errors when multiple streams access the same engine
        pid = str(uuid.uuid4())
        
        params = {
            "id": self.content_id,
            "format": "json",
            "pid": pid
        }
        
        # Build full URL for logging (define early to avoid NameError in exception handlers)
        full_url = f"{url}?id={self.content_id}&format=json&pid={pid}"
        
        try:
            logger.info(f"Requesting stream from AceStream engine in {stream_mode} mode: {url}")
            logger.debug(f"Full request URL: {full_url}")
            logger.debug(f"Engine: {self.engine_host}:{self.engine_port}, Content ID: {self.content_id}, Container: {self.engine_container_id}")
            logger.debug(f"Generated PID: {pid}")
            
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
                logger.error(f"Error details - Engine: {self.engine_host}:{self.engine_port}, Content ID: {self.content_id}, Container: {self.engine_container_id}")
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
                logger.error(f"Error details - Engine: {self.engine_host}:{self.engine_port}, Content ID: {self.content_id}")
                logger.debug(f"Response data: {resp_data}")
                raise RuntimeError("No playback_url in AceStream response")
            
            logger.info(f"AceStream session started: playback_session_id={self.playback_session_id}")
            logger.info(f"Playback URL: {self.playback_url}")
            logger.debug(f"Stat URL: {self.stat_url}")
            logger.debug(f"Command URL: {self.command_url}")
            logger.debug(f"Is Live: {self.is_live}")
            
            return True
            
        except Exception as e:
            # Log detailed error information for both request and general exceptions
            logger.error(f"Failed to request stream from AceStream engine: {e}")
            logger.error(f"Request details - URL: {full_url}, Engine: {self.engine_host}:{self.engine_port}, Content ID: {self.content_id}")
            logger.debug(f"Exception details: {e}", exc_info=True)
            return False

    def _request_stream_legacy_api(self):
        """Optional telnet-style legacy API control flow."""
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

            preflight = client.preflight(self.content_id, tier="light")
            if not preflight.get("available"):
                message = preflight.get("message") or "content unavailable"
                raise AceLegacyApiError(f"Preflight failed: {message}")

            self.resolved_infohash = preflight.get("infohash") or self.content_id
            start_info = client.start_stream(self.resolved_infohash, mode="infohash")
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
            logger.error(f"Failed to request stream from AceStream legacy API: {e}")
            logger.error(
                f"Request details - API: {self.engine_host}:{self.engine_api_port}, Content ID: {self.content_id}"
            )
            logger.debug(f"Exception details: {e}", exc_info=True)
            try:
                if self.ace_api_client:
                    self.ace_api_client.shutdown()
            except Exception:
                pass
            self.ace_api_client = None
            return False

    def _normalize_playback_url(self, url: str) -> str:
        """Rewrite localhost playback URLs so proxy can always reach the selected engine."""
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
                netloc = f"{self.engine_host}:{parsed.port}" if parsed.port else self.engine_host
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
                        key_type="infohash",
                        key=self.content_id
                    ),
                    session=SessionInfo(
                        playback_session_id=self.playback_session_id,
                        stat_url=self.stat_url,
                        command_url=self.command_url,
                        is_live=self.is_live
                    ),
                    labels={
                        "source": "proxy",
                        "worker_id": self.worker_id or "unknown",
                        "proxy.control_mode": self.control_mode,
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
        if self._ended_event_sent:
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
                    
                    # Request stream from engine (if not connected/reconnecting)
                    # We always request a new session on reconnect to get updated URLs
                    if not self.connected:
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
                        logger.warning("Stream read loop exited prematurely. Triggering failover.")
                        raise RuntimeError("Stream dropout")
                    else:
                        break # Normal exit
                    
                except Exception as e:
                    self.retry_count += 1
                    logger.error(f"Stream error on attempt {self.retry_count}/{self.max_retries}: {e}")
                    
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
        
        # Set socket to non-blocking mode for better performance
        # This allows us to check for data availability without long blocking waits
        try:
            import fcntl
            import os as os_module
            fd = self.socket.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os_module.O_NONBLOCK)
            logger.debug(f"Set socket to non-blocking mode for content_id={self.content_id}")
        except Exception as e:
            logger.warning(f"Could not set socket to non-blocking mode: {e}, using blocking mode with short timeout")
        
        while self.running and self.connected:
            try:
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
                break
        
        logger.info(f"Stream processing ended for content_id={self.content_id}")

    def _maybe_publish_legacy_stats(self):
        """Deprecated: legacy stats are now gathered by the collector service."""
        return

    def collect_legacy_stats_probe(self, samples: int = 1, per_sample_timeout_s: float = 1.0):
        """Return a status probe for the active legacy API session, if available."""
        if self.control_mode != "LEGACY_API" or not self.ace_api_client or not self.running:
            return None

        try:
            with self._legacy_api_lock:
                if not self.ace_api_client:
                    return None
                return self.ace_api_client.collect_status_samples(
                    samples=max(1, int(samples)),
                    interval_s=0.0,
                    per_sample_timeout_s=max(0.2, float(per_sample_timeout_s)),
                )
        except Exception as e:
            logger.debug(f"Legacy stats probe failed for content_id={self.content_id}: {e}")
            return None
    
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
        
        # Send stop command to AceStream engine
        if self.control_mode == "LEGACY_API" and self.ace_api_client:
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
