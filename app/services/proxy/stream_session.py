"""Stream session management for AceStream proxy"""

import asyncio
import logging
import httpx
import time
from typing import Optional, AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from .client_manager import ClientManager
from .stream_buffer import StreamBuffer
from .stream_manager import StreamManager
from .stream_generator import StreamGenerator
from .config import (
    EMPTY_STREAM_TIMEOUT,
    STREAM_BUFFER_SIZE,
    COPY_CHUNK_SIZE,
    MAX_CONNECTIONS,
    MAX_KEEPALIVE_CONNECTIONS,
    KEEPALIVE_EXPIRY,
)

logger = logging.getLogger(__name__)


class StreamSession:
    """Manages a single stream session from an AceStream engine.
    
    Handles:
    - Fetching stream from AceStream engine
    - Client multiplexing
    - Automatic cleanup
    - Stream lifecycle
    """
    
    def __init__(
        self,
        stream_id: str,
        ace_id: str,
        engine_host: str,
        engine_port: int,
        container_id: str,
    ):
        self.stream_id = stream_id
        self.ace_id = ace_id
        self.engine_host = engine_host
        self.engine_port = engine_port
        self.container_id = container_id
        
        self.client_manager = ClientManager(stream_id)
        self.created_at = datetime.now(timezone.utc)
        self.started_at: Optional[datetime] = None
        self.ended_at: Optional[datetime] = None
        
        # Stream metadata from AceStream
        self.playback_url: Optional[str] = None
        self.stat_url: Optional[str] = None
        self.command_url: Optional[str] = None
        self.playback_session_id: Optional[str] = None
        
        # Stream state
        self.is_live: bool = False
        self.is_active: bool = False
        self.error: Optional[str] = None
        
        # HTTP client for AceStream communication
        self.http_client: Optional[httpx.AsyncClient] = None
        
        # Buffer and manager for multiplexing (new pattern)
        self.buffer: Optional[StreamBuffer] = None
        self.stream_manager: Optional[StreamManager] = None
        
    async def initialize(self) -> bool:
        """Initialize the stream session by fetching from AceStream engine.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Create HTTP client with specific configuration for AceStream compatibility
            # Based on acexy reference: compression must be disabled and connections limited
            # See: context/acexy/acexy/lib/acexy/acexy.go lines 105-114
            limits = httpx.Limits(
                max_connections=MAX_CONNECTIONS,
                max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
                keepalive_expiry=KEEPALIVE_EXPIRY,
            )
            
            self.http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0, read=None),
                follow_redirects=True,
                limits=limits,
            )
            
            # Build getstream URL
            getstream_url = (
                f"http://{self.engine_host}:{self.engine_port}/ace/getstream"
                f"?id={self.ace_id}&format=json&pid={uuid4()}"
            )
            
            logger.info(f"Fetching stream {self.stream_id} from {getstream_url}")
            
            # Request stream from AceStream
            response = await self.http_client.get(getstream_url)
            response.raise_for_status()
            
            data = response.json()
            
            # Check for errors
            if "error" in data and data["error"]:
                self.error = data["error"]
                logger.error(f"AceStream error for {self.stream_id}: {self.error}")
                return False
            
            # Extract stream info
            if "response" not in data:
                self.error = "Invalid response from AceStream engine"
                logger.error(f"Invalid AceStream response for {self.stream_id}")
                return False
            
            resp = data["response"]
            self.playback_url = resp.get("playback_url")
            self.stat_url = resp.get("stat_url")
            self.command_url = resp.get("command_url")
            self.playback_session_id = resp.get("playback_session_id")
            self.is_live = resp.get("is_live", 0) == 1
            
            if not self.playback_url:
                self.error = "No playback URL in AceStream response"
                logger.error(f"No playback URL for {self.stream_id}")
                return False
            
            # Log the URLs received from engine for debugging
            logger.info(
                f"Stream {self.stream_id} URLs from engine: "
                f"playback_url={self.playback_url}, "
                f"stat_url={self.stat_url}, "
                f"command_url={self.command_url}"
            )
            
            # Ensure playback_url uses the correct engine host/port
            # The engine might return URLs with localhost or container-specific hostnames
            # We need to rewrite them to use the engine_host:engine_port we know is accessible
            from urllib.parse import urlparse, urlunparse
            parsed_playback = urlparse(self.playback_url)
            # Reconstruct playback_url with the correct host:port
            self.playback_url = urlunparse((
                parsed_playback.scheme,
                f"{self.engine_host}:{self.engine_port}",
                parsed_playback.path,
                parsed_playback.params,
                parsed_playback.query,
                parsed_playback.fragment
            ))
            
            # Do the same for stat_url and command_url
            if self.stat_url:
                parsed_stat = urlparse(self.stat_url)
                self.stat_url = urlunparse((
                    parsed_stat.scheme,
                    f"{self.engine_host}:{self.engine_port}",
                    parsed_stat.path,
                    parsed_stat.params,
                    parsed_stat.query,
                    parsed_stat.fragment
                ))
            
            if self.command_url:
                parsed_cmd = urlparse(self.command_url)
                self.command_url = urlunparse((
                    parsed_cmd.scheme,
                    f"{self.engine_host}:{self.engine_port}",
                    parsed_cmd.path,
                    parsed_cmd.params,
                    parsed_cmd.query,
                    parsed_cmd.fragment
                ))
            
            logger.info(
                f"Stream {self.stream_id} initialized successfully "
                f"(playback_session={self.playback_session_id}, is_live={self.is_live}, "
                f"corrected_playback_url={self.playback_url})"
            )
            
            # Create buffer for stream data (Redis-backed)
            # Get Redis client from core utils
            try:
                from core.utils import RedisClient
                redis_client = RedisClient.get_client()
            except Exception as e:
                logger.warning(f"Failed to get Redis client: {e}, using in-memory buffer")
                redis_client = None
            
            self.buffer = StreamBuffer(
                stream_id=self.stream_id,
                redis_client=redis_client
            )
            
            # Create stream manager to pull from AceStream and write to buffer
            self.stream_manager = StreamManager(
                stream_id=self.stream_id,
                playback_url=self.playback_url,
                buffer=self.buffer,
                http_client=self.http_client
            )
            
            # Start the stream manager
            await self.stream_manager.start()
            
            self.is_active = True
            self.started_at = datetime.now(timezone.utc)
            return True
            
        except httpx.HTTPError as e:
            self.error = f"HTTP error: {str(e)}"
            logger.error(f"Failed to initialize stream {self.stream_id}: {e}")
            return False
        except Exception as e:
            self.error = f"Unexpected error: {str(e)}"
            logger.error(f"Unexpected error initializing stream {self.stream_id}: {e}")
            return False
    
    async def stream_data(self, client_id: str) -> AsyncIterator[bytes]:
        """Stream data from buffer to client.
        
        Multiple clients can call this method and they will all receive
        data independently from the shared buffer.
        
        Args:
            client_id: Unique identifier for this client
            
        Yields:
            Chunks of stream data
        """
        if not self.buffer or not self.stream_manager:
            raise RuntimeError("Stream not initialized or buffer not available")
        
        # Create a stream generator for this client
        generator = StreamGenerator(
            stream_id=self.stream_id,
            client_id=client_id,
            buffer=self.buffer,
            initial_behind=3  # Start 3 chunks behind for buffering
        )
        
        try:
            logger.debug(f"Client {client_id} connected to stream {self.stream_id}")
            
            # Wait a bit for buffer to have some data
            max_wait = EMPTY_STREAM_TIMEOUT
            wait_start = time.time()
            while self.buffer.index == 0:
                if time.time() - wait_start > max_wait:
                    logger.error(f"Timeout waiting for stream data for {self.stream_id}")
                    raise RuntimeError("Stream failed to start - no data received")
                await asyncio.sleep(0.1)
            
            # Stream from the generator
            async for chunk in generator.generate():
                # Update client manager activity
                self.client_manager.update_activity()
                yield chunk
            
            logger.info(f"Stream {self.stream_id} client {client_id} disconnected")
            
        except Exception as e:
            logger.error(f"Error in stream_data for {self.stream_id}: {type(e).__name__}: {e}")
            raise
    
    async def stop_stream(self) -> bool:
        """Stop the stream by calling the AceStream command URL.
        
        Returns:
            True if stopped successfully, False otherwise
        """
        if not self.command_url or not self.http_client:
            logger.warning(f"Cannot stop stream {self.stream_id}: no command URL")
            return False
        
        try:
            # Call stop command
            stop_url = f"{self.command_url}?method=stop"
            logger.info(f"Stopping stream {self.stream_id} via {stop_url}")
            
            response = await self.http_client.get(stop_url)
            response.raise_for_status()
            
            self.is_active = False
            self.ended_at = datetime.now(timezone.utc)
            
            logger.info(f"Stream {self.stream_id} stopped successfully")
            return True
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to stop stream {self.stream_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error stopping stream {self.stream_id}: {e}")
            return False
    
    async def cleanup(self):
        """Clean up resources."""
        try:
            # Stop the stream manager if active
            if self.stream_manager:
                await self.stream_manager.stop()
                self.stream_manager = None
            
            # Clean up buffer
            if self.buffer:
                self.buffer.cleanup()
                self.buffer = None
            
            # Try to stop the stream if still active
            if self.is_active and self.command_url:
                await self.stop_stream()
            
            # Close HTTP client
            if self.http_client:
                await self.http_client.aclose()
                self.http_client = None
            
            logger.info(f"Stream session {self.stream_id} cleaned up")
            
        except Exception as e:
            logger.error(f"Error cleaning up stream {self.stream_id}: {e}")
    
    def get_info(self) -> dict:
        """Get stream session info for status reporting."""
        return {
            "stream_id": self.stream_id,
            "ace_id": self.ace_id,
            "engine_host": self.engine_host,
            "engine_port": self.engine_port,
            "container_id": self.container_id,
            "playback_session_id": self.playback_session_id,
            "is_live": self.is_live,
            "is_active": self.is_active,
            "client_count": self.client_manager.get_client_count(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "error": self.error,
            "idle_seconds": self.client_manager.get_idle_time(),
        }
