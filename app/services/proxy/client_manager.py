"""Client connection manager for stream multiplexing"""

import asyncio
import time
import logging
from typing import Set, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class ClientManager:
    """Manages client connections for stream multiplexing.
    
    Multiple clients can connect to the same stream without creating
    duplicate requests to the AceStream engine.
    """

    def __init__(self, stream_id: str):
        self.stream_id = stream_id
        self.clients: Set[str] = set()
        self.lock = asyncio.Lock()
        self.last_activity = time.time()
        
    async def add_client(self, client_id: str) -> int:
        """Add a client to this stream.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            Total number of clients after adding
        """
        async with self.lock:
            self.clients.add(client_id)
            self.last_activity = time.time()
            count = len(self.clients)
            logger.info(f"Client {client_id} added to stream {self.stream_id}. Total clients: {count}")
            return count
    
    async def remove_client(self, client_id: str) -> int:
        """Remove a client from this stream.
        
        Args:
            client_id: Unique identifier for the client
            
        Returns:
            Total number of clients after removal
        """
        async with self.lock:
            self.clients.discard(client_id)
            count = len(self.clients)
            logger.info(f"Client {client_id} removed from stream {self.stream_id}. Remaining clients: {count}")
            return count
    
    def get_client_count(self) -> int:
        """Get the current number of connected clients."""
        return len(self.clients)
    
    def has_clients(self) -> bool:
        """Check if there are any connected clients."""
        return len(self.clients) > 0
    
    def update_activity(self):
        """Update the last activity timestamp."""
        self.last_activity = time.time()
    
    def get_idle_time(self) -> float:
        """Get the time in seconds since last activity."""
        return time.time() - self.last_activity
    
    def get_client_ids(self) -> Set[str]:
        """Get a copy of all client IDs."""
        return self.clients.copy()
