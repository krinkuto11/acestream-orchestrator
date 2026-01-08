"""
Looping streams tracker service.

This service tracks AceStream IDs that have been detected as looping.
When a stream is detected as looping (no new data being fed into the network),
its ID is added to this tracker. Acexy proxy can then check this list before
selecting an engine and return an error to prevent users from trying to play
looping streams.

The retention time for looping stream entries is configurable:
- If retention_minutes is 0 or None: streams remain indefinitely
- Otherwise: streams are automatically removed after the configured time
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set
from threading import Lock

logger = logging.getLogger(__name__)


class LoopingStreamsTracker:
    """
    Tracks AceStream IDs that have been detected as looping.
    
    Thread-safe implementation that supports both indefinite and time-limited retention.
    """
    
    def __init__(self):
        self._looping_streams: Dict[str, datetime] = {}  # stream_id -> detection_time
        self._lock = Lock()
        self._retention_minutes: Optional[int] = None  # None or 0 = indefinite
        self._cleanup_task = None
        self._stop_event = asyncio.Event()
    
    def set_retention_minutes(self, minutes: Optional[int]):
        """
        Set the retention time for looping stream entries.
        
        Args:
            minutes: Retention time in minutes. None or 0 means indefinite retention.
        """
        with self._lock:
            self._retention_minutes = minutes if (minutes is not None and minutes > 0) else None
            logger.info(f"Looping streams retention set to: {minutes if self._retention_minutes else 'indefinite'} minutes")
    
    def get_retention_minutes(self) -> Optional[int]:
        """Get the current retention time in minutes."""
        with self._lock:
            return self._retention_minutes
    
    def add_looping_stream(self, stream_id: str):
        """
        Add a stream ID to the looping streams list.
        
        Args:
            stream_id: The AceStream content ID (infohash or content_id)
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            if stream_id not in self._looping_streams:
                self._looping_streams[stream_id] = now
                logger.info(f"Added looping stream: {stream_id}")
            else:
                # Update timestamp if already exists
                self._looping_streams[stream_id] = now
                logger.debug(f"Updated looping stream timestamp: {stream_id}")
    
    def remove_looping_stream(self, stream_id: str) -> bool:
        """
        Remove a stream ID from the looping streams list.
        
        Args:
            stream_id: The AceStream content ID to remove
            
        Returns:
            True if the stream was removed, False if it wasn't in the list
        """
        with self._lock:
            if stream_id in self._looping_streams:
                del self._looping_streams[stream_id]
                logger.info(f"Removed looping stream: {stream_id}")
                return True
            return False
    
    def is_looping(self, stream_id: str) -> bool:
        """
        Check if a stream ID is in the looping streams list.
        
        Args:
            stream_id: The AceStream content ID to check
            
        Returns:
            True if the stream is marked as looping, False otherwise
        """
        with self._lock:
            return stream_id in self._looping_streams
    
    def get_looping_streams(self) -> Dict[str, str]:
        """
        Get all looping streams with their detection times.
        
        Returns:
            Dict mapping stream_id to ISO-formatted detection time
        """
        with self._lock:
            return {
                stream_id: detection_time.isoformat()
                for stream_id, detection_time in self._looping_streams.items()
            }
    
    def get_looping_stream_ids(self) -> Set[str]:
        """
        Get just the stream IDs without timestamps.
        
        Returns:
            Set of stream IDs that are marked as looping
        """
        with self._lock:
            return set(self._looping_streams.keys())
    
    def clear_all(self):
        """Clear all looping streams from the tracker."""
        with self._lock:
            count = len(self._looping_streams)
            self._looping_streams.clear()
            logger.info(f"Cleared all looping streams (removed {count} entries)")
    
    def _cleanup_expired(self):
        """Remove expired entries based on retention time (internal use)."""
        with self._lock:
            if self._retention_minutes is None:
                # Indefinite retention, nothing to clean up
                return
            
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(minutes=self._retention_minutes)
            
            # Find expired entries
            expired = [
                stream_id for stream_id, detection_time in self._looping_streams.items()
                if detection_time < cutoff
            ]
            
            # Remove expired entries
            for stream_id in expired:
                del self._looping_streams[stream_id]
            
            if expired:
                logger.info(f"Cleaned up {len(expired)} expired looping stream entries")
    
    async def start(self):
        """Start the background cleanup task (if retention is configured)."""
        if self._cleanup_task and not self._cleanup_task.done():
            return
        
        self._stop_event.clear()
        self._cleanup_task = asyncio.create_task(self._run_cleanup())
        logger.info("Looping streams tracker started")
    
    async def stop(self):
        """Stop the background cleanup task."""
        self._stop_event.set()
        if self._cleanup_task:
            await self._cleanup_task
        logger.info("Looping streams tracker stopped")
    
    async def _run_cleanup(self):
        """Background task to periodically clean up expired entries."""
        while not self._stop_event.is_set():
            try:
                # Clean up expired entries
                self._cleanup_expired()
            except Exception:
                logger.exception("Error in looping streams cleanup")
            
            # Wait 60 seconds before next cleanup
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                pass


# Global instance
looping_streams_tracker = LoopingStreamsTracker()
