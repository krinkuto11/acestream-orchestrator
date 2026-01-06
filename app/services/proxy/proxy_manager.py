"""Main proxy manager for AceStream streaming"""

import asyncio
import logging
from typing import Dict, Optional, Set
from uuid import uuid4
import time

from .stream_session import StreamSession
from .engine_selector import EngineSelector
from .config import (
    STREAM_IDLE_TIMEOUT,
    SESSION_CLEANUP_INTERVAL,
    ENGINE_SELECTION_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Import state instance for stream tracking (avoid circular import by importing in methods)
_state_instance = None

def get_state():
    """Get state instance (lazy load to avoid circular import)."""
    global _state_instance
    if _state_instance is None:
        from app.services.state import state
        _state_instance = state
    return _state_instance


class ProxyManager:
    """Manages all proxy stream sessions.
    
    Responsibilities:
    - Session lifecycle (create, track, cleanup)
    - Engine selection and provisioning
    - Client multiplexing
    - Automatic cleanup of idle sessions
    """
    
    _instance: Optional["ProxyManager"] = None
    
    def __init__(self):
        # Active sessions by stream_id (ace_id)
        self.sessions: Dict[str, StreamSession] = {}
        self.sessions_lock = asyncio.Lock()
        
        # Engine selector
        self.engine_selector = EngineSelector()
        
        # Background tasks
        self.cleanup_task: Optional[asyncio.Task] = None
        self.running = False
        
    @classmethod
    def get_instance(cls) -> "ProxyManager":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = ProxyManager()
        return cls._instance
    
    async def start(self):
        """Start the proxy manager and background tasks."""
        if self.running:
            logger.warning("ProxyManager already running")
            return
        
        self.running = True
        
        # Start cleanup task
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        logger.info("ProxyManager started")
    
    async def stop(self):
        """Stop the proxy manager and cleanup all sessions."""
        if not self.running:
            return
        
        self.running = False
        
        # Cancel cleanup task
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Cleanup all active sessions
        async with self.sessions_lock:
            sessions_to_cleanup = list(self.sessions.items())
        
        for ace_id, session in sessions_to_cleanup:
            try:
                await session.cleanup()
                
                # Fire stream ended event
                try:
                    from app.models.schemas import StreamEndedEvent
                    
                    state = get_state()
                    stream = state.get_stream(ace_id)
                    
                    if stream:
                        event = StreamEndedEvent(
                            container_id=session.container_id,
                            stream_id=stream.id,
                            reason="proxy_manager_stopped"
                        )
                        state.on_stream_ended(event)
                except Exception as e:
                    logger.error(f"Failed to mark stream {ace_id} as ended: {e}", exc_info=True)
                    
            except Exception as e:
                logger.error(f"Error cleaning up session {session.stream_id}: {e}")
        
        async with self.sessions_lock:
            self.sessions.clear()
        
        logger.info("ProxyManager stopped")
    
    async def get_or_create_session(self, ace_id: str) -> Optional[StreamSession]:
        """Get existing session or create a new one.
        
        Args:
            ace_id: AceStream content ID (infohash or content_id)
            
        Returns:
            StreamSession if successful, None otherwise
        """
        # Check if session already exists
        async with self.sessions_lock:
            if ace_id in self.sessions:
                session = self.sessions[ace_id]
                if session.is_active:
                    logger.info(f"Reusing existing session for {ace_id}")
                    return session
                else:
                    # Session exists but is not active, remove it
                    logger.info(f"Removing inactive session for {ace_id}")
                    del self.sessions[ace_id]
        
        # Create new session
        return await self._create_session(ace_id)
    
    async def _create_session(self, ace_id: str) -> Optional[StreamSession]:
        """Create a new stream session.
        
        Args:
            ace_id: AceStream content ID
            
        Returns:
            StreamSession if successful, None otherwise
        """
        try:
            # Select best engine
            logger.info(f"Selecting engine for stream {ace_id}")
            
            engine = await asyncio.wait_for(
                self.engine_selector.select_best_engine(),
                timeout=ENGINE_SELECTION_TIMEOUT
            )
            
            if not engine:
                logger.error(f"No engine available for stream {ace_id}")
                return None
            
            # Create session
            session = StreamSession(
                stream_id=ace_id,
                ace_id=ace_id,
                engine_host=engine["host"],
                engine_port=engine["port"],
                container_id=engine["container_id"],
            )
            
            # Initialize session
            logger.info(
                f"Initializing session for {ace_id} on engine "
                f"{engine['container_id'][:12]} (forwarded={engine['is_forwarded']})"
            )
            
            if not await session.initialize():
                logger.error(f"Failed to initialize session for {ace_id}: {session.error}")
                await session.cleanup()
                return None
            
            # Store session
            async with self.sessions_lock:
                self.sessions[ace_id] = session
            
            # Track stream in state database
            try:
                from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo
                
                # Determine key type (assume infohash if 40 chars hex, else content_id)
                key_type = "infohash" if len(ace_id) == 40 and all(c in '0123456789abcdefABCDEF' for c in ace_id) else "content_id"
                
                event = StreamStartedEvent(
                    container_id=session.container_id,
                    engine=EngineAddress(
                        host=session.engine_host,
                        port=session.engine_port
                    ),
                    stream=StreamKey(
                        key_type=key_type,
                        key=ace_id
                    ),
                    session=SessionInfo(
                        playback_session_id=session.playback_session_id,
                        stat_url=session.stat_url,
                        command_url=session.command_url,
                        is_live=1 if session.is_live else 0
                    )
                )
                
                state = get_state()
                state.on_stream_started(event)
                logger.info(f"Tracked proxy stream {ace_id} in state database")
            except Exception as e:
                logger.error(f"Failed to track stream {ace_id} in state database: {e}", exc_info=True)
                # Don't fail the session creation if tracking fails
            
            logger.info(f"Session created for {ace_id} with {session.playback_session_id}")
            return session
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout selecting engine for {ace_id}")
            return None
        except Exception as e:
            logger.error(f"Error creating session for {ace_id}: {e}", exc_info=True)
            return None
    
    async def add_client(self, ace_id: str, client_id: str) -> bool:
        """Add a client to a stream session.
        
        Args:
            ace_id: AceStream content ID
            client_id: Unique client identifier
            
        Returns:
            True if client added successfully, False otherwise
        """
        async with self.sessions_lock:
            session = self.sessions.get(ace_id)
        
        if not session:
            logger.warning(f"Cannot add client {client_id}: session {ace_id} not found")
            return False
        
        await session.client_manager.add_client(client_id)
        return True
    
    async def remove_client(self, ace_id: str, client_id: str) -> int:
        """Remove a client from a stream session.
        
        Args:
            ace_id: AceStream content ID
            client_id: Unique client identifier
            
        Returns:
            Number of remaining clients, or -1 if session not found
        """
        async with self.sessions_lock:
            session = self.sessions.get(ace_id)
        
        if not session:
            logger.warning(f"Cannot remove client {client_id}: session {ace_id} not found")
            return -1
        
        return await session.client_manager.remove_client(client_id)
    
    async def _cleanup_loop(self):
        """Background task to cleanup idle sessions."""
        logger.info("Starting proxy cleanup loop")
        
        while self.running:
            try:
                await asyncio.sleep(SESSION_CLEANUP_INTERVAL)
                await self._cleanup_idle_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}", exc_info=True)
        
        logger.info("Proxy cleanup loop stopped")
    
    async def _cleanup_idle_sessions(self):
        """Clean up sessions with no clients and idle for too long."""
        to_cleanup = []
        
        async with self.sessions_lock:
            for ace_id, session in list(self.sessions.items()):
                # Check if session has no clients and is idle
                if not session.client_manager.has_clients():
                    idle_time = session.client_manager.get_idle_time()
                    if idle_time > STREAM_IDLE_TIMEOUT:
                        logger.info(
                            f"Cleaning up idle session {ace_id} "
                            f"(idle for {idle_time:.1f}s)"
                        )
                        to_cleanup.append((ace_id, session))
        
        # Cleanup outside the lock
        for ace_id, session in to_cleanup:
            try:
                await session.cleanup()
                async with self.sessions_lock:
                    self.sessions.pop(ace_id, None)
                
                # Fire stream ended event
                try:
                    from app.models.schemas import StreamEndedEvent
                    
                    # Find stream_id in state database
                    state = get_state()
                    stream = state.get_stream(ace_id)
                    
                    if stream:
                        event = StreamEndedEvent(
                            container_id=session.container_id,
                            stream_id=stream.id,
                            reason="idle_timeout"
                        )
                        state.on_stream_ended(event)
                        logger.info(f"Marked proxy stream {ace_id} as ended in state database")
                except Exception as e:
                    logger.error(f"Failed to mark stream {ace_id} as ended: {e}", exc_info=True)
                    
            except Exception as e:
                logger.error(f"Error cleaning up session {ace_id}: {e}")
        
        if to_cleanup:
            logger.info(f"Cleaned up {len(to_cleanup)} idle sessions")
    
    async def get_status(self) -> dict:
        """Get proxy manager status.
        
        Returns:
            Dictionary with proxy status information
        """
        async with self.sessions_lock:
            sessions_info = [s.get_info() for s in self.sessions.values()]
            active_sessions = sum(1 for s in self.sessions.values() if s.is_active)
            total_clients = sum(s.client_manager.get_client_count() for s in self.sessions.values())
        
        return {
            "running": self.running,
            "total_sessions": len(self.sessions),
            "active_sessions": active_sessions,
            "total_clients": total_clients,
            "sessions": sessions_info,
        }
    
    async def get_session_info(self, ace_id: str) -> Optional[dict]:
        """Get info for a specific session.
        
        Args:
            ace_id: AceStream content ID
            
        Returns:
            Session info dict or None if not found
        """
        async with self.sessions_lock:
            session = self.sessions.get(ace_id)
        
        if not session:
            return None
        
        return session.get_info()
