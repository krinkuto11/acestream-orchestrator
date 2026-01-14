"""
FastAPI-based HLS Proxy for AceStream.
Rewritten from context/hls_proxy to use FastAPI instead of Django.
Handles HLS manifest proxying, segment buffering, and URL rewriting.
"""

import threading
import logging
import time
import requests
import m3u8
import os
import asyncio
from typing import Dict, Optional, Set, Any
from urllib.parse import urljoin, urlparse
from .config_helper import ConfigHelper

logger = logging.getLogger(__name__)


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
    """Manages client connections and activity tracking (adapted from context/hls_proxy)"""
    
    def __init__(self):
        self.last_activity: Dict[str, float] = {}  # Maps client IPs to last activity timestamp
        self.lock = threading.Lock()
        
    def record_activity(self, client_ip: str):
        """Record client activity timestamp"""
        with self.lock:
            prev_time = self.last_activity.get(client_ip)
            current_time = time.time()
            self.last_activity[client_ip] = current_time
            if not prev_time:
                logger.info(f"New client connected: {client_ip}")
            else:
                logger.debug(f"Client activity: {client_ip}")
                
    def cleanup_inactive(self, timeout: float) -> bool:
        """Remove inactive clients and return True if no clients remain"""
        now = time.time()
        with self.lock:
            active_clients = {
                ip: last_time 
                for ip, last_time in self.last_activity.items()
                if (now - last_time) < timeout
            }
            
            removed = set(self.last_activity.keys()) - set(active_clients.keys())
            if removed:
                for ip in removed:
                    inactive_time = now - self.last_activity[ip]
                    logger.warning(f"Client {ip} inactive for {inactive_time:.1f}s, removing")
            
            self.last_activity = active_clients
            if active_clients:
                oldest = min(now - t for t in active_clients.values())
                logger.debug(f"Active clients: {len(active_clients)}, oldest activity: {oldest:.1f}s ago")
            
            return len(active_clients) == 0
    
    def has_clients(self) -> bool:
        """Check if there are any active clients"""
        with self.lock:
            return len(self.last_activity) > 0


class StreamBuffer:
    """Thread-safe buffer for HLS segments"""
    
    def __init__(self):
        self.buffer: Dict[int, bytes] = {}
        self.lock: threading.Lock = threading.Lock()
    
    def __getitem__(self, key: int) -> Optional[bytes]:
        """Get segment data by sequence number"""
        with self.lock:
            return self.buffer.get(key)
    
    def __setitem__(self, key: int, value: bytes):
        """Store segment data by sequence number"""
        with self.lock:
            self.buffer[key] = value
            # Cleanup old segments if we exceed MAX_SEGMENTS
            if len(self.buffer) > HLSConfig.MAX_SEGMENTS():
                keys = sorted(self.buffer.keys())
                to_remove = keys[:-HLSConfig.MAX_SEGMENTS()]
                for k in to_remove:
                    del self.buffer[k]
    
    def __contains__(self, key: int) -> bool:
        """Check if sequence number exists in buffer"""
        with self.lock:
            return key in self.buffer
    
    def keys(self):
        """Get list of available sequence numbers"""
        with self.lock:
            return list(self.buffer.keys())


