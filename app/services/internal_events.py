"""
Internal event handlers for stream lifecycle events.

This module provides functions to handle stream started/ended events without
making HTTP requests. This avoids deadlocks when uvicorn runs in single-worker
mode, as the proxy can call these functions directly instead of POSTing to
localhost:8000/events/*.

SECURITY NOTE: These functions do NOT require API key authentication because they
are internal to the orchestrator process and only called by trusted proxy threads.
External access to stream events still requires API key authentication via the
HTTP endpoints in main.py (/events/stream_started, /events/stream_ended).
"""

import logging
from typing import Optional
from ..models.schemas import StreamStartedEvent, StreamEndedEvent, StreamState, StreamDataPlaneFailedEvent
from .state import state
from .recovery import recover_stream
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
        # Commit pending reservation
        try:
            from app.proxy.manager import ProxyManager
            redis = ProxyManager.get_instance().redis_client
            if redis and evt.container_id:
                pending_key = f"ace_proxy:engine:{evt.container_id}:pending"
                decr_script = """
                local current = redis.call('GET', KEYS[1])
                if current and tonumber(current) > 0 then
                    return redis.call('DECR', KEYS[1])
                else
                    return 0
                end
                """
                redis.eval(decr_script, 1, pending_key)
                logger.debug(f"Committed pending reservation for engine {evt.container_id[:12]}")
        except Exception as e:
            logger.warning(f"Failed to commit pending reservation for engine {evt.container_id[:12]}: {e}")

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

def handle_stream_data_plane_failed(evt: StreamDataPlaneFailedEvent) -> Optional[StreamState]:
    """
    Handle data plane failure reported by the proxy and trigger Control Plane recovery.
    """
    try:
        stream_id = evt.stream_id
        
        with state._lock:
            st = state.streams.get(stream_id)
            if not st:
                logger.warning(f"Data plane failed event for unknown stream: {stream_id}")
                return None
            
            if st.status != "pending_failover":
                st.status = "pending_failover"
                
                def db_work(session):
                    from ..models.db_models import StreamRow
                    row = session.get(StreamRow, stream_id)
                    if row:
                        row.status = "pending_failover"
                state._enqueue_db_task(db_work)
                
                logger.info(f"Stream {stream_id} transitioned to pending_failover (reason: {evt.reason})")
                
                # Trigger background recovery task
                dead_vpn = None
                dead_engine = state.engines.get(st.container_id) if st.container_id else None
                if dead_engine:
                    dead_vpn = dead_engine.vpn_container
                recover_stream(stream_id, dead_vpn=dead_vpn)
                
                return st
            else:
                logger.debug(f"Stream {stream_id} is already in pending_failover state.")
                return st
                
    except Exception as e:
        logger.error(f"Failed to handle stream data plane failed event: {e}", exc_info=True)
        raise
