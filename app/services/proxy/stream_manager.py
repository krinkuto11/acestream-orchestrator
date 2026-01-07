"""Stream manager that pulls from AceStream and writes to buffer"""

import asyncio
import logging
import httpx
import time
from typing import Optional

from .stream_buffer import StreamBuffer
from .config import COPY_CHUNK_SIZE, USER_AGENT

logger = logging.getLogger(__name__)


class StreamManager:
    """Manages pulling stream data from AceStream and writing to buffer.
    
    Based on dispatcharr_proxy's StreamManager pattern:
    - Single connection to upstream (AceStream)
    - Writes data to Redis buffer
    - Multiple clients read from buffer independently
    - Handles connection failures and retries
    """
    
    def __init__(
        self,
        stream_id: str,
        playback_url: str,
        buffer: StreamBuffer,
        http_client: httpx.AsyncClient,
    ):
        """Initialize stream manager.
        
        Args:
            stream_id: Unique stream identifier
            playback_url: URL to fetch stream from
            buffer: Stream buffer to write to
            http_client: HTTP client for requests
        """
        self.stream_id = stream_id
        self.playback_url = playback_url
        self.buffer = buffer
        self.http_client = http_client
        
        # State
        self.is_running = False
        self.is_connected = False
        self.stream_task: Optional[asyncio.Task] = None
        self.error: Optional[Exception] = None
        
        # Stats
        self.bytes_received = 0
        self.chunks_received = 0
        self.start_time: Optional[float] = None
        
        # Health tracking
        self.last_data_time: Optional[float] = None
        self.healthy = True
        
    async def start(self):
        """Start pulling stream data."""
        if self.is_running:
            logger.warning(f"StreamManager for {self.stream_id} already running")
            return
        
        self.is_running = True
        self.start_time = time.time()
        self.stream_task = asyncio.create_task(self._stream_loop())
        logger.info(f"Started StreamManager for {self.stream_id}")
    
    async def stop(self):
        """Stop pulling stream data."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.stream_task:
            self.stream_task.cancel()
            try:
                await self.stream_task
            except asyncio.CancelledError:
                pass
            self.stream_task = None
        
        logger.info(
            f"Stopped StreamManager for {self.stream_id} "
            f"({self.chunks_received} chunks, {self.bytes_received / 1024 / 1024:.1f}MB)"
        )
    
    async def _stream_loop(self):
        """Main loop that pulls data from AceStream and writes to buffer."""
        try:
            logger.info(f"Starting stream fetch for {self.stream_id} from {self.playback_url}")
            
            # Build headers - critical for AceStream compatibility
            # Based on acexy reference implementation
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Encoding": "identity",  # Disable compression - required for AceStream
            }
            
            # Stream from playback URL
            async with self.http_client.stream(
                "GET",
                self.playback_url,
                headers=headers,
                timeout=httpx.Timeout(timeout=None, connect=30.0, read=None, write=None, pool=None)
            ) as response:
                logger.info(
                    f"Stream response received for {self.stream_id}, "
                    f"status: {response.status_code}"
                )
                response.raise_for_status()
                
                self.is_connected = True
                last_log_time = time.time()
                
                async for chunk in response.aiter_bytes(chunk_size=COPY_CHUNK_SIZE):
                    if not chunk or not self.is_running:
                        break
                    
                    # Update stats
                    self.chunks_received += 1
                    self.bytes_received += len(chunk)
                    self.last_data_time = time.time()
                    self.healthy = True
                    
                    # Write to buffer
                    success = self.buffer.add_chunk(chunk)
                    if not success:
                        logger.warning(f"Failed to add chunk to buffer for {self.stream_id}")
                    
                    # Log progress periodically
                    if self.chunks_received % 1000 == 0:
                        elapsed = time.time() - last_log_time
                        rate = 1000 / elapsed if elapsed > 0 else 0
                        logger.debug(
                            f"Stream {self.stream_id}: {self.chunks_received} chunks "
                            f"({self.bytes_received / 1024 / 1024:.1f}MB), "
                            f"buffer_index={self.buffer.index}, "
                            f"rate={rate:.1f} chunks/s"
                        )
                        last_log_time = time.time()
                
                logger.info(f"Stream {self.stream_id} ended normally after {self.chunks_received} chunks")
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error streaming {self.stream_id}: {type(e).__name__}: {e}")
            self.error = e
            self.healthy = False
            
        except Exception as e:
            logger.error(f"Unexpected error streaming {self.stream_id}: {e}", exc_info=True)
            self.error = e
            self.healthy = False
            
        finally:
            self.is_connected = False
    
    def is_healthy(self) -> bool:
        """Check if stream manager is healthy.
        
        Returns:
            True if healthy, False otherwise
        """
        if not self.healthy:
            return False
        
        # Check if we've received data recently
        if self.last_data_time:
            elapsed = time.time() - self.last_data_time
            if elapsed > 30:  # No data for 30 seconds
                logger.warning(
                    f"StreamManager for {self.stream_id} unhealthy: "
                    f"no data for {elapsed:.1f}s"
                )
                return False
        
        return True