class StreamManager:
    """Manages HLS stream state and fetching"""
    
    def __init__(self, playback_url: str, channel_id: str, engine_host: str, engine_port: int, 
                 engine_container_id: str, session_info: Dict[str, Any], api_key: Optional[str] = None):
        self.playback_url = playback_url
        self.channel_id = channel_id
        self.engine_host = engine_host
        self.engine_port = engine_port
        self.engine_container_id = engine_container_id
        self.running = True
        
        # Session info from AceStream API
        self.playback_session_id = session_info.get('playback_session_id')
        self.stat_url = session_info.get('stat_url')
        self.command_url = session_info.get('command_url')
        self.is_live = session_info.get('is_live', 1)
        
        # API key for orchestrator events
        self.api_key = api_key
        
        # Sequence tracking
        self.next_sequence = 0
        self.segment_durations: Dict[int, float] = {}
        
        # Manifest info
        self.target_duration = 10.0
        self.manifest_version = 3
        
        # Buffer state
        self.buffer_ready = threading.Event()
        self.initial_buffering = True
        self.buffered_duration = 0.0
        
        # Orchestrator event tracking
        self.stream_id = None  # Will be set after sending start event
        self._ended_event_sent = False  # Track if we've already sent the ended event
        
        # Client management (for activity-based cleanup)
        self.client_manager: Optional[ClientManager] = None
        self.cleanup_thread: Optional[threading.Thread] = None
        self.cleanup_running = False
        
        logger.info(f"Initialized HLS stream manager for channel {channel_id}")
    
    def stop(self):
        """Stop the stream manager"""
        self.running = False
        self.cleanup_running = False
        logger.info(f"Stopping stream manager for channel {self.channel_id}")
        
        # Send stop command to AceStream engine
        if self.command_url:
            try:
                requests.get(f"{self.command_url}?method=stop", timeout=5)
                logger.info("Sent stop command to AceStream engine")
            except Exception as e:
                logger.warning(f"Failed to send stop command: {e}")
    
    def _send_stream_started_event(self):
        """Send stream started event to orchestrator using internal handler (no HTTP)
        
        Runs asynchronously in a background daemon thread to avoid blocking initialize_channel().
        This ensures the UI remains responsive during stream initialization.
        
        Note: stream_id is set to a temporary value immediately, then updated by the background
        thread when the event handler completes. This is intentional for fire-and-forget async.
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
                        key=self.channel_id
                    ),
                    session=SessionInfo(
                        playback_session_id=self.playback_session_id,
                        stat_url=self.stat_url,
                        command_url=self.command_url,
                        is_live=self.is_live
                    ),
                    labels={
                        "source": "hls_proxy",
                        "stream_mode": "HLS"
                    }
                )
                
                # Call internal handler directly (no HTTP request)
                result = handle_stream_started(event)
                
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
        # This will be updated by the background thread once the event is processed
        self.stream_id = f"temp-hls-{self.channel_id[:16]}-{int(time.time())}"
        
        # Send event in background thread to avoid blocking
        event_thread = threading.Thread(
            target=_send_event,
            name=f"HLS-StartEvent-{self.channel_id[:8]}",
            daemon=True
        )
        event_thread.start()
    
    def _send_stream_ended_event(self, reason="normal"):
        """Send stream ended event to orchestrator using internal handler (no HTTP)"""
        # Check if we've already sent the ended event
        if self._ended_event_sent:
            logger.debug(f"HLS stream ended event already sent for stream_id={self.stream_id}, skipping")
            return
        
        # Check if we have a stream_id to send
        if not self.stream_id:
            logger.warning(f"No stream_id available for channel_id={self.channel_id}, cannot send ended event")
            return
        
        def _send_event():
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
                
                logger.info(f"Sent HLS stream ended event to orchestrator: stream_id={self.stream_id}, reason={reason}")
                
            except Exception as e:
                logger.warning(f"Failed to send HLS stream ended event to orchestrator: {e}")
                logger.debug(f"Exception details: {e}", exc_info=True)
        
        # Send event in background thread to avoid blocking
        event_thread = threading.Thread(
            target=_send_event,
            name=f"HLS-EndEvent-{self.channel_id[:8]}",
            daemon=True
        )
        event_thread.start()
    
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
                    
                    # Calculate timeout based on target duration (similar to reference implementation)
                    # Use 3x target duration as timeout (configurable via CLIENT_TIMEOUT_FACTOR)
                    timeout = self.target_duration * 3.0
                    
                    if self.client_manager and self.client_manager.cleanup_inactive(timeout):
                        logger.info(f"Channel {self.channel_id}: All clients disconnected for {timeout:.1f}s")
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
    """Fetches HLS segments from the source URL"""
    
    def __init__(self, manager: StreamManager, buffer: StreamBuffer):
        self.manager = manager
        self.buffer = buffer
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': HLSConfig.DEFAULT_USER_AGENT})
        self.downloaded_segments: Set[str] = set()
    
    def fetch_loop(self):
        """Main fetch loop for downloading segments"""
        retry_delay = 1
        max_retry_delay = 8
        
        while self.manager.running:
            try:
                # Fetch manifest
                # Note: playback_url is immutable after channel initialization.
                # The has_channel() check in main.py ensures we don't create duplicate
                # channels, so there's no risk of the URL changing during fetch.
                response = self.session.get(self.manager.playback_url, timeout=10)
                response.raise_for_status()
                
                manifest = m3u8.loads(response.text)
                
                # Update manifest info
                if manifest.target_duration:
                    self.manager.target_duration = float(manifest.target_duration)
                if manifest.version:
                    self.manager.manifest_version = manifest.version
                
                if not manifest.segments:
                    logger.warning(f"No segments in manifest for channel {self.manager.channel_id}")
                    time.sleep(retry_delay)
                    continue
                
                # Initial buffering - fetch multiple segments
                if self.manager.initial_buffering:
                    self._fetch_initial_segments(manifest, response.url)
                    continue
                
                # Normal operation - fetch latest segment
                self._fetch_latest_segment(manifest, response.url)
                
                # Wait before next manifest fetch
                time.sleep(self.manager.target_duration * HLSConfig.SEGMENT_FETCH_INTERVAL())
                
                # Reset retry delay on success
                retry_delay = 1
                
            except Exception as e:
                # Only log if manager is still running (expected errors when stopping)
                if self.manager.running:
                    logger.error(f"Fetch loop error for channel {self.manager.channel_id}: {e}")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
    
    def _fetch_initial_segments(self, manifest: m3u8.M3U8, base_url: str):
        """Fetch initial segments for buffering"""
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
        
        # Download segments
        successful_downloads = 0
        for segment in segments_to_fetch:
            try:
                segment_url = urljoin(base_url, segment.uri)
                segment_data = self._download_segment(segment_url)
                
                if segment_data and len(segment_data) > 0:
                    seq = self.manager.next_sequence
                    self.buffer[seq] = segment_data
                    duration = float(segment.duration)
                    self.manager.segment_durations[seq] = duration
                    self.manager.buffered_duration += duration
                    self.manager.next_sequence += 1
                    successful_downloads += 1
                    self.downloaded_segments.add(segment.uri)
                    logger.debug(f"Buffered initial segment {seq} (duration: {duration}s)")
            except Exception as e:
                logger.error(f"Error downloading initial segment: {e}")
        
        # Mark buffer ready if we got some segments
        if successful_downloads > 0:
            self.manager.initial_buffering = False
            self.manager.buffer_ready.set()
            logger.info(f"Initial buffer ready with {successful_downloads} segments "
                       f"({self.manager.buffered_duration:.1f}s of content)")
    
    def _fetch_latest_segment(self, manifest: m3u8.M3U8, base_url: str):
        """Fetch the latest segment if not already downloaded"""
        latest_segment = manifest.segments[-1]
        
        if latest_segment.uri in self.downloaded_segments:
            return
        
        try:
            segment_url = urljoin(base_url, latest_segment.uri)
            segment_data = self._download_segment(segment_url)
            
            if segment_data and len(segment_data) > 0:
                seq = self.manager.next_sequence
                self.buffer[seq] = segment_data
                duration = float(latest_segment.duration)
                self.manager.segment_durations[seq] = duration
                self.manager.next_sequence += 1
                self.downloaded_segments.add(latest_segment.uri)
                logger.debug(f"Buffered segment {seq} (duration: {duration}s)")
        except Exception as e:
            logger.error(f"Error downloading latest segment: {e}")
    
    def _download_segment(self, url: str) -> Optional[bytes]:
        """Download a single segment"""
        # Check if manager is still running before downloading
        if not self.manager.running:
            logger.debug(f"Stream manager stopped, skipping segment download from {url}")
            return None
        
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.content
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
        self.fetch_threads: Dict[str, threading.Thread] = {}
        self.lock = threading.Lock()  # Lock for thread-safe operations
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
                          api_key: Optional[str] = None):
        """Initialize a new HLS channel.
        
        This method should only be called for new channels. Existing channels should be
        detected using has_channel() before calling this method.
        """
        with self.lock:
            # Safety check - this should not happen if caller uses has_channel() properly
            if channel_id in self.stream_managers:
                logger.warning(f"HLS channel {channel_id} already exists, skipping initialization")
                return
            
            logger.info(f"Initializing HLS channel {channel_id} with URL {playback_url}")
            
            # Create manager, buffer, and client manager
            manager = StreamManager(
                playback_url=playback_url,
                channel_id=channel_id,
                engine_host=engine_host,
                engine_port=engine_port,
                engine_container_id=engine_container_id,
                session_info=session_info,
                api_key=api_key
            )
            buffer = StreamBuffer()
            client_manager = ClientManager()
            
            # Link client manager to stream manager
            manager.client_manager = client_manager
            
            self.stream_managers[channel_id] = manager
            self.stream_buffers[channel_id] = buffer
            self.client_managers[channel_id] = client_manager
            
            # Send stream started event to orchestrator
            manager._send_stream_started_event()
            
            # Start fetcher thread
            fetcher = StreamFetcher(manager, buffer)
            thread = threading.Thread(
                target=fetcher.fetch_loop,
                name=f"HLS-Fetcher-{channel_id[:8]}",
                daemon=True
            )
            thread.start()
            self.fetch_threads[channel_id] = thread
            
            # Start cleanup monitoring
            manager.start_cleanup_monitoring(self)
            
            logger.info(f"HLS channel {channel_id} initialized")
    
    def record_client_activity(self, channel_id: str, client_ip: str):
        """Record client activity for a channel (called on each manifest/segment request)"""
        if channel_id in self.client_managers:
            self.client_managers[channel_id].record_activity(client_ip)
    
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
            
            # Note: Don't wait for fetch thread - it's a daemon thread that will
            # stop on its own when manager.running becomes False. Waiting would
            # block the request handler and make the UI unresponsive.
            
            # Cleanup
            del self.stream_managers[channel_id]
            del self.stream_buffers[channel_id]
            if channel_id in self.client_managers:
                del self.client_managers[channel_id]
            if channel_id in self.fetch_threads:
                del self.fetch_threads[channel_id]
            
            logger.info(f"HLS channel {channel_id} stopped and cleaned up")
    
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
                
                await asyncio.sleep(0.05)  # Non-blocking wait instead of time.sleep(0.1)
            
            # Build manifest
            return self._build_manifest(channel_id, manager, buffer)
    
    def _build_manifest(self, channel_id: str, manager: 'StreamManager', buffer: 'StreamBuffer') -> str:
        """Build manifest from buffer state (fast, non-blocking operation)"""
        # Build manifest
        available = sorted(buffer.keys())
        max_seq = max(available)
        
        if len(available) <= HLSConfig.INITIAL_SEGMENTS():
            min_seq = min(available)
        else:
            min_seq = max(min(available), max_seq - HLSConfig.WINDOW_SIZE() + 1)
        
        # Generate manifest lines
        manifest_lines = [
            '#EXTM3U',
            f'#EXT-X-VERSION:{manager.manifest_version}',
            f'#EXT-X-MEDIA-SEQUENCE:{min_seq}',
            f'#EXT-X-TARGETDURATION:{int(manager.target_duration)}',
        ]
        
        # Add segments within window
        window_segments = [s for s in available if min_seq <= s <= max_seq]
        for seq in window_segments:
            duration = manager.segment_durations.get(seq, 10.0)
            manifest_lines.append(f'#EXTINF:{duration},')
            manifest_lines.append(f'/ace/hls/{channel_id}/segment/{seq}.ts')
        
        manifest_content = '\n'.join(manifest_lines)
        logger.debug(f"Generated manifest for channel {channel_id} with segments {min_seq}-{max_seq}")
        
        return manifest_content
    
    def get_segment(self, channel_id: str, segment_name: str) -> bytes:
        """Get segment data"""
        if channel_id not in self.stream_buffers:
            raise ValueError(f"Channel {channel_id} not found")
        
        try:
            segment_id = int(segment_name.split('.')[0])
        except ValueError:
            raise ValueError(f"Invalid segment name: {segment_name}")
        
        buffer = self.stream_buffers[channel_id]
        segment_data = buffer[segment_id]
        
        if segment_data is None:
            raise ValueError(f"Segment {segment_id} not found in buffer")
        
        return segment_data
