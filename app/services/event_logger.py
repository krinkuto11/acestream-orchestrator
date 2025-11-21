"""
Event logging service for tracking significant application events.
These are not debug logs, but rather important operational events
that users should be able to review for transparency and traceability.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Literal
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..models.db_models import EventRow
from ..services.db import get_session

logger = logging.getLogger(__name__)

EventType = Literal["engine", "stream", "vpn", "health", "system"]


class EventLogger:
    """Service for logging and retrieving application events."""
    
    # Maximum number of events to keep in database
    MAX_EVENTS = 10000
    
    # Age threshold for automatic cleanup (days)
    MAX_AGE_DAYS = 30
    
    def log_event(
        self,
        event_type: EventType,
        category: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        container_id: Optional[str] = None,
        stream_id: Optional[str] = None
    ) -> int:
        """
        Log a significant application event.
        
        Args:
            event_type: Type of event (engine, stream, vpn, health, system)
            category: Category within type (created, deleted, started, ended, etc.)
            message: Human-readable message
            details: Additional structured data
            container_id: Associated container ID if applicable
            stream_id: Associated stream ID if applicable
            
        Returns:
            Event ID
        """
        try:
            with get_session() as session:
                event = EventRow(
                    timestamp=datetime.now(timezone.utc),
                    event_type=event_type,
                    category=category,
                    message=message,
                    details=details or {},
                    container_id=container_id,
                    stream_id=stream_id
                )
                session.add(event)
                session.commit()
                session.refresh(event)
                
                # Async cleanup in background if needed
                self._cleanup_old_events_if_needed(session)
                
                return event.id
        except Exception as e:
            logger.error(f"Failed to log event: {e}", exc_info=True)
            return -1
    
    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[EventType] = None,
        category: Optional[str] = None,
        container_id: Optional[str] = None,
        stream_id: Optional[str] = None,
        since: Optional[datetime] = None
    ) -> List[EventRow]:
        """
        Retrieve events with optional filtering.
        
        Args:
            limit: Maximum number of events to return
            offset: Pagination offset
            event_type: Filter by event type
            category: Filter by category
            container_id: Filter by container ID
            stream_id: Filter by stream ID
            since: Only return events after this timestamp
            
        Returns:
            List of event rows
        """
        try:
            with get_session() as session:
                query = session.query(EventRow)
                
                if event_type:
                    query = query.filter(EventRow.event_type == event_type)
                if category:
                    query = query.filter(EventRow.category == category)
                if container_id:
                    query = query.filter(EventRow.container_id == container_id)
                if stream_id:
                    query = query.filter(EventRow.stream_id == stream_id)
                if since:
                    query = query.filter(EventRow.timestamp >= since)
                
                query = query.order_by(desc(EventRow.timestamp))
                query = query.limit(limit).offset(offset)
                
                return query.all()
        except Exception as e:
            logger.error(f"Failed to retrieve events: {e}", exc_info=True)
            return []
    
    def get_event_count(
        self,
        event_type: Optional[EventType] = None,
        category: Optional[str] = None,
        since: Optional[datetime] = None
    ) -> int:
        """Get count of events matching filters."""
        try:
            with get_session() as session:
                query = session.query(EventRow)
                
                if event_type:
                    query = query.filter(EventRow.event_type == event_type)
                if category:
                    query = query.filter(EventRow.category == category)
                if since:
                    query = query.filter(EventRow.timestamp >= since)
                
                return query.count()
        except Exception as e:
            logger.error(f"Failed to count events: {e}", exc_info=True)
            return 0
    
    def get_event_stats(self) -> Dict[str, Any]:
        """Get statistics about logged events."""
        try:
            with get_session() as session:
                total_count = session.query(EventRow).count()
                
                # Count by type
                type_counts = {}
                for event_type in ["engine", "stream", "vpn", "health", "system"]:
                    count = session.query(EventRow).filter(
                        EventRow.event_type == event_type
                    ).count()
                    type_counts[event_type] = count
                
                # Get oldest and newest
                oldest = session.query(EventRow).order_by(EventRow.timestamp).first()
                newest = session.query(EventRow).order_by(desc(EventRow.timestamp)).first()
                
                return {
                    "total": total_count,
                    "by_type": type_counts,
                    "oldest": oldest.timestamp if oldest else None,
                    "newest": newest.timestamp if newest else None
                }
        except Exception as e:
            logger.error(f"Failed to get event stats: {e}", exc_info=True)
            return {"total": 0, "by_type": {}, "oldest": None, "newest": None}
    
    def _cleanup_old_events_if_needed(self, session: Session):
        """Clean up old events if we exceed limits."""
        try:
            # Check total count
            total = session.query(EventRow).count()
            
            if total > self.MAX_EVENTS:
                # Delete oldest events to bring back to limit
                excess = total - self.MAX_EVENTS
                oldest_events = session.query(EventRow).order_by(
                    EventRow.timestamp
                ).limit(excess).all()
                
                for event in oldest_events:
                    session.delete(event)
                
                logger.info(f"Cleaned up {excess} old events (total exceeded {self.MAX_EVENTS})")
            
            # Also delete events older than MAX_AGE_DAYS
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.MAX_AGE_DAYS)
            old_events = session.query(EventRow).filter(
                EventRow.timestamp < cutoff
            ).all()
            
            if old_events:
                for event in old_events:
                    session.delete(event)
                logger.info(f"Cleaned up {len(old_events)} events older than {self.MAX_AGE_DAYS} days")
            
            session.commit()
        except Exception as e:
            logger.error(f"Failed to cleanup old events: {e}", exc_info=True)
    
    def cleanup_old_events(self, max_age_days: Optional[int] = None):
        """Manually trigger cleanup of old events."""
        try:
            with get_session() as session:
                cutoff = datetime.now(timezone.utc) - timedelta(
                    days=max_age_days if max_age_days is not None else self.MAX_AGE_DAYS
                )
                
                result = session.query(EventRow).filter(
                    EventRow.timestamp < cutoff
                ).delete()
                
                session.commit()
                logger.info(f"Cleaned up {result} events older than {max_age_days or self.MAX_AGE_DAYS} days")
                return result
        except Exception as e:
            logger.error(f"Failed to cleanup old events: {e}", exc_info=True)
            return 0


# Global event logger instance
event_logger = EventLogger()
