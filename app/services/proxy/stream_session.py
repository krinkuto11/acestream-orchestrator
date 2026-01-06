"""Stream session management for AceStream proxy"""

import asyncio
import logging
import httpx
import time
from typing import Optional, AsyncIterator
from datetime import datetime, timezone
from uuid import uuid4

from .client_manager import ClientManager
from .config import (
    EMPTY_STREAM_TIMEOUT,
    STREAM_BUFFER_SIZE,
    COPY_CHUNK_SIZE,
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
        
    async def initialize(self) -> bool:
        """Initialize the stream session by fetching from AceStream engine.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Create HTTP client with no read timeout for streaming
            # connect: 10s, read: None (unlimited for streaming), write: 30s, pool: 30s
            self.http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0, read=None),
                follow_redirects=True,
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
            
            logger.info(
                f"Stream {self.stream_id} initialized successfully "
                f"(playback_session={self.playback_session_id}, is_live={self.is_live})"
            )
            
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
    
    async def stream_data(self) -> AsyncIterator[bytes]:
        """Stream data from the AceStream playback URL.
        
        Yields:
            Chunks of stream data
        """
        if not self.playback_url or not self.http_client:
            raise RuntimeError("Stream not initialized")
        
        try:
            logger.info(f"Starting stream data for {self.stream_id} from {self.playback_url}")
            
            # Stream from playback URL with explicit timeout override for this request
            # Set a longer initial timeout for establishing the stream connection
            async with self.http_client.stream(
                "GET", 
                self.playback_url,
                timeout=httpx.Timeout(60.0, connect=30.0, read=None, write=30.0)
            ) as response:
                logger.info(f"Stream response received for {self.stream_id}, status: {response.status_code}")
                response.raise_for_status()
                
                chunk_count = 0
                last_log_time = time.time()
                
                async for chunk in response.aiter_bytes(chunk_size=COPY_CHUNK_SIZE):
                    if chunk:
                        chunk_count += 1
                        
                        # Update client manager activity
                        self.client_manager.update_activity()
                        
                        # Log progress periodically (every 1000 chunks)
                        if chunk_count % 1000 == 0:
                            elapsed = time.time() - last_log_time
                            logger.debug(
                                f"Stream {self.stream_id}: {chunk_count} chunks "
                                f"({chunk_count * COPY_CHUNK_SIZE / 1024 / 1024:.1f}MB), "
                                f"clients={self.client_manager.get_client_count()}, "
                                f"rate={1000 / elapsed:.1f} chunks/s"
                            )
                            last_log_time = time.time()
                        
                        yield chunk
                
                logger.info(f"Stream {self.stream_id} ended normally after {chunk_count} chunks")
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error streaming {self.stream_id}: {type(e).__name__}: {e}")
            self.error = f"Stream error: {type(e).__name__}: {str(e)}"
            raise
        except Exception as e:
            logger.error(f"Unexpected error streaming {self.stream_id}: {type(e).__name__}: {e}")
            self.error = f"Unexpected error: {type(e).__name__}: {str(e)}"
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
