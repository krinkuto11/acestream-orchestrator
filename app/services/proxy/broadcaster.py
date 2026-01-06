"""Stream broadcaster for multiplexing to multiple clients"""

import asyncio
import logging
import httpx
import time
from typing import Optional, Set, AsyncIterator
from collections import deque

from .config import COPY_CHUNK_SIZE, USER_AGENT

logger = logging.getLogger(__name__)


class StreamBroadcaster:
    """Broadcasts a single upstream stream to multiple clients.
    
    This implements true multiplexing where only one HTTP connection
    is made to the AceStream engine, and all clients receive the same
    stream data through asyncio queues.
    """
    
    def __init__(self, stream_id: str, playback_url: str, http_client: httpx.AsyncClient):
        self.stream_id = stream_id
        self.playback_url = playback_url
        self.http_client = http_client
        
        # Broadcast state
        self.is_streaming = False
        self.stream_task: Optional[asyncio.Task] = None
        self.stream_error: Optional[Exception] = None
        
        # Client queues for broadcasting
        self.client_queues: Set[asyncio.Queue] = set()
        self.queues_lock = asyncio.Lock()
        
        # Buffer for late-joining clients (ring buffer)
        # Stores recent chunks so new clients get immediate data
        self.recent_chunks: deque = deque(maxlen=100)  # ~6.4MB buffer (64KB * 100)
        
        # Synchronization
        self.first_chunk_event = asyncio.Event()
        
        # Stats
        self.chunk_count = 0
        self.bytes_sent = 0
        self.start_time: Optional[float] = None
        
    async def start(self):
        """Start the broadcast stream."""
        if self.is_streaming:
            logger.warning(f"Broadcaster for {self.stream_id} already streaming")
            return
        
        self.is_streaming = True
        self.start_time = time.time()
        self.stream_task = asyncio.create_task(self._stream_loop())
        logger.info(f"Started broadcaster for {self.stream_id}")
        
    async def stop(self):
        """Stop the broadcast stream."""
        if not self.is_streaming:
            return
        
        self.is_streaming = False
        
        if self.stream_task:
            self.stream_task.cancel()
            try:
                await self.stream_task
            except asyncio.CancelledError:
                pass
            self.stream_task = None
        
        # Clear all client queues
        async with self.queues_lock:
            for queue in self.client_queues:
                try:
                    queue.put_nowait(None)  # Signal end of stream
                except asyncio.QueueFull:
                    pass
            self.client_queues.clear()
        
        logger.info(
            f"Stopped broadcaster for {self.stream_id} "
            f"({self.chunk_count} chunks, {self.bytes_sent / 1024 / 1024:.1f}MB)"
        )
        
    async def add_client(self) -> asyncio.Queue:
        """Add a client to receive broadcast data.
        
        Returns:
            Queue that will receive stream chunks
        """
        queue = asyncio.Queue(maxsize=50)  # Buffer ~3.2MB per client
        
        async with self.queues_lock:
            self.client_queues.add(queue)
            logger.debug(f"Added client to broadcaster {self.stream_id} (total: {len(self.client_queues)})")
            
            # Send recent chunks to new client for immediate playback
            for chunk in self.recent_chunks:
                try:
                    queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    # If queue is full, skip old chunks
                    logger.warning(f"Queue full when sending recent chunks to new client")
                    break
        
        return queue
        
    async def remove_client(self, queue: asyncio.Queue):
        """Remove a client from the broadcast.
        
        Args:
            queue: The client's queue to remove
        """
        async with self.queues_lock:
            self.client_queues.discard(queue)
            logger.debug(f"Removed client from broadcaster {self.stream_id} (remaining: {len(self.client_queues)})")
            
    def get_client_count(self) -> int:
        """Get the number of connected clients."""
        return len(self.client_queues)
        
    async def wait_for_first_chunk(self, timeout: float = 30.0):
        """Wait for the first chunk to be received.
        
        This ensures new clients don't start streaming before any data is available.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Raises:
            asyncio.TimeoutError: If no data received within timeout
        """
        try:
            await asyncio.wait_for(self.first_chunk_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(f"Timeout waiting for first chunk for {self.stream_id}")
            raise
            
    async def _stream_loop(self):
        """Background task that fetches from AceStream and broadcasts to all clients."""
        try:
            logger.info(f"Starting stream fetch for {self.stream_id} from {self.playback_url}")
            
            # Build headers - AceStream requires specific headers for compatibility
            # Based on acexy reference implementation:
            # 1. User-Agent to identify as media player
            # 2. Accept-Encoding: identity to disable compression (critical for AceStream)
            # See: context/acexy/acexy/lib/acexy/acexy.go lines 105-114
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
                timeout=httpx.Timeout(60.0, connect=30.0, read=None, write=30.0)
            ) as response:
                logger.info(f"Stream response received for {self.stream_id}, status: {response.status_code}")
                response.raise_for_status()
                
                last_log_time = time.time()
                
                async for chunk in response.aiter_bytes(chunk_size=COPY_CHUNK_SIZE):
                    if not chunk or not self.is_streaming:
                        break
                    
                    self.chunk_count += 1
                    self.bytes_sent += len(chunk)
                    
                    # Add to recent chunks buffer
                    self.recent_chunks.append(chunk)
                    
                    # Signal first chunk received
                    if self.chunk_count == 1:
                        self.first_chunk_event.set()
                        logger.info(f"First chunk received for {self.stream_id} ({len(chunk)} bytes)")
                    
                    # Broadcast to all clients
                    async with self.queues_lock:
                        dead_queues = []
                        for queue in self.client_queues:
                            try:
                                # Use put_nowait to avoid blocking
                                # If queue is full, client is too slow - drop them
                                queue.put_nowait(chunk)
                            except asyncio.QueueFull:
                                logger.warning(f"Client queue full for {self.stream_id}, marking for removal")
                                dead_queues.append(queue)
                        
                        # Remove dead queues
                        for queue in dead_queues:
                            self.client_queues.discard(queue)
                            try:
                                queue.put_nowait(None)  # Signal disconnection
                            except:
                                pass
                    
                    # Log progress periodically
                    if self.chunk_count % 1000 == 0:
                        elapsed = time.time() - last_log_time
                        rate = 1000 / elapsed if elapsed > 0 else 0
                        logger.debug(
                            f"Stream {self.stream_id}: {self.chunk_count} chunks "
                            f"({self.bytes_sent / 1024 / 1024:.1f}MB), "
                            f"clients={len(self.client_queues)}, "
                            f"rate={rate:.1f} chunks/s"
                        )
                        last_log_time = time.time()
                
                logger.info(f"Stream {self.stream_id} ended normally after {self.chunk_count} chunks")
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error streaming {self.stream_id}: {type(e).__name__}: {e}")
            self.stream_error = e
        except Exception as e:
            logger.error(f"Unexpected error streaming {self.stream_id}: {type(e).__name__}: {e}")
            self.stream_error = e
        finally:
            # Signal end to all clients
            async with self.queues_lock:
                for queue in self.client_queues:
                    try:
                        queue.put_nowait(None)  # Signal end of stream
                    except asyncio.QueueFull:
                        pass
            
            self.is_streaming = False
