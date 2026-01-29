from __future__ import annotations
import threading
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        
        # Emergency mode state for redundant VPN failure handling
        self._emergency_mode = False
        self._failed_vpn_container: Optional[str] = None
        self._healthy_vpn_container: Optional[str] = None
        self._emergency_mode_entered_at: Optional[datetime] = None
        
        # Reprovisioning mode state for coordinating system-wide reprovisioning
        self._reprovisioning_mode = False
        self._reprovisioning_entered_at: Optional[datetime] = None
        
        # VPN recovery mode state for directing engines to recovered VPN
        self._vpn_recovery_mode = False
        self._recovery_target_vpn: Optional[str] = None
        self._vpn_recovery_entered_at: Optional[datetime] = None
        
        # Lookahead provisioning tracking
        # Tracks the minimum stream count across all engines when lookahead was last triggered
        # This prevents repeated lookahead triggers until all engines reach this layer
        self._lookahead_layer: Optional[int] = None

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
                                  vpn_container=None)
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
            
            # Immediately remove the stream from memory (from both streams dict and stats dict)
            # This ensures ended streams don't appear in the /streams endpoint
            # The database record is preserved below for historical tracking
            if st.id in self.streams:
                del self.streams[st.id]
            if st.id in self.stream_stats:
                del self.stream_stats[st.id]
                
        try:
            with SessionLocal() as s:
                row = s.get(StreamRow, st.id)
                if row:
                    row.ended_at = st.ended_at; row.status = st.status; s.commit()
        except Exception:
            # Database operation failed, but we can continue since we've updated memory state
            pass
        
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
        """Remove an engine from the state and return it if it existed.
        
        Note: If the removed engine was forwarded, the autoscaler will automatically
        provision a new engine to maintain MIN_REPLICAS. That new engine will become
        the forwarded engine since none will exist for that VPN.
        """
        with self._lock:
            removed_engine = self.engines.pop(container_id, None)
            if removed_engine:
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
    
    def update_stream_metadata(
        self, 
        stream_id: str, 
        resolution: Optional[str] = None,
        fps: Optional[float] = None,
        video_codec: Optional[str] = None,
        audio_codec: Optional[str] = None
    ):
        """Update stream metadata (resolution, fps, codecs)."""
        with self._lock:
            st = self.streams.get(stream_id)
            if not st:
                logger.warning(f"Cannot update metadata for unknown stream {stream_id}")
                return
            
            # Update in-memory state
            if resolution is not None:
                st.resolution = resolution
            if fps is not None:
                st.fps = fps
            if video_codec is not None:
                st.video_codec = video_codec
            if audio_codec is not None:
                st.audio_codec = audio_codec
            
            logger.info(
                f"Updated metadata for stream {stream_id}: "
                f"resolution={resolution}, fps={fps}, "
                f"video_codec={video_codec}, audio_codec={audio_codec}"
            )
        
        # Note: We don't persist metadata to database since it's transient
        # and will be re-extracted on next stream start if needed

    def get_stream_stats(self, stream_id: str):
        with self._lock:
            return self.stream_stats.get(stream_id, [])
    
    def list_streams_with_stats(self, status: Optional[str] = None, container_id: Optional[str] = None) -> List[StreamState]:
        """
        Get streams enriched with their latest stats.
        Returns copies of stream objects with stats attached to avoid mutating the originals.
        For ended streams, speed/peer data is set to None as it's no longer relevant.
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
                
                # Only add stats for active streams
                if stream.status == "started":
                    stats = self.stream_stats.get(stream.id, [])
                    if stats:
                        latest_stat = stats[-1]  # Get the most recent stat
                        enriched.peers = latest_stat.peers
                        enriched.speed_down = latest_stat.speed_down
                        enriched.speed_up = latest_stat.speed_up
                        enriched.downloaded = latest_stat.downloaded
                        enriched.uploaded = latest_stat.uploaded
                        enriched.livepos = latest_stat.livepos
                else:
                    # For ended streams, clear speed/peer data as it's no longer relevant
                    enriched.peers = None
                    enriched.speed_down = None
                    enriched.speed_up = None
                    enriched.livepos = None
                    # Keep downloaded/uploaded totals for historical record from last stat
                    stats = self.stream_stats.get(stream.id, [])
                    if stats:
                        latest_stat = stats[-1]
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
        # Note: livepos data is intentionally not persisted to database
        # It's highly transient (updates every 1s) and would cause database bloat.
        # It's only kept in memory for real-time access via /streams endpoint.
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
                
                forwarded = getattr(e, 'forwarded', False)
                vpn_container = getattr(e, 'vpn_container', None)
                
                self.engines[e.engine_key] = EngineState(container_id=e.engine_key, container_name=container_name,
                                                         host=e.host, port=e.port, labels=e.labels or {}, forwarded=forwarded,
                                                         first_seen=first_seen, last_seen=last_seen, streams=[],
                                                         health_status="unknown", last_health_check=None, last_stream_usage=None,
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
        
        # Stop all managed containers in parallel
        containers_stopped = 0
        try:
            from ..services.health import list_managed
            from ..services.provisioner import stop_container
            
            managed_containers = list_managed()
            logger.info(f"Found {len(managed_containers)} managed containers to stop")
            
            if managed_containers:
                # Stop containers in parallel using ThreadPoolExecutor
                # This significantly improves shutdown performance
                def stop_single_container(container):
                    """Helper function to stop a single container."""
                    try:
                        logger.info(f"Stopping container {container.id[:12]}")
                        stop_container(container.id)
                        logger.info(f"Successfully stopped container {container.id[:12]}")
                        return True
                    except Exception as e:
                        # Log error but continue cleanup
                        logger.warning(f"Failed to stop container {container.id}: {e}")
                        return False
                
                # Use ThreadPoolExecutor for parallel stopping
                # Limit workers to min(container_count, 10) to avoid overwhelming Docker daemon
                # while still achieving significant speedup
                max_workers = min(len(managed_containers), 10)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all stop tasks
                    futures = [executor.submit(stop_single_container, container) 
                              for container in managed_containers]
                    
                    # Wait for all tasks to complete and count successes
                    for future in as_completed(futures):
                        if future.result():
                            containers_stopped += 1
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
    
    def enter_emergency_mode(self, failed_vpn: str, healthy_vpn: str) -> bool:
        """
        Enter emergency mode when one VPN fails in redundant mode.
        
        Emergency mode:
        - Immediately takes over all operations
        - Deletes engines from the failed VPN
        - Only manages the working VPN's engines
        - Prevents provisioner/health_manager from operating on failed VPN
        
        Args:
            failed_vpn: Name of the failed VPN container
            healthy_vpn: Name of the healthy VPN container
            
        Returns:
            True if emergency mode was entered, False if already in emergency mode
        """
        with self._lock:
            if self._emergency_mode:
                logger.warning(f"Already in emergency mode (failed VPN: {self._failed_vpn_container})")
                return False
            
            self._emergency_mode = True
            self._failed_vpn_container = failed_vpn
            self._healthy_vpn_container = healthy_vpn
            self._emergency_mode_entered_at = self.now()
            
            logger.warning(f"⚠️  ENTERING EMERGENCY MODE ⚠️")
            logger.warning(f"Failed VPN: {failed_vpn}")
            logger.warning(f"Healthy VPN: {healthy_vpn}")
            logger.warning(f"System will operate with reduced capacity on single VPN until recovery")
            
            # Remove all engines assigned to the failed VPN
            engines_to_remove = [
                eng.container_id for eng in self.engines.values()
                if eng.vpn_container == failed_vpn
            ]
            
            if engines_to_remove:
                logger.warning(f"Removing {len(engines_to_remove)} engines from failed VPN '{failed_vpn}'")
                for container_id in engines_to_remove:
                    self.remove_engine(container_id)
                    # Also stop the container
                    try:
                        from .provisioner import stop_container
                        stop_container(container_id)
                        logger.info(f"Stopped engine {container_id[:12]} from failed VPN")
                    except Exception as e:
                        logger.error(f"Failed to stop engine {container_id[:12]}: {e}")
            
            remaining_engines = len([eng for eng in self.engines.values()])
            logger.warning(f"Emergency mode active: operating with {remaining_engines} engines on '{healthy_vpn}'")
            
            return True
    
    def exit_emergency_mode(self) -> bool:
        """
        Exit emergency mode after VPN recovery.
        
        Returns:
            True if emergency mode was exited, False if not in emergency mode
        """
        with self._lock:
            if not self._emergency_mode:
                return False
            
            failed_vpn = self._failed_vpn_container
            healthy_vpn = self._healthy_vpn_container
            duration = (self.now() - self._emergency_mode_entered_at).total_seconds() if self._emergency_mode_entered_at else 0
            
            self._emergency_mode = False
            self._failed_vpn_container = None
            self._healthy_vpn_container = None
            self._emergency_mode_entered_at = None
            
            logger.info(f"✅ EXITING EMERGENCY MODE ✅")
            logger.info(f"VPN '{failed_vpn}' has recovered after {duration:.1f}s")
            logger.info(f"System will restore full capacity and resume normal operations")
            
            return True
    
    def is_emergency_mode(self) -> bool:
        """Check if system is in emergency mode."""
        with self._lock:
            return self._emergency_mode
    
    def get_emergency_mode_info(self) -> Dict:
        """Get information about emergency mode status."""
        with self._lock:
            if not self._emergency_mode:
                return {
                    "active": False,
                    "failed_vpn": None,
                    "healthy_vpn": None,
                    "duration_seconds": 0
                }
            
            duration = (self.now() - self._emergency_mode_entered_at).total_seconds() if self._emergency_mode_entered_at else 0
            
            return {
                "active": True,
                "failed_vpn": self._failed_vpn_container,
                "healthy_vpn": self._healthy_vpn_container,
                "duration_seconds": duration,
                "entered_at": self._emergency_mode_entered_at.isoformat() if self._emergency_mode_entered_at else None
            }
    
    def should_skip_vpn_operations(self, vpn_container: str) -> bool:
        """
        Check if operations should be skipped for a VPN container.
        
        In emergency mode, operations on the failed VPN should be skipped.
        """
        with self._lock:
            if not self._emergency_mode:
                return False
            return vpn_container == self._failed_vpn_container
    
    def enter_reprovisioning_mode(self) -> bool:
        """
        Enter reprovisioning mode to coordinate system-wide engine reprovisioning.
        
        Reprovisioning mode:
        - Pauses health management operations
        - Pauses autoscaling operations
        - Allows monitoring to continue (but with reduced operations)
        
        Returns:
            bool: True if successfully entered reprovisioning mode, False if already in it
        """
        with self._lock:
            if self._reprovisioning_mode:
                logger.warning("Already in reprovisioning mode")
                return False
            
            self._reprovisioning_mode = True
            self._reprovisioning_entered_at = self.now()
            
            logger.info("Entered reprovisioning mode - pausing health management and autoscaling")
            return True
    
    def exit_reprovisioning_mode(self) -> bool:
        """
        Exit reprovisioning mode and resume normal operations.
        
        Returns:
            bool: True if successfully exited reprovisioning mode, False if wasn't in it
        """
        with self._lock:
            if not self._reprovisioning_mode:
                logger.warning("Not in reprovisioning mode")
                return False
            
            duration = (self.now() - self._reprovisioning_entered_at).total_seconds() if self._reprovisioning_entered_at else 0
            
            self._reprovisioning_mode = False
            self._reprovisioning_entered_at = None
            
            logger.info(f"Exited reprovisioning mode after {duration:.1f}s - resuming normal operations")
            return True
    
    def is_reprovisioning_mode(self) -> bool:
        """Check if system is in reprovisioning mode."""
        with self._lock:
            return self._reprovisioning_mode
    
    def get_reprovisioning_mode_info(self) -> Dict:
        """Get information about reprovisioning mode status."""
        with self._lock:
            if not self._reprovisioning_mode:
                return {
                    "active": False,
                    "duration_seconds": 0,
                    "entered_at": None
                }
            
            duration = (self.now() - self._reprovisioning_entered_at).total_seconds() if self._reprovisioning_entered_at else 0
            
            return {
                "active": True,
                "duration_seconds": duration,
                "entered_at": self._reprovisioning_entered_at.isoformat() if self._reprovisioning_entered_at else None
            }
    
    def enter_vpn_recovery_mode(self, target_vpn: str) -> bool:
        """
        Enter VPN recovery mode to direct all new engines to the specified VPN.
        
        This is used after a VPN recovers to ensure engines are provisioned to it
        to restore balance, rather than using round-robin which would keep imbalance.
        
        Args:
            target_vpn: VPN container name to direct all new engines to
            
        Returns:
            bool: True if successfully entered VPN recovery mode, False if already in it
        """
        with self._lock:
            if self._vpn_recovery_mode:
                logger.warning(f"Already in VPN recovery mode (target: {self._recovery_target_vpn})")
                return False
            
            self._vpn_recovery_mode = True
            self._recovery_target_vpn = target_vpn
            self._vpn_recovery_entered_at = self.now()
            
            logger.info(f"Entered VPN recovery mode - all new engines will be assigned to '{target_vpn}'")
            return True
    
    def exit_vpn_recovery_mode(self) -> bool:
        """
        Exit VPN recovery mode and resume normal round-robin provisioning.
        
        Returns:
            bool: True if successfully exited VPN recovery mode, False if wasn't in it
        """
        with self._lock:
            if not self._vpn_recovery_mode:
                logger.warning("Not in VPN recovery mode")
                return False
            
            duration = (self.now() - self._vpn_recovery_entered_at).total_seconds() if self._vpn_recovery_entered_at else 0
            target_vpn = self._recovery_target_vpn
            
            self._vpn_recovery_mode = False
            self._recovery_target_vpn = None
            self._vpn_recovery_entered_at = None
            
            logger.info(f"Exited VPN recovery mode after {duration:.1f}s (target was: {target_vpn}) - resuming normal round-robin")
            return True
    
    def is_vpn_recovery_mode(self) -> bool:
        """Check if system is in VPN recovery mode."""
        with self._lock:
            return self._vpn_recovery_mode
    
    def get_vpn_recovery_target(self) -> Optional[str]:
        """Get the target VPN for recovery mode, or None if not in recovery mode."""
        with self._lock:
            return self._recovery_target_vpn if self._vpn_recovery_mode else None
    
    def set_lookahead_layer(self, layer: int) -> None:
        """
        Set the lookahead layer (minimum stream count) when provisioning is triggered.
        This prevents repeated lookahead triggers until all engines reach this layer.
        
        Args:
            layer: The minimum stream count across all engines when lookahead was triggered
        """
        with self._lock:
            self._lookahead_layer = layer
            logger.info(f"Lookahead layer set to {layer} - next lookahead trigger will wait until all engines reach layer {layer}")
    
    def get_lookahead_layer(self) -> Optional[int]:
        """Get the current lookahead layer, or None if not set."""
        with self._lock:
            return self._lookahead_layer
    
    def reset_lookahead_layer(self) -> None:
        """Reset the lookahead layer tracking."""
        with self._lock:
            if self._lookahead_layer is not None:
                logger.info(f"Resetting lookahead layer (was {self._lookahead_layer})")
            self._lookahead_layer = None
    
    def cleanup_ended_streams(self, max_age_seconds: int = 3600) -> int:
        """
        Backup cleanup for ended streams that are older than max_age_seconds.
        
        This is a safety net that removes any ended streams still in memory 
        (in case immediate removal in on_stream_ended() failed) and also 
        removes old stream records from the database for cleanup.
        
        Args:
            max_age_seconds: Maximum age in seconds for ended streams to keep (default: 1 hour)
            
        Returns:
            Number of streams removed from memory (should normally be 0)
        """
        from datetime import timedelta
        
        with self._lock:
            now = self.now()
            cutoff_time = now - timedelta(seconds=max_age_seconds)
            
            # Find ended streams that are older than the cutoff
            streams_to_remove = []
            for stream_id, stream in self.streams.items():
                if stream.status == "ended" and stream.ended_at and stream.ended_at < cutoff_time:
                    streams_to_remove.append(stream_id)
            
            # Remove them from memory
            for stream_id in streams_to_remove:
                del self.streams[stream_id]
                # Also remove stats for the stream to free memory
                if stream_id in self.stream_stats:
                    del self.stream_stats[stream_id]
        
        # Remove from database as well
        if streams_to_remove:
            try:
                from ..models.db_models import StreamRow, StatRow
                with SessionLocal() as s:
                    # Delete stats first (foreign key constraint)
                    s.query(StatRow).filter(StatRow.stream_id.in_(streams_to_remove)).delete(synchronize_session=False)
                    # Then delete streams
                    s.query(StreamRow).filter(StreamRow.id.in_(streams_to_remove)).delete(synchronize_session=False)
                    s.commit()
                    logger.info(f"Cleaned up {len(streams_to_remove)} ended streams older than {max_age_seconds}s")
            except Exception as e:
                logger.warning(f"Failed to clean up ended streams from database: {e}")
        
        return len(streams_to_remove)

state = State()

def load_state_from_db():
    state.load_from_db()

def cleanup_on_shutdown():
    """Cleanup function for application shutdown."""
    state.cleanup_all()
