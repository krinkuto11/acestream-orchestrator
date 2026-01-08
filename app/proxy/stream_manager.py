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
from typing import Optional

from .http_streamer import HTTPStreamReader
from .stream_buffer import StreamBuffer
from .client_manager import ClientManager
from .redis_keys import RedisKeys
from .constants import StreamState, EventType, StreamMetadataField, VLC_USER_AGENT
from .config_helper import ConfigHelper, Config
from .utils import get_logger

logger = get_logger()


class StreamManager:
    """Manages connection to AceStream engine and stream health"""
    
    def __init__(self, content_id, engine_host, engine_port, engine_container_id, buffer, client_manager, worker_id=None, api_key=None):
        # Basic properties
        self.content_id = content_id
        self.engine_host = engine_host
        self.engine_port = engine_port
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
        
        logger.info(f"StreamManager initialized for content_id={content_id}")
    
    def request_stream_from_engine(self):
        """Request stream from AceStream engine API"""
        url = f"http://{self.engine_host}:{self.engine_port}/ace/getstream"
        params = {
            "format": "json",
            "infohash": self.content_id
        }
        
        # Build full URL for logging (define early to avoid NameError in exception handlers)
        full_url = f"{url}?format=json&infohash={self.content_id}"
        
        try:
            logger.info(f"Requesting stream from AceStream engine: {url}")
            logger.debug(f"Full request URL: {full_url}")
            logger.debug(f"Engine: {self.engine_host}:{self.engine_port}, Content ID: {self.content_id}, Container: {self.engine_container_id}")
            
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
                    "key": self.content_id
                },
                "session": {
                    "playback_session_id": self.playback_session_id,
                    "stat_url": self.stat_url,
                    "command_url": self.command_url,
                    "is_live": self.is_live
                },
                "labels": {
                    "source": "proxy",
                    "worker_id": self.worker_id or "unknown"
                }
            }
            
            headers = {}
            if self.api_key:
                headers['X-API-KEY'] = self.api_key
                logger.debug(f"Sending stream started event with API key to {orchestrator_url}/events/stream_started")
            else:
                logger.warning("No API key configured for stream started event - may fail with 401 Unauthorized")
            
            logger.debug(f"Stream started event data: {event_data}")
            
            response = requests.post(
                f"{orchestrator_url}/events/stream_started",
                json=event_data,
                headers=headers,
                timeout=5
            )
            
            logger.debug(f"Stream started event response status: {response.status_code}")
            
            response.raise_for_status()
            
            # Get stream_id from response
            result = response.json()
            self.stream_id = result.get('id')
            
            logger.info(f"Sent stream started event to orchestrator: stream_id={self.stream_id}")
            
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 401:
                logger.error(f"Failed to send stream started event - 401 Unauthorized. Check API_KEY configuration.")
                logger.error(f"API key present: {bool(self.api_key)}, Orchestrator URL: {orchestrator_url}")
            else:
                logger.warning(f"Failed to send stream started event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
        except Exception as e:
            logger.warning(f"Failed to send stream started event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
    
    def _send_stream_ended_event(self, reason="normal"):
        """Send stream ended event to orchestrator"""
        try:
            orchestrator_url = os.getenv('ORCHESTRATOR_URL', 'http://localhost:8000')
            
            event_data = {
                "container_id": self.engine_container_id,
                "stream_id": self.stream_id,
                "reason": reason
            }
            
            headers = {}
            if self.api_key:
                headers['X-API-KEY'] = self.api_key
                logger.debug(f"Sending stream ended event with API key to {orchestrator_url}/events/stream_ended")
            else:
                logger.warning("No API key configured for stream ended event - may fail with 401 Unauthorized")
            
            logger.debug(f"Stream ended event data: {event_data}")
            
            response = requests.post(
                f"{orchestrator_url}/events/stream_ended",
                json=event_data,
                headers=headers,
                timeout=5
            )
            
            logger.debug(f"Stream ended event response status: {response.status_code}")
            
            response.raise_for_status()
            
            logger.info(f"Sent stream ended event to orchestrator: stream_id={self.stream_id}, reason={reason}")
            
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 401:
                logger.error(f"Failed to send stream ended event - 401 Unauthorized. Check API_KEY configuration.")
                logger.error(f"API key present: {bool(self.api_key)}, Orchestrator URL: {orchestrator_url}")
            else:
                logger.warning(f"Failed to send stream ended event to orchestrator: {e}")
            logger.debug(f"Exception details: {e}", exc_info=True)
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
        """Main execution loop"""
        stream_end_reason = "normal"
        try:
            # Start health monitor
            health_thread = threading.Thread(target=self._monitor_health, daemon=True)
            health_thread.start()
            
            # Request stream from engine
            if not self.request_stream_from_engine():
                logger.error("Failed to request stream from engine")
                stream_end_reason = "failed_to_start"
                return
            
            # Send stream started event to orchestrator
            self._send_stream_started_event()
            
            # Start streaming
            if not self.start_stream():
                logger.error("Failed to start stream")
                stream_end_reason = "failed_to_start"
                return
            
            # Process stream data
            self._process_stream_data()
            
        except Exception as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            stream_end_reason = "error"
        finally:
            # Send stream ended event
            self._send_stream_ended_event(reason=stream_end_reason)
            self._cleanup()
    
    def _process_stream_data(self):
        """Read from stream and feed to buffer"""
        chunk_count = 0
        
        while self.running and self.connected:
            try:
                # Read chunk from socket (with timeout)
                import select
                ready, _, _ = select.select([self.socket], [], [], 5.0)
                
                if not ready:
                    # Timeout - no data available
                    continue
                
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
                
            except Exception as e:
                logger.error(f"Error processing stream data: {e}")
                break
        
        logger.info(f"Stream processing ended for content_id={self.content_id}")
    
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
        if self.command_url:
            try:
                requests.get(f"{self.command_url}?method=stop", timeout=5)
                logger.info("Sent stop command to AceStream engine")
            except Exception as e:
                logger.warning(f"Failed to send stop command: {e}")
        
        # Send ended event if we haven't already
        if self.stream_id:
            self._send_stream_ended_event(reason="stopped")
    
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
