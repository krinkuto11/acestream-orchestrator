"""
Internal event handlers for stream lifecycle events.

This module provides functions to handle stream started/ended events without
making HTTP requests. This avoids deadlocks when uvicorn runs in single-worker
mode, as the proxy can call these functions directly instead of POSTing to
localhost:8000/events/*.
"""

import logging
from typing import Optional
from ..models.schemas import StreamStartedEvent, StreamEndedEvent, StreamState
from .state import state
from .event_logger import event_logger

logger = logging.getLogger(__name__)


def handle_stream_started(evt: StreamStartedEvent) -> StreamState:
    """
    Handle stream started event internally without HTTP request.
    
    Args:
        evt: Stream started event data
        
    Returns:
        StreamState object with stream ID
    """
    try:
        result = state.on_stream_started(evt)
        
        # Log stream start event
        event_logger.log_event(
            event_type="stream",
            category="started",
            message=f"Stream started: {evt.stream.key_type}={evt.stream.key[:16]}...",
            details={
                "key_type": evt.stream.key_type,
                "key": evt.stream.key,
                "engine_port": evt.engine.port,
                "is_live": bool(evt.session.is_live)
            },
            container_id=evt.container_id,
            stream_id=result.id
        )
        
        logger.debug(f"Internal stream started event handled: stream_id={result.id}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to handle internal stream started event: {e}", exc_info=True)
        raise


def handle_stream_ended(evt: StreamEndedEvent) -> Optional[StreamState]:
    """
    Handle stream ended event internally without HTTP request.
    
    Args:
        evt: Stream ended event data
        
    Returns:
        StreamState object if stream was found, None otherwise
    """
    try:
        st = state.on_stream_ended(evt)
        
        # Log stream end event
        if st:
            event_logger.log_event(
                event_type="stream",
                category="ended",
                message=f"Stream ended: {st.id[:16]}... (reason: {evt.reason or 'unknown'})",
                details={
                    "reason": evt.reason,
                    "key_type": st.key_type,
                    "key": st.key
                },
                container_id=st.container_id,
                stream_id=st.id
            )
            
            logger.debug(f"Internal stream ended event handled: stream_id={st.id}")
        else:
            logger.warning(f"Stream ended event for unknown stream: container_id={evt.container_id}, stream_id={evt.stream_id}")
        
        return st
        
    except Exception as e:
        logger.error(f"Failed to handle internal stream ended event: {e}", exc_info=True)
        raise
