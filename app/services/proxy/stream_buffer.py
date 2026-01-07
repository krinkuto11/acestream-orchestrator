"""Redis-backed buffer management for stream multiplexing"""

import logging
import time
from typing import Optional, List, Tuple
from collections import deque
import redis

from .config import COPY_CHUNK_SIZE, MEMORY_BUFFER_SIZE

logger = logging.getLogger(__name__)

# TS packet size constant
TS_PACKET_SIZE = 188


class StreamBuffer:
    """Manages stream data buffering with Redis storage.
    
    Based on dispatcharr_proxy's StreamBuffer pattern:
    - Stores chunks in Redis with TTL
    - Tracks current write index
    - Allows multiple clients to read at different positions
    - Handles partial TS packets properly
    """
    
    def __init__(self, stream_id: str, redis_client: Optional[redis.Redis] = None):
        """Initialize stream buffer.
        
        Args:
            stream_id: Unique identifier for this stream
            redis_client: Redis client for storage (optional, will use in-memory if None)
        """
        self.stream_id = stream_id
        self.redis_client = redis_client
        self.index = 0  # Current write position
        
        # Redis keys
        self.buffer_index_key = f"ace_proxy:buffer:{stream_id}:index"
        self.buffer_chunk_prefix = f"ace_proxy:buffer:{stream_id}:chunk"
        
        # Configuration
        self.chunk_ttl = 300  # 5 minutes TTL for chunks
        self.target_chunk_size = COPY_CHUNK_SIZE * 5  # ~320KB chunks
        
        # Write buffer for accumulating data
        self._write_buffer = bytearray()
        self._partial_packet = bytearray()
        
        # In-memory fallback if Redis not available
        self._memory_chunks: deque = deque(maxlen=MEMORY_BUFFER_SIZE)  # Keep last N chunks in memory
        
        # Initialize from Redis if available
        if self.redis_client:
            try:
                current_index = self.redis_client.get(self.buffer_index_key)
                if current_index:
                    self.index = int(current_index)
                    logger.info(f"Initialized buffer for {stream_id} from Redis with index {self.index}")
            except Exception as e:
                logger.error(f"Error initializing buffer from Redis: {e}")
    
    def add_chunk(self, chunk: bytes) -> bool:
        """Add data to buffer with TS packet alignment.
        
        Args:
            chunk: Raw data to add
            
        Returns:
            True if successful, False otherwise
        """
        if not chunk:
            return False
        
        try:
            # Combine with any previous partial packet
            combined_data = bytearray(self._partial_packet) + bytearray(chunk)
            
            # Calculate complete packets
            complete_packets_size = (len(combined_data) // TS_PACKET_SIZE) * TS_PACKET_SIZE
            
            if complete_packets_size == 0:
                # Not enough data for a complete packet
                self._partial_packet = combined_data
                return True
            
            # Split into complete packets and remainder
            complete_packets = combined_data[:complete_packets_size]
            self._partial_packet = combined_data[complete_packets_size:]
            
            # Add completed packets to write buffer
            self._write_buffer.extend(complete_packets)
            
            # Write to storage when we have enough data
            writes_done = 0
            while len(self._write_buffer) >= self.target_chunk_size:
                # Extract a full chunk
                chunk_data = bytes(self._write_buffer[:self.target_chunk_size])
                self._write_buffer = self._write_buffer[self.target_chunk_size:]
                
                # Increment index and write
                self.index += 1
                
                if self.redis_client:
                    # Write to Redis
                    try:
                        chunk_key = f"{self.buffer_chunk_prefix}:{self.index}"
                        self.redis_client.setex(chunk_key, self.chunk_ttl, chunk_data)
                        self.redis_client.set(self.buffer_index_key, self.index)
                        writes_done += 1
                    except Exception as e:
                        logger.error(f"Error writing to Redis: {e}")
                        # Fall back to memory storage
                        self._memory_chunks.append((self.index, chunk_data))
                else:
                    # Store in memory
                    self._memory_chunks.append((self.index, chunk_data))
                    writes_done += 1
            
            if writes_done > 0:
                logger.debug(
                    f"Added {writes_done} chunks to buffer for {self.stream_id} "
                    f"at index {self.index}"
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding chunk to buffer: {e}")
            return False
    
    def get_chunk(self, index: int) -> Optional[bytes]:
        """Get a specific chunk by index.
        
        Args:
            index: Chunk index to retrieve
            
        Returns:
            Chunk data if available, None otherwise
        """
        if index <= 0 or index > self.index:
            return None
        
        try:
            if self.redis_client:
                # Try to get from Redis
                chunk_key = f"{self.buffer_chunk_prefix}:{index}"
                data = self.redis_client.get(chunk_key)
                if data:
                    return data
            
            # Fall back to memory
            for mem_index, mem_data in self._memory_chunks:
                if mem_index == index:
                    return mem_data
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting chunk {index}: {e}")
            return None
    
    def get_chunks_from(self, start_index: int, count: int = 10) -> Tuple[List[bytes], int]:
        """Get multiple chunks starting from an index.
        
        Args:
            start_index: Starting index
            count: Maximum number of chunks to retrieve
            
        Returns:
            Tuple of (chunks, next_index)
        """
        chunks = []
        current_index = start_index
        
        for i in range(count):
            chunk = self.get_chunk(current_index)
            if chunk:
                chunks.append(chunk)
                current_index += 1
            else:
                break
        
        return chunks, current_index
    
    def cleanup(self):
        """Clean up buffer resources."""
        if self.redis_client:
            try:
                # Delete index key
                self.redis_client.delete(self.buffer_index_key)
                
                # Delete all chunk keys (scan and delete)
                pattern = f"{self.buffer_chunk_prefix}:*"
                cursor = 0
                while True:
                    cursor, keys = self.redis_client.scan(cursor, match=pattern, count=100)
                    if keys:
                        self.redis_client.delete(*keys)
                    if cursor == 0:
                        break
                
                logger.info(f"Cleaned up Redis buffer for {self.stream_id}")
            except Exception as e:
                logger.error(f"Error cleaning up Redis buffer: {e}")
        
        # Clear memory
        self._memory_chunks.clear()
        self._write_buffer.clear()
        self._partial_packet.clear()
