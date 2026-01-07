"""Stream generator that reads from buffer and yields to clients"""

import asyncio
import logging
import time
from typing import AsyncIterator, Optional

from .stream_buffer import StreamBuffer

logger = logging.getLogger(__name__)


class StreamGenerator:
    """Generates stream data for clients by reading from buffer.
    
    Based on dispatcharr_proxy's StreamGenerator pattern:
    - Reads from Redis buffer at client's position
    - Handles clients at different positions
    - Sends keepalive packets when waiting
    - Detects and handles disconnections
    """
    
    def __init__(
        self,
        stream_id: str,
        client_id: str,
        buffer: StreamBuffer,
        initial_behind: int = 3,
    ):
        """Initialize stream generator.
        
        Args:
            stream_id: Unique stream identifier
            client_id: Unique client identifier
            buffer: Stream buffer to read from
            initial_behind: How many chunks behind to start (for buffering)
        """
        self.stream_id = stream_id
        self.client_id = client_id
        self.buffer = buffer
        self.initial_behind = initial_behind
        
        # Client position tracking
        self.local_index = 0
        self.consecutive_empty = 0
        
        # Stats
        self.bytes_sent = 0
        self.chunks_sent = 0
        self.start_time: Optional[float] = None
        
    async def generate(self) -> AsyncIterator[bytes]:
        """Generate stream data for client.
        
        Yields:
            Chunks of stream data
        """
        self.start_time = time.time()
        
        # Calculate starting position
        current_buffer_index = self.buffer.index
        self.local_index = max(1, current_buffer_index - self.initial_behind)
        
        logger.info(
            f"[{self.client_id}] Starting stream at index {self.local_index} "
            f"(buffer at {current_buffer_index})"
        )
        
        try:
            while True:
                # Get chunks from buffer
                chunks, next_index = self.buffer.get_chunks_from(self.local_index, count=10)
                
                if chunks:
                    # Send chunks to client
                    for chunk in chunks:
                        yield chunk
                        self.bytes_sent += len(chunk)
                        self.chunks_sent += 1
                    
                    # Update position
                    self.local_index = next_index
                    self.consecutive_empty = 0
                    
                    # Log stats periodically
                    if self.chunks_sent % 100 == 0:
                        elapsed = time.time() - self.start_time
                        rate = self.bytes_sent / elapsed / 1024 if elapsed > 0 else 0
                        logger.debug(
                            f"[{self.client_id}] Stats: {self.chunks_sent} chunks, "
                            f"{self.bytes_sent / 1024:.1f} KB, {rate:.1f} KB/s"
                        )
                    
                else:
                    # No data available yet
                    self.consecutive_empty += 1
                    
                    # Check if we're too far behind (chunks expired)
                    chunks_behind = self.buffer.index - self.local_index
                    if chunks_behind > 50:
                        # Jump forward to stay near buffer head
                        new_index = max(self.local_index, self.buffer.index - self.initial_behind)
                        logger.warning(
                            f"[{self.client_id}] Too far behind ({chunks_behind} chunks), "
                            f"jumping from {self.local_index} to {new_index}"
                        )
                        self.local_index = new_index
                        self.consecutive_empty = 0
                        continue
                    
                    # Send keepalive packet if waiting too long
                    if self.consecutive_empty > 10:
                        keepalive_packet = self._create_keepalive_packet()
                        if keepalive_packet:
                            yield keepalive_packet
                            self.bytes_sent += len(keepalive_packet)
                        self.consecutive_empty = 0
                    
                    # Wait before checking again
                    sleep_time = min(0.1 * self.consecutive_empty, 1.0)
                    await asyncio.sleep(sleep_time)
                    
                    # Check for timeout
                    if self.consecutive_empty > 300:  # 30 seconds with no data
                        logger.warning(
                            f"[{self.client_id}] Timeout: no data for 30 seconds "
                            f"at index {self.local_index}"
                        )
                        break
                
        except asyncio.CancelledError:
            logger.info(f"[{self.client_id}] Stream generation cancelled")
            raise
            
        except Exception as e:
            logger.error(f"[{self.client_id}] Error generating stream: {e}", exc_info=True)
            raise
            
        finally:
            elapsed = time.time() - self.start_time if self.start_time else 0
            logger.info(
                f"[{self.client_id}] Stream ended: {self.chunks_sent} chunks, "
                f"{self.bytes_sent / 1024:.1f} KB in {elapsed:.1f}s"
            )
    
    def _create_keepalive_packet(self) -> Optional[bytes]:
        """Create a keepalive TS packet.
        
        Returns:
            TS packet bytes or None
        """
        # Simple null packet (PID 0x1FFF)
        # Sync byte (0x47) + null PID + rest zeros
        packet = bytearray(188)
        packet[0] = 0x47  # Sync byte
        packet[1] = 0x1F  # PID high byte
        packet[2] = 0xFF  # PID low byte
        return bytes(packet)
