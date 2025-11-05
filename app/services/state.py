from __future__ import annotations
import threading
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone
from ..models.schemas import EngineState, StreamState, StreamStartedEvent, StreamEndedEvent, StreamStatSnapshot
from ..services.db import SessionLocal
from ..models.db_models import EngineRow, StreamRow, StatRow

logger = logging.getLogger(__name__)

class State:
    def __init__(self):
        self._lock = threading.RLock()
        self.engines: Dict[str, EngineState] = {}
        self.streams: Dict[str, StreamState] = {}
        self.stream_stats: Dict[str, List[StreamStatSnapshot]] = {}

    @staticmethod
    def now():
        return datetime.now(timezone.utc)
    
    def _is_redundant_mode(self) -> bool:
        """Check if we're in redundant VPN mode."""
        from ..core.config import cfg
        return (cfg.VPN_MODE == 'redundant' and 
                cfg.GLUETUN_CONTAINER_NAME and 
                cfg.GLUETUN_CONTAINER_NAME_2)

    def on_stream_started(self, evt: StreamStartedEvent) -> StreamState:
        with self._lock:
            # Try to find existing engine using multiple approaches
            eng = None
            if evt.container_id:
                # First try by container_id
                eng = self.engines.get(evt.container_id)

            if not eng:
                # If not found, search for engine with matching host:port
                target_host = evt.engine.host
                target_port = evt.engine.port
                for existing_eng in self.engines.values():
                    if existing_eng.host == target_host and existing_eng.port == target_port:
                        eng = existing_eng
                        break

            # Determine the final key to use for this engine
            if eng:
                # Use existing engine's key
                key = eng.container_id
            else:
                # Create new engine with appropriate key
                key = evt.container_id or f"{evt.engine.host}:{evt.engine.port}"
            
            # Get container name from Docker if we have a container_id
            container_name = None
            if evt.container_id:
                from ..services.inspect import get_container_name
                container_name = get_container_name(evt.container_id)
                # If we can't get the name from Docker, but we have a container_id,
                # use a truncated version of the container_id as a fallback
                if not container_name:
                    container_name = f"container-{evt.container_id[:12]}"
            else:
                # If no container_id provided, use host:port as a descriptive name
                container_name = f"engine-{evt.engine.host}-{evt.engine.port}"
            
            if not eng:
                eng = EngineState(container_id=key, container_name=container_name, host=evt.engine.host, port=evt.engine.port,
                                  labels=evt.labels or {}, forwarded=False, first_seen=self.now(), last_seen=self.now(), streams=[],
                                  health_status="unknown", last_health_check=None, last_stream_usage=self.now(),
                                  last_cache_cleanup=None, cache_size_bytes=None, vpn_container=None)
                self.engines[key] = eng
            else:
                eng.host = evt.engine.host; eng.port = evt.engine.port; eng.last_seen = self.now()
                eng.last_stream_usage = self.now()  # Update last stream usage when stream starts
                if container_name and not eng.container_name:
                    eng.container_name = container_name
                if evt.labels: eng.labels.update(evt.labels)

            stream_id = (evt.labels.get("stream_id") if evt.labels else None) or f"{evt.stream.key}|{evt.session.playback_session_id}"
            st = StreamState(id=stream_id, key_type=evt.stream.key_type, key=evt.stream.key,
                             container_id=key, playback_session_id=evt.session.playback_session_id,
                             stat_url=str(evt.session.stat_url), command_url=str(evt.session.command_url),
                             is_live=bool(evt.session.is_live), started_at=self.now(), status="started")
            self.streams[stream_id] = st
            if stream_id not in eng.streams: eng.streams.append(stream_id)

        with SessionLocal() as s:
            s.merge(EngineRow(engine_key=eng.container_id, container_id=evt.container_id, container_name=container_name,
                              host=eng.host, port=eng.port, labels=eng.labels, forwarded=eng.forwarded, 
                              first_seen=eng.first_seen, last_seen=eng.last_seen, vpn_container=eng.vpn_container))
            s.merge(StreamRow(id=stream_id, engine_key=eng.container_id, key_type=st.key_type, key=st.key,
                              playback_session_id=st.playback_session_id, stat_url=st.stat_url, command_url=st.command_url,
                              is_live=st.is_live, started_at=st.started_at, status=st.status))
            s.commit()
        return st

    def on_stream_ended(self, evt: StreamEndedEvent) -> Optional[StreamState]:
        engine_became_idle = False
        container_id_for_cleanup = None
        stream_id_for_metrics = None
        
        with self._lock:
            st: Optional[StreamState] = None
            if evt.stream_id and evt.stream_id in self.streams:
                st = self.streams[evt.stream_id]
            else:
                for s in reversed(list(self.streams.values())):
                    if s.container_id == (evt.container_id or s.container_id) and s.ended_at is None:
                        st = s; break
            if not st: return None
            st.ended_at = self.now(); st.status = "ended"
            stream_id_for_metrics = st.id
            
            # Remove the stream from the engine's streams list
            eng = self.engines.get(st.container_id)
            if eng and st.id in eng.streams:
                eng.streams.remove(st.id)
                # Check if engine has no more active streams
                if len(eng.streams) == 0:
                    engine_became_idle = True
                    container_id_for_cleanup = st.container_id
                
        try:
            with SessionLocal() as s:
                row = s.get(StreamRow, st.id)
                if row:
                    row.ended_at = st.ended_at; row.status = st.status; s.commit()
        except Exception:
            # Database operation failed, but we can continue since we've updated memory state
            pass
        
        # Clear AceStream cache when engine becomes idle (outside of lock to avoid blocking)
        if engine_became_idle and container_id_for_cleanup:
            try:
                from ..services.provisioner import clear_acestream_cache
                logger.debug(f"Engine {container_id_for_cleanup[:12]} has no active streams, clearing cache")
                success, cache_size = clear_acestream_cache(container_id_for_cleanup)
                
                # Update engine state with cleanup info
                if success:
                    with self._lock:
                        eng = self.engines.get(container_id_for_cleanup)
                        if eng:
                            eng.last_cache_cleanup = self.now()
                            eng.cache_size_bytes = cache_size
                    
                    # Update database as well
                    try:
                        with SessionLocal() as s:
                            engine_row = s.get(EngineRow, container_id_for_cleanup)
                            if engine_row:
                                engine_row.last_cache_cleanup = self.now()
                                engine_row.cache_size_bytes = cache_size
                                s.commit()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Failed to clear cache for idle engine {container_id_for_cleanup[:12]}: {e}")
        
        # Clean up metrics tracking for ended stream
        if stream_id_for_metrics:
            try:
                from ..services.metrics import on_stream_ended as metrics_stream_ended
                metrics_stream_ended(stream_id_for_metrics)
            except Exception as e:
                logger.warning(f"Failed to clean up metrics for stream {stream_id_for_metrics}: {e}")
        
        return st

    def list_engines(self) -> List[EngineState]:
        with self._lock:
            return list(self.engines.values())

    def get_engine(self, container_id: str) -> Optional[EngineState]:
        with self._lock:
            return self.engines.get(container_id)

    def remove_engine(self, container_id: str) -> Optional[EngineState]:
        """Remove an engine from the state and return it if it existed."""
        was_forwarded = False
        removed_vpn_container = None
        with self._lock:
            removed_engine = self.engines.pop(container_id, None)
            if removed_engine:
                was_forwarded = removed_engine.forwarded
                removed_vpn_container = removed_engine.vpn_container
                # Also remove any associated streams that are still active
                streams_to_remove = [s_id for s_id, stream in self.streams.items() 
                                   if stream.container_id == container_id and stream.status != "ended"]
                for s_id in streams_to_remove:
                    if s_id in self.streams:
                        self.streams[s_id].status = "ended"
                        self.streams[s_id].ended_at = self.now()
        
        # Remove from database as well (if database is available)
        if removed_engine:
            try:
                with SessionLocal() as s:
                    # Remove engine from database
                    engine_row = s.get(EngineRow, container_id)
                    if engine_row:
                        s.delete(engine_row)
                        s.commit()
            except Exception:
                # Database operation failed, but we can continue since we've updated memory state
                pass
        
        # If the removed engine was forwarded, promote another engine if using Gluetun
        if was_forwarded:
            from ..core.config import cfg
            if cfg.GLUETUN_CONTAINER_NAME and self.engines:
                is_redundant_mode = self._is_redundant_mode()
                
                with self._lock:
                    for engine_id, engine in self.engines.items():
                        # In redundant mode, only promote engines from the same VPN
                        # In single mode, promote any non-forwarded engine
                        same_vpn = (engine.vpn_container == removed_vpn_container)
                        should_promote = not engine.forwarded and (not is_redundant_mode or same_vpn)
                        
                        if should_promote:
                            if is_redundant_mode and removed_vpn_container:
                                logger.info(f"Forwarded engine removed from VPN '{removed_vpn_container}', promoting {engine_id[:12]} to forwarded")
                            else:
                                logger.info(f"Forwarded engine removed, promoting {engine_id[:12]} to forwarded")
                            self.set_forwarded_engine(engine_id)
                            break
        
        return removed_engine

    def list_streams(self, status: Optional[str] = None, container_id: Optional[str] = None) -> List[StreamState]:
        with self._lock:
            res = list(self.streams.values())
            if status: res = [s for s in res if s.status == status]
            if container_id: res = [s for s in res if s.container_id == container_id]
            return res

    def get_stream(self, stream_id: str) -> Optional[StreamState]:
        with self._lock:
            return self.streams.get(stream_id)

    def get_stream_stats(self, stream_id: str):
        with self._lock:
            return self.stream_stats.get(stream_id, [])
    
    def list_streams_with_stats(self, status: Optional[str] = None, container_id: Optional[str] = None) -> List[StreamState]:
        """
        Get streams enriched with their latest stats.
        Returns copies of stream objects with stats attached to avoid mutating the originals.
        """
        with self._lock:
            streams = list(self.streams.values())
            if status:
                streams = [s for s in streams if s.status == status]
            if container_id:
                streams = [s for s in streams if s.container_id == container_id]
            
            # Create enriched copies of streams with latest stats
            enriched_streams = []
            for stream in streams:
                # Create a copy using model_copy to avoid mutating the original
                enriched = stream.model_copy()
                stats = self.stream_stats.get(stream.id, [])
                if stats:
                    latest_stat = stats[-1]  # Get the most recent stat
                    enriched.peers = latest_stat.peers
                    enriched.speed_down = latest_stat.speed_down
                    enriched.speed_up = latest_stat.speed_up
                    enriched.downloaded = latest_stat.downloaded
                    enriched.uploaded = latest_stat.uploaded
                enriched_streams.append(enriched)
            
            return enriched_streams
    
    def get_realtime_snapshot(self):
        """Get a snapshot of all data for realtime updates with minimal lock time"""
        with self._lock:
            return {
                "engines": list(self.engines.values()),
                "streams": list(self.streams.values()),
                "stream_stats": dict(self.stream_stats)
            }

    def append_stat(self, stream_id: str, snap: StreamStatSnapshot):
        with self._lock:
            arr = self.stream_stats.setdefault(stream_id, [])
            arr.append(snap)
            from ..core.config import cfg as _cfg
            if len(arr) > _cfg.STATS_HISTORY_MAX:
                del arr[: len(arr) - _cfg.STATS_HISTORY_MAX]
        with SessionLocal() as s:
            s.add(StatRow(stream_id=stream_id, ts=snap.ts, peers=snap.peers, speed_down=snap.speed_down,
                          speed_up=snap.speed_up, downloaded=snap.downloaded, uploaded=snap.uploaded, status=snap.status))
            s.commit()

    def set_engine_vpn(self, container_id: str, vpn_container: str):
        """Set the VPN container assignment for an engine."""
        with self._lock:
            eng = self.engines.get(container_id)
            if eng:
                eng.vpn_container = vpn_container
                # Update database as well
                try:
                    with SessionLocal() as s:
                        engine_row = s.get(EngineRow, container_id)
                        if engine_row:
                            engine_row.vpn_container = vpn_container
                            s.commit()
                except Exception as e:
                    logger.warning(f"Failed to update VPN assignment in database: {e}")

    def get_engines_by_vpn(self, vpn_container: str) -> List[EngineState]:
        """Get all engines assigned to a specific VPN container."""
        with self._lock:
            return [eng for eng in self.engines.values() if eng.vpn_container == vpn_container]

    def load_from_db(self):
        from ..models.db_models import EngineRow, StreamRow
        with SessionLocal() as s:
            for e in s.query(EngineRow).all():
                # Ensure datetime objects are timezone-aware when loaded from database
                first_seen = e.first_seen.replace(tzinfo=timezone.utc) if e.first_seen.tzinfo is None else e.first_seen
                last_seen = e.last_seen.replace(tzinfo=timezone.utc) if e.last_seen.tzinfo is None else e.last_seen 
                # Get container_name from the database or try to fetch from Docker if not available
                container_name = getattr(e, 'container_name', None)
                if not container_name and e.container_id:
                    from ..services.inspect import get_container_name
                    container_name = get_container_name(e.container_id)
                    # If we can't get the name from Docker, but we have a container_id,
                    # use a truncated version of the container_id as a fallback
                    if not container_name:
                        container_name = f"container-{e.container_id[:12]}"
                elif not container_name:
                    # If no container_name and no container_id, use host:port as fallback
                    container_name = f"engine-{e.host}-{e.port}"
                
                # Handle new cache cleanup fields with proper timezone handling
                last_cache_cleanup = None
                if hasattr(e, 'last_cache_cleanup') and e.last_cache_cleanup:
                    last_cache_cleanup = e.last_cache_cleanup.replace(tzinfo=timezone.utc) if e.last_cache_cleanup.tzinfo is None else e.last_cache_cleanup
                cache_size_bytes = getattr(e, 'cache_size_bytes', None)
                forwarded = getattr(e, 'forwarded', False)
                vpn_container = getattr(e, 'vpn_container', None)
                
                self.engines[e.engine_key] = EngineState(container_id=e.engine_key, container_name=container_name,
                                                         host=e.host, port=e.port, labels=e.labels or {}, forwarded=forwarded,
                                                         first_seen=first_seen, last_seen=last_seen, streams=[],
                                                         health_status="unknown", last_health_check=None, last_stream_usage=None,
                                                         last_cache_cleanup=last_cache_cleanup, cache_size_bytes=cache_size_bytes,
                                                         vpn_container=vpn_container)

            for r in s.query(StreamRow).filter(StreamRow.status=="started").all():
                # Ensure datetime objects are timezone-aware when loaded from database
                started_at = r.started_at.replace(tzinfo=timezone.utc) if r.started_at.tzinfo is None else r.started_at
                ended_at = r.ended_at.replace(tzinfo=timezone.utc) if r.ended_at and r.ended_at.tzinfo is None else r.ended_at
                st = StreamState(id=r.id, key_type=r.key_type, key=r.key, container_id=r.engine_key,
                                 playback_session_id=r.playback_session_id, stat_url=r.stat_url, command_url=r.command_url,
                                 is_live=r.is_live, started_at=started_at, ended_at=ended_at, status=r.status)
                self.streams[st.id] = st
                eng = self.engines.get(r.engine_key)
                if eng and st.id not in eng.streams: eng.streams.append(st.id)

    def clear_state(self):
        """Clear all in-memory state."""
        with self._lock:
            self.engines.clear()
            self.streams.clear()
            self.stream_stats.clear()
        
        # Also clear cumulative metrics tracking
        try:
            from ..services.metrics import reset_cumulative_metrics
            reset_cumulative_metrics()
        except Exception as e:
            logger.warning(f"Failed to reset cumulative metrics: {e}")

    def clear_database(self):
        """Clear all database state."""
        from ..models.db_models import EngineRow, StreamRow, StatRow
        with SessionLocal() as s:
            try:
                # Delete all records in reverse dependency order
                s.query(StatRow).delete()
                s.query(StreamRow).delete()
                s.query(EngineRow).delete()
                s.commit()
            except Exception as e:
                # If tables don't exist or other database error, continue silently
                # This can happen during startup before tables are created
                s.rollback()
                logger.debug(f"Database cleanup skipped (tables may not exist): {e}")

    def cleanup_all(self):
        """Full cleanup: stop containers, clear database and memory state."""
        logger.info("Starting full cleanup: stopping all managed containers")
        
        # Stop all managed containers
        containers_stopped = 0
        try:
            from ..services.health import list_managed
            from ..services.provisioner import stop_container
            
            managed_containers = list_managed()
            logger.info(f"Found {len(managed_containers)} managed containers to stop")
            
            for container in managed_containers:
                try:
                    logger.info(f"Stopping container {container.id[:12]}")
                    stop_container(container.id)
                    containers_stopped += 1
                    logger.info(f"Successfully stopped container {container.id[:12]}")
                except Exception as e:
                    # Log error but continue cleanup
                    logger.warning(f"Failed to stop container {container.id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to list or stop managed containers: {e}")
        
        logger.info(f"Stopped {containers_stopped} containers during cleanup")
        
        # Clear database state
        logger.info("Clearing database state")
        self.clear_database()
        
        # Clear in-memory state
        logger.info("Clearing in-memory state")
        self.clear_state()
        
        # Clear port allocations to prevent double-counting during reindex
        logger.info("Clearing port allocations")
        try:
            from ..services.ports import alloc
            alloc.clear_all_allocations()
        except Exception as e:
            logger.warning(f"Failed to clear port allocations: {e}")
        
        logger.info("Full cleanup completed")
    
    def update_engine_health(self, container_id: str, health_status: str):
        """Update engine health status."""
        with self._lock:
            engine = self.engines.get(container_id)
            if engine:
                engine.health_status = health_status
                engine.last_health_check = self.now()
    
    def update_engines_health(self):
        """Update health status for all engines."""
        from ..services.health import check_acestream_health
        with self._lock:
            for engine in self.engines.values():
                health_status = check_acestream_health(engine.host, engine.port)
                engine.health_status = health_status
                engine.last_health_check = self.now()
    
    def set_forwarded_engine(self, container_id: str):
        """Mark an engine as the forwarded engine and clear forwarded flag from others.
        
        In redundant VPN mode, only clears forwarded flag from engines on the same VPN.
        In single VPN mode, clears forwarded flag from all engines.
        """
        with self._lock:
            # Get the target engine first to determine its VPN
            target_engine = self.engines.get(container_id)
            if not target_engine:
                logger.warning(f"Cannot set forwarded flag: engine {container_id[:12]} not found")
                return
            
            target_vpn = target_engine.vpn_container
            is_redundant_mode = self._is_redundant_mode()
            
            # Clear forwarded flag from engines
            for engine in self.engines.values():
                if engine.forwarded:
                    # In redundant mode, only clear if on the same VPN
                    # In single mode, clear all forwarded engines
                    should_clear = (not is_redundant_mode or engine.vpn_container == target_vpn)
                    
                    if should_clear:
                        engine.forwarded = False
                        # Update database
                        with SessionLocal() as s:
                            s.merge(EngineRow(engine_key=engine.container_id, container_id=engine.container_id,
                                            container_name=engine.container_name, host=engine.host, port=engine.port,
                                            labels=engine.labels, forwarded=False, first_seen=engine.first_seen,
                                            last_seen=engine.last_seen, vpn_container=engine.vpn_container))
                            s.commit()
            
            # Set forwarded flag on the specified engine
            target_engine.forwarded = True
            # Update database
            with SessionLocal() as s:
                s.merge(EngineRow(engine_key=target_engine.container_id, container_id=target_engine.container_id,
                                container_name=target_engine.container_name, host=target_engine.host, port=target_engine.port,
                                labels=target_engine.labels, forwarded=True, first_seen=target_engine.first_seen,
                                last_seen=target_engine.last_seen, vpn_container=target_engine.vpn_container))
                s.commit()
            
            if is_redundant_mode and target_vpn:
                logger.info(f"Engine {container_id[:12]} is now the forwarded engine for VPN '{target_vpn}'")
            else:
                logger.info(f"Engine {container_id[:12]} is now the forwarded engine")
    
    def get_forwarded_engine(self) -> Optional[EngineState]:
        """Get the engine marked as forwarded, if any."""
        with self._lock:
            for engine in self.engines.values():
                if engine.forwarded:
                    return engine
            return None
    
    def has_forwarded_engine(self) -> bool:
        """Check if there is a forwarded engine."""
        return self.get_forwarded_engine() is not None
    
    def get_forwarded_engine_for_vpn(self, vpn_container: str) -> Optional[EngineState]:
        """Get the forwarded engine for a specific VPN container."""
        with self._lock:
            for engine in self.engines.values():
                if engine.forwarded and engine.vpn_container == vpn_container:
                    return engine
            return None
    
    def has_forwarded_engine_for_vpn(self, vpn_container: str) -> bool:
        """Check if there is a forwarded engine for a specific VPN container."""
        return self.get_forwarded_engine_for_vpn(vpn_container) is not None

state = State()

def load_state_from_db():
    state.load_from_db()

def cleanup_on_shutdown():
    """Cleanup function for application shutdown."""
    state.cleanup_all()
