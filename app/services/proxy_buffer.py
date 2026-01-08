"""
Stream Buffer for proxy sessions.

Buffers stream data so multiple clients can read from the same stream.
"""

import asyncio
import logging
from typing import List
from collections import deque

logger = logging.getLogger(__name__)


class ProxyBuffer:
    """Ring buffer for multiplexing stream data to multiple clients."""
    
    def __init__(self, max_chunks: int = 1000):
        """
        Initialize proxy buffer.
        
        Args:
            max_chunks: Maximum number of chunks to keep in buffer (default 1000)
        """
        self._chunks: deque = deque(maxlen=max_chunks)
        self._lock = asyncio.Lock()
        self._failed = False
        self._chunk_available = asyncio.Event()
    
    async def add_chunk(self, chunk: bytes):
        """Add a chunk to the buffer."""
        if not chunk:
            return
        
        async with self._lock:
            self._chunks.append(chunk)
            # Signal that new data is available
            self._chunk_available.set()
            self._chunk_available.clear()
    
    async def get_chunks(self, start_index: int) -> List[bytes]:
        """
        Get chunks from the buffer starting at the given index.
        
        Args:
            start_index: Index to start reading from
            
        Returns:
            List of chunks starting from start_index
        """
        async with self._lock:
            # Calculate current buffer position
            current_index = len(self._chunks)
            
            # If start_index is behind, just return what we have
            if start_index < 0:
                start_index = 0
            
            # If start_index is beyond current, return empty
            if start_index >= current_index:
                return []
            
            # Return chunks from start_index to end
            return list(self._chunks)[start_index:]
    
    async def has_data(self, chunk_index: int) -> bool:
        """Check if buffer has data beyond the given index."""
        async with self._lock:
            return chunk_index < len(self._chunks)
    
    async def wait_for_data(self, timeout: float = 1.0) -> bool:
        """
        Wait for new data to be available.
        
        Args:
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if data became available, False on timeout
        """
        try:
            await asyncio.wait_for(self._chunk_available.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
    
    def mark_failed(self):
        """Mark buffer as failed (stream error)."""
        self._failed = True
    
    def is_failed(self) -> bool:
        """Check if buffer is marked as failed."""
        return self._failed
    
    def clear(self):
        """Clear all buffered data."""
        self._chunks.clear()
        self._failed = False
