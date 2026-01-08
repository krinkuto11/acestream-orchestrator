"""
Client Manager for proxy sessions.

Tracks clients connected to each stream session.
"""

import asyncio
import logging
from typing import Set

logger = logging.getLogger(__name__)


class ProxyClientManager:
    """Manages clients for a single proxy session."""
    
    def __init__(self):
        self._clients: Set[str] = set()
        self._lock = asyncio.Lock()
    
    async def add_client(self, client_id: str):
        """Add a client to the session."""
        async with self._lock:
            self._clients.add(client_id)
            logger.debug(f"Client {client_id} added (total: {len(self._clients)})")
    
    async def remove_client(self, client_id: str) -> int:
        """
        Remove a client from the session.
        
        Returns:
            Number of remaining clients
        """
        async with self._lock:
            self._clients.discard(client_id)
            remaining = len(self._clients)
            logger.debug(f"Client {client_id} removed (remaining: {remaining})")
            return remaining
    
    def get_client_count(self) -> int:
        """Get current number of clients."""
        return len(self._clients)
    
    def get_clients(self) -> Set[str]:
        """Get set of all client IDs."""
        return self._clients.copy()
