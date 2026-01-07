"""Acexy-style stream generator - reads from client queue and yields to HTTP response.

Based on acexy's pattern where each client gets data from a shared multiwriter.
This is fundamentally different from the buffer-based approach - clients receive
data in real-time as it arrives, not from historical buffer.
"""

import asyncio
import logging
import time
from typing import AsyncIterator, Optional
from asyncio import Queue

logger = logging.getLogger(__name__)


class AcexyStreamGenerator:
    """Generates stream data for a client using acexy's queue-based pattern.
    
    In acexy pattern:
    - Client subscribes to multiwriter (gets a queue)
    - Chunks are pushed to queue as they arrive from source
    - Client reads from queue and yields to HTTP response
    - No historical buffer - real-time only
    """
    
    def __init__(
        self,
        stream_id: str,
        client_id: str,
        queue: Queue,
    ):
        """Initialize stream generator.
        
        Args:
            stream_id: Unique stream identifier
            client_id: Unique client identifier
            queue: Queue to read chunks from (provided by AcexyStreamManager)
        """
        self.stream_id = stream_id
        self.client_id = client_id
        self.queue = queue
        
        # Stats
        self.bytes_sent = 0
        self.chunks_sent = 0
        self.start_time: Optional[float] = None
    
    async def generate(self) -> AsyncIterator[bytes]:
        """Generate stream data for client by reading from queue.
        
        Yields:
            Chunks of stream data
        """
        self.start_time = time.time()
        last_log_time = time.time()
        
        logger.info(
            f"[{self.client_id}] Starting acexy-style stream generation for {self.stream_id}"
        )
        
        try:
            while True:
                try:
                    # Wait for next chunk from queue
                    # Use timeout to periodically check if we should still be running
                    chunk = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                    
                    # None is sentinel value indicating stream end
                    if chunk is None:
                        logger.info(
                            f"[{self.client_id}] Stream {self.stream_id} ended (received sentinel)"
                        )
                        break
                    
                    # Yield chunk to client
                    yield chunk
                    self.bytes_sent += len(chunk)
                    self.chunks_sent += 1
                    
                    # Mark task as done
                    self.queue.task_done()
                    
                    # Log stats periodically
                    now = time.time()
                    if now - last_log_time >= 10.0:  # Every 10 seconds
                        elapsed = now - self.start_time
                        rate = self.bytes_sent / elapsed / 1024 if elapsed > 0 else 0
                        logger.debug(
                            f"[{self.client_id}] Stats: {self.chunks_sent} chunks, "
                            f"{self.bytes_sent / 1024:.1f} KB, {rate:.1f} KB/s"
                        )
                        last_log_time = now
                    
                except asyncio.TimeoutError:
                    # No chunk received in timeout period, continue waiting
                    # This is normal - stream might be buffering or slow
                    continue
                    
        except asyncio.CancelledError:
            logger.info(f"[{self.client_id}] Stream generation cancelled for {self.stream_id}")
            raise
            
        except Exception as e:
            logger.error(
                f"[{self.client_id}] Error generating stream for {self.stream_id}: {e}",
                exc_info=True
            )
            raise
            
        finally:
            elapsed = time.time() - self.start_time if self.start_time else 0
            rate = self.bytes_sent / elapsed / 1024 if elapsed > 0 else 0
            logger.info(
                f"[{self.client_id}] Stream ended for {self.stream_id}: "
                f"{self.chunks_sent} chunks, {self.bytes_sent / 1024:.1f} KB, "
                f"{rate:.1f} KB/s over {elapsed:.1f}s"
            )
