"""Acexy-inspired stream manager - direct streaming with parallel multiwriter pattern.

Based on context/acexy implementation:
- Direct streaming from playback_url (no intermediate buffering)
- Parallel multiwriter pattern (one source → many clients simultaneously)
- Critical HTTP client configuration for AceStream compatibility
- io.Copy-style chunked reading and writing
"""

import asyncio
import logging
import httpx
import time
from typing import Optional, Dict, Set, Any
from asyncio import Queue, Event
from dataclasses import dataclass

from .config import COPY_CHUNK_SIZE, USER_AGENT

logger = logging.getLogger(__name__)


@dataclass
class ClientWriter:
    """Represents a connected client writer."""
    client_id: str
    queue: Queue
    connected_event: Event
    bytes_sent: int = 0
    chunks_sent: int = 0
    connected_at: float = 0.0
    last_write_time: float = 0.0
    
    def __post_init__(self):
        self.connected_at = time.time()
        self.last_write_time = time.time()


class AcexyStreamManager:
    """Stream manager using acexy's parallel multiwriter pattern.
    
    Key principles from acexy/lib/acexy/acexy.go:
    1. Single HTTP connection to playback_url
    2. Read chunks from stream response
    3. Write each chunk to ALL connected clients in parallel
    4. Add/remove clients dynamically
    5. Proper HTTP client configuration for AceStream
    """
    
    def __init__(
        self,
        stream_id: str,
        playback_url: str,
        http_client: httpx.AsyncClient,
        stream_session: Optional[Any] = None,
        empty_timeout: float = 60.0,
        buffer_size: int = COPY_CHUNK_SIZE,
    ):
        """Initialize Acexy-style stream manager.
        
        Args:
            stream_id: Unique stream identifier
            playback_url: AceStream playback URL to stream from
            http_client: HTTP client (must be configured per acexy requirements)
            stream_session: Reference to parent StreamSession (for metadata)
            empty_timeout: Timeout when stream is empty (no data received)
            buffer_size: Size of chunks to read from source
        """
        self.stream_id = stream_id
        self.playback_url = playback_url
        self.http_client = http_client
        self.stream_session = stream_session
        self.empty_timeout = empty_timeout
        self.buffer_size = buffer_size
        
        # Client management (parallel multiwriter)
        self.clients: Dict[str, ClientWriter] = {}
        self.clients_lock = asyncio.Lock()
        
        # Stream state
        self.is_running = False
        self.is_connected = False
        self.stream_task: Optional[asyncio.Task] = None
        self.error: Optional[Exception] = None
        
        # Connection event - signals when connection is established or failed
        self.connection_event = Event()
        
        # Stats
        self.bytes_received = 0
        self.chunks_received = 0
        self.start_time: Optional[float] = None
        
        # Health tracking
        self.last_data_time: Optional[float] = None
        self.healthy = True
        
        # Retry and robustness (from dispatcharr)
        self.retry_count = 0
        self.max_retries = 3
        self.health_monitor_task: Optional[asyncio.Task] = None
        self.connection_start_time: Optional[float] = None
        
    async def start(self):
        """Start the stream manager."""
        if self.is_running:
            logger.warning(f"AcexyStreamManager for {self.stream_id} already running")
            return
        
        self.is_running = True
        self.start_time = time.time()
        self.connection_event.clear()
        self.stream_task = asyncio.create_task(self._stream_loop())
        
        # Start health monitor task (from dispatcharr pattern)
        self.health_monitor_task = asyncio.create_task(self._health_monitor_loop())
        
        logger.info(f"Started AcexyStreamManager for {self.stream_id}")
    
    async def stop(self):
        """Stop the stream manager."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        # Cancel health monitor
        if self.health_monitor_task:
            self.health_monitor_task.cancel()
            try:
                await self.health_monitor_task
            except asyncio.CancelledError:
                pass
            self.health_monitor_task = None
        
        # Cancel stream task
        if self.stream_task:
            self.stream_task.cancel()
            try:
                await self.stream_task
            except asyncio.CancelledError:
                pass
            self.stream_task = None
        
        # Disconnect all clients
        async with self.clients_lock:
            for client_id in list(self.clients.keys()):
                await self._remove_client_internal(client_id)
        
        logger.info(
            f"Stopped AcexyStreamManager for {self.stream_id} "
            f"({self.chunks_received} chunks, {self.bytes_received / 1024 / 1024:.1f}MB)"
        )
    
    async def wait_for_connection(self, timeout: float = 30.0) -> bool:
        """Wait for stream connection to be established.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if connected, False if timeout or error
        """
        try:
            await asyncio.wait_for(self.connection_event.wait(), timeout=timeout)
            if self.error:
                logger.error(
                    f"AcexyStreamManager connection failed for {self.stream_id}: {self.error}"
                )
                return False
            if not self.is_connected:
                logger.error(
                    f"AcexyStreamManager connection event set but not connected for {self.stream_id}"
                )
                return False
            logger.info(f"AcexyStreamManager connected successfully for {self.stream_id}")
            return True
        except asyncio.TimeoutError:
            logger.error(
                f"Timeout waiting for AcexyStreamManager connection for {self.stream_id} "
                f"after {timeout}s"
            )
            return False
    
    async def add_client(self, client_id: str) -> Queue:
        """Add a client to receive stream data.
        
        Args:
            client_id: Unique client identifier
            
        Returns:
            Queue to read stream chunks from
        """
        async with self.clients_lock:
            if client_id in self.clients:
                logger.warning(f"Client {client_id} already connected to {self.stream_id}")
                return self.clients[client_id].queue
            
            # Create client writer
            queue = Queue(maxsize=100)  # Buffer up to 100 chunks per client
            connected_event = Event()
            
            writer = ClientWriter(
                client_id=client_id,
                queue=queue,
                connected_event=connected_event,
            )
            
            self.clients[client_id] = writer
            
            logger.info(
                f"Added client {client_id} to stream {self.stream_id} "
                f"({len(self.clients)} total clients)"
            )
            
            return queue
    
    async def remove_client(self, client_id: str) -> int:
        """Remove a client from the stream.
        
        Args:
            client_id: Client identifier
            
        Returns:
            Number of remaining clients
        """
        async with self.clients_lock:
            return await self._remove_client_internal(client_id)
    
    async def _remove_client_internal(self, client_id: str) -> int:
        """Internal method to remove client (must be called with lock held)."""
        if client_id not in self.clients:
            logger.warning(f"Client {client_id} not found in stream {self.stream_id}")
            return len(self.clients)
        
        writer = self.clients.pop(client_id)
        
        # Signal queue end
        try:
            await writer.queue.put(None)
        except:
            pass
        
        # Log stats
        if writer.connected_at:
            duration = time.time() - writer.connected_at
            rate = writer.bytes_sent / duration / 1024 if duration > 0 else 0
            logger.info(
                f"Removed client {client_id} from stream {self.stream_id}: "
                f"{writer.chunks_sent} chunks, {writer.bytes_sent / 1024:.1f} KB, "
                f"{rate:.1f} KB/s over {duration:.1f}s"
            )
        
        remaining = len(self.clients)
        logger.info(f"Client {client_id} removed from {self.stream_id} ({remaining} remaining)")
        
        return remaining
    
    async def has_clients(self) -> bool:
        """Check if there are any connected clients."""
        async with self.clients_lock:
            return len(self.clients) > 0
    
    async def get_client_count(self) -> int:
        """Get number of connected clients."""
        async with self.clients_lock:
            return len(self.clients)
    
    async def _stream_loop(self):
        """Main streaming loop with retry logic (acexy + dispatcharr patterns).
        
        Combines:
        - Acexy's direct streaming approach
        - Dispatcharr's retry and robustness logic
        """
        # Retry loop (from dispatcharr)
        while self.is_running and self.retry_count < self.max_retries:
            try:
                logger.info(
                    f"Starting acexy-style stream from {self.playback_url} for {self.stream_id} "
                    f"(attempt {self.retry_count + 1}/{self.max_retries})"
                )
                
                # Build headers - CRITICAL for AceStream compatibility
                # Based on acexy reference: must disable compression, set proper user agent
                # See: context/acexy/acexy/lib/acexy/acexy.go lines 105-114
                headers = {
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",  # CRITICAL: Disable compression
                    "Connection": "keep-alive",
                }
                
                # Stream from playback URL
                # Note: HTTP client should already be configured with:
                # - DisableCompression: true
                # - MaxConnsPerHost: limited (10)
                # - MaxIdleConns: limited (10)
                async with self.http_client.stream(
                    "GET",
                    self.playback_url,
                    headers=headers,
                    timeout=httpx.Timeout(
                        timeout=None,
                        connect=30.0,
                        read=None,
                        write=None,
                        pool=None
                    )
                ) as response:
                    logger.info(
                        f"Stream response received for {self.stream_id}, "
                        f"status: {response.status_code}, "
                        f"content-type: {response.headers.get('content-type', 'unknown')}"
                    )
                    response.raise_for_status()
                    
                    self.is_connected = True
                    self.connection_start_time = time.time()
                    self.connection_event.set()
                    
                    # Reset retry count on successful connection
                    if self.retry_count > 0:
                        logger.info(
                            f"Stream {self.stream_id} reconnected successfully after {self.retry_count} retries"
                        )
                        self.retry_count = 0
                    
                    # Read and multicast chunks (acexy Copier pattern)
                    last_log_time = time.time()
                    last_data_time = time.time()
                    
                    async for chunk in response.aiter_bytes(chunk_size=self.buffer_size):
                        if not chunk or not self.is_running:
                            break
                        
                        # Update stats
                        self.chunks_received += 1
                        self.bytes_received += len(chunk)
                        self.last_data_time = time.time()
                        last_data_time = time.time()
                        self.healthy = True
                        
                        # Write chunk to ALL clients in parallel (acexy PMultiWriter pattern)
                        await self._multicast_chunk(chunk)
                        
                        # Log progress periodically
                        if self.chunks_received % 1000 == 0:
                            elapsed = time.time() - last_log_time
                            rate = 1000 / elapsed if elapsed > 0 else 0
                            client_count = len(self.clients)
                            logger.debug(
                                f"Stream {self.stream_id}: {self.chunks_received} chunks "
                                f"({self.bytes_received / 1024 / 1024:.1f}MB), "
                                f"{client_count} clients, "
                                f"rate={rate:.1f} chunks/s"
                            )
                            last_log_time = time.time()
                        
                        # Check for empty timeout
                        if time.time() - last_data_time > self.empty_timeout:
                            logger.warning(
                                f"Empty timeout reached for {self.stream_id} "
                                f"(no data for {self.empty_timeout}s)"
                            )
                            break
                    
                    logger.info(
                        f"Stream {self.stream_id} ended normally after {self.chunks_received} chunks"
                    )
                    # Normal end - don't retry
                    break
                    
            except httpx.HTTPError as e:
                logger.error(
                    f"HTTP error streaming {self.stream_id} (attempt {self.retry_count + 1}): "
                    f"{type(e).__name__}: {e}"
                )
                self.error = e
                self.healthy = False
                self.is_connected = False
                self.retry_count += 1
                
                # Signal connection event on first failure so wait_for_connection doesn't hang
                if self.retry_count == 1:
                    self.connection_event.set()
                
                # Calculate backoff delay (from dispatcharr)
                if self.retry_count < self.max_retries and self.is_running:
                    backoff_delay = min(2 ** self.retry_count, 10)  # Exponential backoff, max 10s
                    logger.info(
                        f"Retrying stream {self.stream_id} in {backoff_delay}s "
                        f"(attempt {self.retry_count}/{self.max_retries})"
                    )
                    await asyncio.sleep(backoff_delay)
                else:
                    logger.error(
                        f"Max retries ({self.max_retries}) reached for stream {self.stream_id}"
                    )
                    break
                
            except Exception as e:
                logger.error(
                    f"Unexpected error streaming {self.stream_id}: {e}",
                    exc_info=True
                )
                self.error = e
                self.healthy = False
                self.is_connected = False
                self.retry_count += 1
                
                # Signal connection event on first failure
                if self.retry_count == 1:
                    self.connection_event.set()
                
                # Retry with backoff
                if self.retry_count < self.max_retries and self.is_running:
                    backoff_delay = min(2 ** self.retry_count, 10)
                    logger.info(
                        f"Retrying stream {self.stream_id} in {backoff_delay}s after error "
                        f"(attempt {self.retry_count}/{self.max_retries})"
                    )
                    await asyncio.sleep(backoff_delay)
                else:
                    logger.error(
                        f"Max retries ({self.max_retries}) reached for stream {self.stream_id}"
                    )
                    break
            
        # Notify all clients that stream has ended
        await self._notify_all_clients_end()
    
    async def _multicast_chunk(self, chunk: bytes):
        """Multicast chunk to all connected clients in parallel.
        
        This implements acexy's PMultiWriter.Write() pattern:
        - Write to all clients in parallel (concurrent goroutines)
        - Don't wait for slow clients (use queue with maxsize)
        - Collect errors but don't stop on individual failures
        
        Args:
            chunk: Data chunk to send to all clients
        """
        async with self.clients_lock:
            clients_snapshot = list(self.clients.values())
        
        if not clients_snapshot:
            return
        
        # Write to all clients concurrently (acexy pattern)
        tasks = []
        for writer in clients_snapshot:
            task = asyncio.create_task(self._write_to_client(writer, chunk))
            tasks.append(task)
        
        # Wait for all writes to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log errors but don't stop stream
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                writer = clients_snapshot[i]
                logger.warning(
                    f"Error writing to client {writer.client_id} in stream {self.stream_id}: {result}"
                )
    
    async def _write_to_client(self, writer: ClientWriter, chunk: bytes):
        """Write chunk to a single client writer.
        
        Args:
            writer: Client writer
            chunk: Data chunk to write
        """
        try:
            # Try to put chunk in queue (non-blocking)
            # If queue is full, skip this chunk for this client (slow client protection)
            try:
                writer.queue.put_nowait(chunk)
                writer.bytes_sent += len(chunk)
                writer.chunks_sent += 1
                writer.last_write_time = time.time()
            except asyncio.QueueFull:
                logger.warning(
                    f"Queue full for client {writer.client_id}, dropping chunk "
                    f"(slow client protection)"
                )
        except Exception as e:
            logger.error(
                f"Error writing to client {writer.client_id} queue: {e}"
            )
            raise
    
    async def _notify_all_clients_end(self):
        """Notify all clients that stream has ended."""
        async with self.clients_lock:
            for writer in self.clients.values():
                try:
                    await writer.queue.put(None)  # Sentinel value for end
                except:
                    pass
    
    def is_healthy(self) -> bool:
        """Check if stream manager is healthy."""
        if not self.healthy:
            return False
        
        # Check if we've received data recently
        if self.last_data_time:
            elapsed = time.time() - self.last_data_time
            if elapsed > 30:  # No data for 30 seconds
                logger.warning(
                    f"AcexyStreamManager for {self.stream_id} unhealthy: "
                    f"no data for {elapsed:.1f}s"
                )
                return False
        
        return True
    
    async def _health_monitor_loop(self):
        """Health monitoring loop (from dispatcharr pattern).
        
        Monitors stream health and triggers recovery if needed.
        """
        logger.info(f"Started health monitor for stream {self.stream_id}")
        
        try:
            while self.is_running:
                await asyncio.sleep(5.0)  # Check every 5 seconds
                
                if not self.is_connected:
                    # Not connected yet or connection lost
                    continue
                
                # Check if we're receiving data
                if self.last_data_time:
                    elapsed = time.time() - self.last_data_time
                    
                    if elapsed > 30:  # No data for 30 seconds
                        logger.warning(
                            f"Health monitor: No data for {elapsed:.1f}s on stream {self.stream_id}"
                        )
                        self.healthy = False
                        
                        # If stream has been stable for a while before this issue,
                        # the connection might be temporarily disrupted
                        # The retry logic in _stream_loop will handle reconnection
                    else:
                        # Data is flowing normally
                        if not self.healthy:
                            logger.info(
                                f"Health monitor: Stream {self.stream_id} recovered"
                            )
                            self.healthy = True
                
        except asyncio.CancelledError:
            logger.info(f"Health monitor stopped for stream {self.stream_id}")
        except Exception as e:
            logger.error(
                f"Error in health monitor for stream {self.stream_id}: {e}",
                exc_info=True
            )
