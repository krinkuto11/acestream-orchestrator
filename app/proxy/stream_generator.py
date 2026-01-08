"""
Stream generation and client-side handling for AceStream streams.
Simplified adaptation from ts_proxy - handles per-client delivery and buffering.
"""

import time
import logging
import gevent

from .config_helper import ConfigHelper
from .utils import get_logger, create_ts_packet
from .redis_keys import RedisKeys
from .constants import StreamMetadataField, INITIAL_DATA_WAIT_TIMEOUT, INITIAL_DATA_CHECK_INTERVAL

logger = get_logger()


class StreamGenerator:
    """Handles generating streams for clients"""
    
    def __init__(self, content_id, client_id, client_ip, client_user_agent, stream_initializing=False):
        self.content_id = content_id
        self.client_id = client_id
        self.client_ip = client_ip
        self.client_user_agent = client_user_agent
        self.stream_initializing = stream_initializing
        
        # Performance tracking
        self.stream_start_time = time.time()
        self.bytes_sent = 0
        self.chunks_sent = 0
        self.local_index = 0
        self.consecutive_empty = 0
        
        # Rate tracking
        self.last_stats_time = time.time()
        self.last_stats_bytes = 0
        self.current_rate = 0.0
        
        # TTL refresh
        self.last_ttl_refresh = time.time()
        self.ttl_refresh_interval = 3
    
    def generate(self):
        """Generator function that produces stream content for the client"""
        self.stream_start_time = time.time()
        self.bytes_sent = 0
        self.chunks_sent = 0
        
        try:
            logger.info(f"[{self.client_id}] Stream generator started, stream_ready={not self.stream_initializing}")
            
            # If stream is initializing, wait for it
            if self.stream_initializing:
                stream_ready = self._wait_for_initialization()
                if not stream_ready:
                    return
            
            logger.info(f"[{self.client_id}] Stream ready, starting normal streaming")
            
            # Reset start time for real streaming
            self.stream_start_time = time.time()
            
            # Setup streaming
            if not self._setup_streaming():
                return
            
            # Main streaming loop
            while True:
                # Get chunks from buffer
                chunks = self.buffer.get_chunks(self.local_index)
                
                if chunks:
                    # Send chunks to client
                    for chunk in chunks:
                        yield chunk
                        self.bytes_sent += len(chunk)
                        self.chunks_sent += 1
                    
                    # Update local index
                    self.local_index += len(chunks)
                    self.consecutive_empty = 0
                    
                    # Update stats periodically
                    if time.time() - self.last_stats_time >= 5:
                        self._update_stats()
                    
                else:
                    # No data available
                    self.consecutive_empty += 1
                    
                    # Check if stream has ended
                    if self.consecutive_empty > 30:
                        logger.info(f"[{self.client_id}] Stream ended (no data)")
                        break
                    
                    # Wait a bit before retrying
                    gevent.sleep(0.1)
                
                # Refresh client TTL periodically
                if time.time() - self.last_ttl_refresh >= self.ttl_refresh_interval:
                    self._refresh_ttl()
            
            logger.info(f"[{self.client_id}] Stream completed: {self.bytes_sent} bytes, {self.chunks_sent} chunks")
            
        except GeneratorExit:
            logger.info(f"[{self.client_id}] Client disconnected")
        except Exception as e:
            logger.error(f"[{self.client_id}] Stream error: {e}", exc_info=True)
        finally:
            self._cleanup()
    
    def _wait_for_initialization(self):
        """Wait for stream to initialize"""
        timeout = ConfigHelper.channel_init_grace_period()
        start_time = time.time()
        
        logger.info(f"[{self.client_id}] Waiting for stream initialization (timeout: {timeout}s)")
        
        while time.time() - start_time < timeout:
            # Check if stream is ready (would check Redis metadata in full implementation)
            # For now, just wait a bit
            gevent.sleep(1)
            
            # TODO: Check Redis for stream state
            # For now, assume ready after a short wait
            if time.time() - start_time > 2:
                return True
        
        logger.error(f"[{self.client_id}] Stream initialization timeout")
        return False
    
    def _wait_for_initial_data(self):
        """Wait for initial data to arrive in the buffer before starting streaming.
        
        This is critical because the HTTP streamer needs time to connect to the
        playback URL and fetch the first chunks. Without this wait, clients will
        see an empty buffer and disconnect prematurely.
        """
        timeout = INITIAL_DATA_WAIT_TIMEOUT
        check_interval = INITIAL_DATA_CHECK_INTERVAL
        start_time = time.time()
        
        logger.info(f"[{self.client_id}] Waiting for initial data in buffer...")
        
        while time.time() - start_time < timeout:
            # Check if buffer has any data
            if self.buffer.index > 0:
                elapsed = time.time() - start_time
                logger.info(f"[{self.client_id}] Initial data available after {elapsed:.2f}s (buffer index: {self.buffer.index})")
                return True
            
            # Wait before checking again
            gevent.sleep(check_interval)
        
        # Timeout - but still proceed if we have at least some data
        if self.buffer.index > 0:
            logger.warning(f"[{self.client_id}] Initial data wait timeout, but buffer has data (index: {self.buffer.index})")
            return True
        
        logger.error(f"[{self.client_id}] Timeout waiting for initial data (buffer still empty after {timeout}s)")
        return False
    
    def _setup_streaming(self):
        """Setup streaming parameters"""
        from .server import ProxyServer
        
        proxy_server = ProxyServer.get_instance()
        
        # Get stream buffer
        self.buffer = proxy_server.stream_buffers.get(self.content_id)
        if not self.buffer:
            logger.error(f"[{self.client_id}] No buffer found for content_id={self.content_id}")
            return False
        
        # Get client manager
        self.client_manager = proxy_server.client_managers.get(self.content_id)
        if not self.client_manager:
            logger.error(f"[{self.client_id}] No client manager found")
            return False
        
        # Add client
        self.client_manager.add_client(self.client_id, self.client_ip, self.client_user_agent)
        
        # Wait for initial data in buffer before starting streaming
        # This gives the HTTP streamer time to fetch data from the playback URL
        if not self._wait_for_initial_data():
            # Error already logged in _wait_for_initial_data
            return False
        
        # Start from current buffer position
        self.local_index = self.buffer.index
        
        logger.info(f"[{self.client_id}] Starting from buffer index {self.local_index}")
        return True
    
    def _update_stats(self):
        """Update streaming statistics"""
        now = time.time()
        elapsed = now - self.last_stats_time
        
        if elapsed > 0:
            bytes_since_last = self.bytes_sent - self.last_stats_bytes
            self.current_rate = bytes_since_last / elapsed / 1024  # KB/s
            
            logger.debug(f"[{self.client_id}] Rate: {self.current_rate:.1f} KB/s, Total: {self.bytes_sent / 1024 / 1024:.1f} MB")
            
            self.last_stats_time = now
            self.last_stats_bytes = self.bytes_sent
    
    def _refresh_ttl(self):
        """Refresh client TTL in Redis"""
        if hasattr(self, 'client_manager') and self.client_manager:
            self.client_manager.refresh_client_ttl()
            self.last_ttl_refresh = time.time()
    
    def _cleanup(self):
        """Cleanup after streaming ends"""
        # Remove client
        if hasattr(self, 'client_manager') and self.client_manager:
            self.client_manager.remove_client(self.client_id)
        
        logger.info(f"[{self.client_id}] Cleanup complete")


def create_stream_generator(content_id, client_id, client_ip, client_user_agent, stream_initializing=False):
    """Factory function to create StreamGenerator"""
    return StreamGenerator(content_id, client_id, client_ip, client_user_agent, stream_initializing)
