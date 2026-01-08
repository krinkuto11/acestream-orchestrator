"""
Stream Proxy Manager for multiplexing AceStream Engine connections.

This module manages proxy sessions that multiplex multiple clients to single
AceStream engine streams, implementing the following workflow:
1. Client requests stream via /ace/getstream?id=<infohash>
2. ProxyManager selects best engine (prioritizes forwarded, balances load)
3. Engine is queried for stream session via /ace/getstream?format=json&infohash=<infohash>
4. HTTP streamer reads from playback_url and pipes to all connected clients
5. Multiple clients reading same infohash share the same engine stream
"""

import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ProxyManager:
    """Singleton manager for AceStream proxy sessions."""
    
    _instance: Optional['ProxyManager'] = None
    _lock = asyncio.Lock()
    
    def __init__(self):
        # Map of content_id -> ProxySession
        self._sessions: Dict[str, 'ProxySession'] = {}
        self._sessions_lock = asyncio.Lock()
        logger.info("ProxyManager initialized")
    
    @classmethod
    def get_instance(cls) -> 'ProxyManager':
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def get_or_create_session(self, content_id: str) -> Optional['ProxySession']:
        """
        Get existing session or create new one for the given content ID.
        
        Args:
            content_id: AceStream content ID (infohash or content_id)
            
        Returns:
            ProxySession instance or None if unable to create
        """
        async with self._sessions_lock:
            # Check if session already exists
            if content_id in self._sessions:
                session = self._sessions[content_id]
                logger.info(f"Using existing session for content_id={content_id}")
                return session
            
            # Create new session
            from .proxy_session import ProxySession
            try:
                session = ProxySession(content_id)
                await session.initialize()
                self._sessions[content_id] = session
                logger.info(f"Created new session for content_id={content_id}")
                return session
            except Exception as e:
                logger.error(f"Failed to create session for content_id={content_id}: {e}", exc_info=True)
                return None
    
    async def add_client(self, content_id: str, client_id: str) -> bool:
        """
        Add a client to a session.
        
        Args:
            content_id: AceStream content ID
            client_id: Unique client identifier
            
        Returns:
            True if client was added successfully
        """
        async with self._sessions_lock:
            session = self._sessions.get(content_id)
            if session:
                await session.client_manager.add_client(client_id)
                return True
            return False
    
    async def remove_client(self, content_id: str, client_id: str) -> int:
        """
        Remove a client from a session.
        
        Args:
            content_id: AceStream content ID
            client_id: Unique client identifier
            
        Returns:
            Number of remaining clients in the session
        """
        async with self._sessions_lock:
            session = self._sessions.get(content_id)
            if session:
                remaining = await session.client_manager.remove_client(client_id)
                
                # Clean up session if no clients remain
                if remaining == 0:
                    logger.info(f"No clients remaining for content_id={content_id}, scheduling cleanup")
                    # Give a grace period before cleanup
                    asyncio.create_task(self._cleanup_session_delayed(content_id, delay=5))
                
                return remaining
            return 0
    
    async def _cleanup_session_delayed(self, content_id: str, delay: int = 5):
        """Clean up a session after a delay if no clients reconnect."""
        await asyncio.sleep(delay)
        
        async with self._sessions_lock:
            session = self._sessions.get(content_id)
            if session and session.client_manager.get_client_count() == 0:
                logger.info(f"Cleaning up session for content_id={content_id} (no clients for {delay}s)")
                await session.stop()
                del self._sessions[content_id]
    
    async def remove_failed_session(self, content_id: str, reason: str = "unknown"):
        """
        Remove a failed session immediately.
        
        Args:
            content_id: AceStream content ID
            reason: Reason for removal
        """
        async with self._sessions_lock:
            session = self._sessions.get(content_id)
            if session:
                logger.warning(f"Removing failed session for content_id={content_id}: {reason}")
                await session.stop()
                del self._sessions[content_id]
    
    async def get_status(self) -> dict:
        """Get proxy manager status including all sessions."""
        async with self._sessions_lock:
            sessions_info = []
            for content_id, session in self._sessions.items():
                session_info = {
                    "content_id": content_id,
                    "engine_id": session.engine_id,
                    "engine_host": session.engine_host,
                    "engine_port": session.engine_port,
                    "client_count": session.client_manager.get_client_count(),
                    "started_at": session.started_at.isoformat() if session.started_at else None,
                    "playback_url": session.playback_url,
                }
                sessions_info.append(session_info)
            
            return {
                "total_sessions": len(self._sessions),
                "sessions": sessions_info,
            }
    
    async def get_session_info(self, content_id: str) -> Optional[dict]:
        """Get detailed info for a specific session."""
        async with self._sessions_lock:
            session = self._sessions.get(content_id)
            if session:
                return {
                    "content_id": content_id,
                    "engine_id": session.engine_id,
                    "engine_host": session.engine_host,
                    "engine_port": session.engine_port,
                    "client_count": session.client_manager.get_client_count(),
                    "started_at": session.started_at.isoformat() if session.started_at else None,
                    "playback_url": session.playback_url,
                    "stat_url": session.stat_url,
                    "command_url": session.command_url,
                    "playback_session_id": session.playback_session_id,
                }
            return None
