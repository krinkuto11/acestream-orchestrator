import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

def recover_stream(stream_id: str, dead_vpn: Optional[str] = None):
    """
    Background recovery task orchestration.
    Queries the global state to decouple from dead engines, finds a healthy replacement, 
    and issues a migration payload to the proxy for Perfect Splice recovery.
    """
    def _recovery_task():
        try:
            # Brief delay to allow state changes to synchronize globally
            time.sleep(1.0)
            
            from .state import state
            from ..proxy.manager import ProxyManager
            from ..services.engine_selection import select_best_engine
            
            stream_state = state.get_stream(stream_id)
            if not stream_state:
                logger.warning(f"Recovery failed: Stream {stream_id} not found in state.")
                return
                
            if stream_state.status != "pending_failover":
                logger.info(f"Stream {stream_id} is no longer pending failover (status: {stream_state.status}). Aborting recovery.")
                return

            dead_container_id = stream_state.container_id
            
            # Lookup the dead engine to find its VPN for blacklisting
            dead_engine = state.get_engine(dead_container_id) if dead_container_id else None
            resolved_dead_vpn = dead_vpn or (dead_engine.vpn_container if dead_engine else None)
            
            logger.info(f"Initiating Control Plane recovery for stream {stream_id} (previous engine: {dead_container_id}, previous VPN: {resolved_dead_vpn or 'N/A'})")

            # Try to select a new engine, heavily penalizing the dead one
            penalties = {dead_container_id: 999} if dead_container_id else {}
            new_engine = None
            max_retries = 15
            
            for attempt in range(max_retries):
                try:
                    new_engine, _ = select_best_engine(
                        additional_load_by_engine=penalties,
                        exclude_vpn=resolved_dead_vpn
                    )
                except Exception as e:
                    logger.warning(f"Engine selection failed for stream {stream_id} (attempt {attempt + 1}/{max_retries}): {e}. Waiting for capacity...")
                    time.sleep(2.0)
                    continue
                
                logger.info(f"Selected new engine {new_engine.container_id} for stream {stream_id}. Triggering migration API...")
                
                # Instruct the proxy to hot-swap to the new engine via the ProxyManager facade
                migration_result = ProxyManager.migrate_stream(stream_state.key, new_engine)

                if migration_result.get("migrated"):
                    logger.info(f"Successfully migrated stream {stream_id} to engine {new_engine.container_id}.")
                    
                    # ProxyManager handles calling state.reassign_active_streams_to_engine_by_key
                    # automatically so we just need to ensure the stream status is flipped back to started.
                    with state._lock:
                        st = state.streams.get(stream_id)
                        if st:
                            st.status = "started"
                            # DB synchronization will occur async or on next stats payload
                            def db_work(session):
                                from ..models.db_models import StreamRow
                                row = session.get(StreamRow, stream_id)
                                if row:
                                    row.status = "started"
                            state._enqueue_db_task(db_work)
                    return  # Success, exit the thread
                else:
                    reason = migration_result.get("reason", "unknown")
                    logger.warning(f"Migration failed for stream {stream_id} on engine {new_engine.container_id} (attempt {attempt + 1}/{max_retries}): {reason}. Retrying...")
                    # Apply a soft penalty to the engine so we might explore others if available, 
                    # but we can still re-select it if it's the only one (waiting for it to boot).
                    penalties[new_engine.container_id] = penalties.get(new_engine.container_id, 0) + 1
                    time.sleep(2.0)

            # If we exhaust the loop:
            logger.error(f"Exhausted all retries waiting for a replacement engine for stream {stream_id}.")
            from ..models.schemas import StreamEndedEvent
            from .internal_events import handle_stream_ended
            handle_stream_ended(StreamEndedEvent(stream_id=stream_id, container_id=dead_container_id, reason="failover_exhausted"))
                
        except Exception as e:
            logger.error(f"Error during stream recovery task for {stream_id}: {e}", exc_info=True)

    thread = threading.Thread(target=_recovery_task, name=f"recovery-{stream_id[:8]}", daemon=True)
    thread.start()
