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
from typing import Dict, Optional, Set
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


class HLSConfig:
    """Configuration for HLS proxy"""
    DEFAULT_USER_AGENT = "VLC/3.0.16 LibVLC/3.0.16"
    MAX_SEGMENTS = 20
    INITIAL_SEGMENTS = 3
    WINDOW_SIZE = 6
    BUFFER_READY_TIMEOUT = 30
    FIRST_SEGMENT_TIMEOUT = 30
    INITIAL_BUFFER_SECONDS = 10
    MAX_INITIAL_SEGMENTS = 10


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
            if len(self.buffer) > HLSConfig.MAX_SEGMENTS:
                keys = sorted(self.buffer.keys())
                to_remove = keys[:-HLSConfig.MAX_SEGMENTS]
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
                 engine_container_id: str, session_info: Dict[str, any], api_key: Optional[str] = None):
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
        
        logger.info(f"Initialized HLS stream manager for channel {channel_id}")
    
    def stop(self):
        """Stop the stream manager"""
        self.running = False
        logger.info(f"Stopping stream manager for channel {self.channel_id}")
        
        # Send stop command to AceStream engine
        if self.command_url:
            try:
                requests.get(f"{self.command_url}?method=stop", timeout=5)
                logger.info("Sent stop command to AceStream engine")
            except Exception as e:
                logger.warning(f"Failed to send stop command: {e}")
    
    def _send_stream_started_event(self):
        """Send stream started event to orchestrator"""
        try:
            # Get orchestrator URL from environment
            orchestrator_url = os.getenv('ORCHESTRATOR_URL', 'http://localhost:8000')
            
            event_data = {
                "container_id": self.engine_container_id,
                "engine": {
                    "host": self.engine_host,
                    "port": self.engine_port
                },
                "stream": {
                    "key_type": "infohash",
                    "key": self.channel_id
                },
                "session": {
                    "playback_session_id": self.playback_session_id,
                    "stat_url": self.stat_url,
                    "command_url": self.command_url,
                    "is_live": self.is_live
                },
                "labels": {
                    "source": "hls_proxy",
                    "stream_mode": "HLS"
                }
            }
            
            headers = {}
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
                logger.debug(f"Sending HLS stream started event with API key to {orchestrator_url}/events/stream_started")
            else:
                logger.warning("No API key configured for HLS stream started event - may fail with 401 Unauthorized")
            
            logger.debug(f"HLS stream started event data: {event_data}")
            
            response = requests.post(
                f"{orchestrator_url}/events/stream_started",
                json=event_data,
                headers=headers,
                timeout=5
            )
            
            logger.debug(f"HLS stream started event response status: {response.status_code}")
            
            response.raise_for_status()
            
            # Get stream_id from response
            result = response.json()
            self.stream_id = result.get('id')
            
            logger.info(f"Sent HLS stream started event to orchestrator: stream_id={self.stream_id}")
            
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 401:
                logger.error(f"Failed to send HLS stream started event - 401 Unauthorized. Check API_KEY configuration.")
                logger.error(f"API key present: {bool(self.api_key)}, Orchestrator URL: {orchestrator_url}")
            else:
                logger.warning(f"Failed to send HLS stream started event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
        except Exception as e:
            logger.warning(f"Failed to send HLS stream started event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
    
    def _send_stream_ended_event(self, reason="normal"):
        """Send stream ended event to orchestrator"""
        # Check if we've already sent the ended event
        if self._ended_event_sent:
            logger.debug(f"HLS stream ended event already sent for stream_id={self.stream_id}, skipping")
            return
        
        # Check if we have a stream_id to send
        if not self.stream_id:
            logger.warning(f"No stream_id available for channel_id={self.channel_id}, cannot send ended event")
            return
        
        try:
            orchestrator_url = os.getenv('ORCHESTRATOR_URL', 'http://localhost:8000')
            
            event_data = {
                "container_id": self.engine_container_id,
                "stream_id": self.stream_id,
                "reason": reason
            }
            
            headers = {}
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
                logger.debug(f"Sending HLS stream ended event with API key to {orchestrator_url}/events/stream_ended")
            else:
                logger.warning("No API key configured for HLS stream ended event - may fail with 401 Unauthorized")
            
            logger.debug(f"HLS stream ended event data: {event_data}")
            
            response = requests.post(
                f"{orchestrator_url}/events/stream_ended",
                json=event_data,
                headers=headers,
                timeout=5
            )
            
            logger.debug(f"HLS stream ended event response status: {response.status_code}")
            
            response.raise_for_status()
            
            # Mark as sent
            self._ended_event_sent = True
            
            logger.info(f"Sent HLS stream ended event to orchestrator: stream_id={self.stream_id}, reason={reason}")
            
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 401:
                logger.error(f"Failed to send HLS stream ended event - 401 Unauthorized. Check API_KEY configuration.")
                logger.error(f"API key present: {bool(self.api_key)}, Orchestrator URL: {orchestrator_url}")
            else:
                logger.warning(f"Failed to send HLS stream ended event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
        except Exception as e:
            logger.warning(f"Failed to send HLS stream ended event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)


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
                time.sleep(self.manager.target_duration * 0.5)
                
                # Reset retry delay on success
                retry_delay = 1
                
            except Exception as e:
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
            
            if (current_duration >= HLSConfig.INITIAL_BUFFER_SECONDS or 
                len(segments_to_fetch) >= HLSConfig.MAX_INITIAL_SEGMENTS):
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
        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.content
        except Exception as e:
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
        self.fetch_threads: Dict[str, threading.Thread] = {}
        self.client_counts: Dict[str, int] = {}  # Track number of clients per channel
        self.lock = threading.Lock()  # Lock for thread-safe client tracking
        logger.info("HLS ProxyServer initialized")
    
    def initialize_channel(self, channel_id: str, playback_url: str, engine_host: str, 
                          engine_port: int, engine_container_id: str, session_info: Dict[str, any],
                          api_key: Optional[str] = None):
        """Initialize a new HLS channel (or add client to existing channel)"""
        with self.lock:
            # If channel already exists, just increment client count
            if channel_id in self.stream_managers:
                self.client_counts[channel_id] = self.client_counts.get(channel_id, 0) + 1
                logger.info(f"HLS channel {channel_id} already exists, added client (total clients: {self.client_counts[channel_id]})")
                return
            
            logger.info(f"Initializing HLS channel {channel_id} with URL {playback_url}")
            
            # Create manager and buffer
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
            
            self.stream_managers[channel_id] = manager
            self.stream_buffers[channel_id] = buffer
            self.client_counts[channel_id] = 1
            
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
            
            logger.info(f"HLS channel {channel_id} initialized with 1 client")
    
    def remove_client(self, channel_id: str):
        """Remove a client from a channel and stop if no clients remain"""
        with self.lock:
            if channel_id not in self.client_counts:
                logger.warning(f"Attempt to remove client from non-existent HLS channel {channel_id}")
                return
            
            self.client_counts[channel_id] -= 1
            remaining_clients = self.client_counts[channel_id]
            
            logger.info(f"Client removed from HLS channel {channel_id}, remaining clients: {remaining_clients}")
            
            # If no clients remain, schedule channel cleanup
            if remaining_clients <= 0:
                logger.info(f"No clients left for HLS channel {channel_id}, scheduling stop")
                # Use threading.Timer for delayed cleanup (similar to TS proxy)
                timer = threading.Timer(5.0, self.stop_channel, args=[channel_id])
                timer.daemon = True
                timer.start()
    
    def stop_channel(self, channel_id: str, reason: str = "normal"):
        """Stop and cleanup a channel"""
        with self.lock:
            if channel_id not in self.stream_managers:
                logger.debug(f"HLS channel {channel_id} already stopped")
                return
            
            # Check if there are still clients (in case new ones connected during timer delay)
            if self.client_counts.get(channel_id, 0) > 0:
                logger.info(f"Cancelling stop for HLS channel {channel_id} - clients reconnected")
                return
            
            logger.info(f"Stopping HLS channel {channel_id}")
            manager = self.stream_managers[channel_id]
            
            # Send stream ended event before stopping
            manager._send_stream_ended_event(reason=reason)
            
            # Stop the manager
            manager.stop()
            
            # Wait for thread to finish
            if channel_id in self.fetch_threads:
                self.fetch_threads[channel_id].join(timeout=5)
            
            # Cleanup
            del self.stream_managers[channel_id]
            del self.stream_buffers[channel_id]
            if channel_id in self.fetch_threads:
                del self.fetch_threads[channel_id]
            if channel_id in self.client_counts:
                del self.client_counts[channel_id]
            
            logger.info(f"HLS channel {channel_id} stopped and cleaned up")
    
    def get_manifest(self, channel_id: str) -> str:
        """Generate HLS manifest for a channel"""
        if channel_id not in self.stream_managers:
            raise ValueError(f"Channel {channel_id} not found")
        
        manager = self.stream_managers[channel_id]
        buffer = self.stream_buffers[channel_id]
        
        # Wait for initial buffer
        if not manager.buffer_ready.wait(HLSConfig.BUFFER_READY_TIMEOUT):
            raise TimeoutError("Timeout waiting for initial buffer")
        
        # Wait for first segment
        start_time = time.time()
        while True:
            available = buffer.keys()
            if available:
                break
            
            if time.time() - start_time > HLSConfig.FIRST_SEGMENT_TIMEOUT:
                raise TimeoutError("Timeout waiting for first segment")
            
            time.sleep(0.1)
        
        # Build manifest
        available = sorted(buffer.keys())
        max_seq = max(available)
        
        if len(available) <= HLSConfig.INITIAL_SEGMENTS:
            min_seq = min(available)
        else:
            min_seq = max(min(available), max_seq - HLSConfig.WINDOW_SIZE + 1)
        
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
