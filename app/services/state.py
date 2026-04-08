from __future__ import annotations
import queue
import threading
import logging
import uuid
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..models.schemas import EngineState, StreamState, StreamStartedEvent, StreamEndedEvent, StreamStatSnapshot
from ..proxy.constants import normalize_proxy_mode
from ..services.db import SessionLocal
from ..models.db_models import EngineRow, StreamRow, StatRow

logger = logging.getLogger(__name__)

ACTIVE_MONITOR_SESSION_STATUSES: Set[str] = {"starting", "running", "stuck", "reconnecting"}
VPN_NODE_LIFECYCLE_ACTIVE = "active"
VPN_NODE_LIFECYCLE_DRAINING = "draining"
ENGINE_LIFECYCLE_LABEL = "acestream.lifecycle"
ENGINE_DRAIN_REASON_LABEL = "acestream.drain_reason"
ENGINE_DRAIN_REQUESTED_AT_LABEL = "acestream.drain_requested_at"

class State:
    def __init__(self):
        self._lock = threading.RLock()
        self._db_queue: queue.Queue = queue.Queue()
        self._stop_db_worker = threading.Event()
        self._db_worker = threading.Thread(target=self._db_persistence_worker, daemon=True, name="state-db-worker")
        self._db_worker.start()
        self.engines: Dict[str, EngineState] = {}
        self.streams: Dict[str, StreamState] = {}
        self.stream_stats: Dict[str, List[StreamStatSnapshot]] = {}
        self.monitor_sessions: Dict[str, Dict[str, object]] = {}
        self._desired_replica_count = 0
        self._desired_vpn_node_count = 0
        self._dynamic_vpn_nodes: Dict[str, Dict[str, object]] = {}
        self._scaling_intents: List[Dict[str, object]] = []
        self._max_scaling_intents = 300
        self._target_engine_config_hash: str = ""
        self._target_engine_generation: int = 0
        self._state_change_subscribers: Dict[str, Callable[[Dict[str, object]], None]] = {}
        self._state_change_lock = threading.RLock()
        self._state_change_seq = 0
        self._last_stats_broadcast_monotonic = 0.0
        
        # Lookahead provisioning tracking
        # Tracks the minimum stream count across all engines when lookahead was last triggered
        # This prevents repeated lookahead triggers until all engines reach this layer
        self._lookahead_layer: Optional[int] = None

        # Cache statistics
        self.cache_stats = {
            "total_bytes": 0,
            "volume_count": 0,
            "last_updated": None
        }

        try:
            from ..core.config import cfg
            self._desired_replica_count = cfg.MIN_REPLICAS
        except Exception:
            self._desired_replica_count = 0

    @staticmethod
    def now():
        return datetime.now(timezone.utc)

    def subscribe_state_changes(self, callback: Callable[[Dict[str, object]], None]) -> Callable[[], None]:
        """Register a callback for state change broadcasts and return an unsubscribe function."""
        if not callable(callback):
            raise TypeError("callback must be callable")

        token = str(uuid.uuid4())
        with self._state_change_lock:
            self._state_change_subscribers[token] = callback

        def _unsubscribe():
            with self._state_change_lock:
                self._state_change_subscribers.pop(token, None)

        return _unsubscribe

    def get_state_change_seq(self) -> int:
        with self._state_change_lock:
            return self._state_change_seq

    def broadcast_state_change(self, change_type: str = "state_updated", metadata: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        """Broadcast an in-process state-change event to subscribers."""
        with self._state_change_lock:
            self._state_change_seq += 1
            seq = self._state_change_seq
            subscribers = list(self._state_change_subscribers.values())

        event = {
            "seq": seq,
            "change_type": str(change_type or "state_updated"),
            "at": self.now().isoformat(),
            "metadata": dict(metadata or {}),
        }

        for callback in subscribers:
            try:
                callback(event)
            except Exception as e:
                logger.debug(f"State change subscriber callback failed: {e}")

        return event

    def _enqueue_db_task(self, task: Callable[[Any], None]):
        if self._stop_db_worker.is_set():
            logger.debug("Skipping DB task enqueue because DB worker is stopping")
            return
        self._db_queue.put(task)

    def _db_persistence_worker(self):
        while True:
            if self._stop_db_worker.is_set() and self._db_queue.empty():
                break

            try:
                task = self._db_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                if task is None:
                    break

                with SessionLocal() as s:
                    try:
                        task(s)
                        s.commit()
                    except Exception as e:
                        logger.error(f"Background DB write failed: {e}")
                        s.rollback()
            except Exception as e:
                logger.error(f"DB worker loop error: {e}")
            finally:
                self._db_queue.task_done()
    
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
                api_port = None
                if evt.labels:
                    api_port_raw = evt.labels.get("host.api_port") or evt.labels.get("acestream.api_port")
                    if api_port_raw:
                        try:
                            api_port = int(api_port_raw)
                        except ValueError:
                            api_port = None
                eng = EngineState(container_id=key, container_name=container_name, host=evt.engine.host, port=evt.engine.port,
                                  api_port=api_port, labels=evt.labels or {}, forwarded=False, first_seen=self.now(), last_seen=self.now(), streams=[],
                                  health_status="unknown", last_health_check=None, last_stream_usage=self.now(),
                                  vpn_container=None)
                self.engines[key] = eng
            else:
                eng.host = evt.engine.host; eng.port = evt.engine.port; eng.last_seen = self.now()
                eng.last_stream_usage = self.now()  # Update last stream usage when stream starts
                if container_name and not eng.container_name:
                    eng.container_name = container_name
                if evt.labels: eng.labels.update(evt.labels)
                if evt.labels and eng.api_port is None:
                    api_port_raw = evt.labels.get("host.api_port") or evt.labels.get("acestream.api_port")
                    if api_port_raw:
                        try:
                            eng.api_port = int(api_port_raw)
                        except ValueError:
                            pass

            stream_id = (evt.labels.get("stream_id") if evt.labels else None) or f"{evt.stream.key}|{evt.session.playback_session_id}"
            st = StreamState(id=stream_id, key_type=evt.stream.key_type, key=evt.stream.key,
                             file_indexes=evt.stream.file_indexes,
                             seekback=evt.stream.seekback,
                             live_delay=(evt.stream.live_delay if evt.stream.live_delay is not None else evt.stream.seekback),
                             control_mode=normalize_proxy_mode((evt.labels or {}).get("proxy.control_mode"), default=None),
                             container_id=key, container_name=eng.container_name if eng else container_name,
                             playback_session_id=evt.session.playback_session_id,
                             stat_url=str(evt.session.stat_url or ""), command_url=str(evt.session.command_url or ""),
                             is_live=bool(evt.session.is_live), started_at=self.now(), status="started")
            self.streams[stream_id] = st
            if stream_id not in eng.streams: eng.streams.append(stream_id)

            # Seed first snapshot from stream-start labels (legacy API compatibility).
            if evt.labels:
                def _to_int(v):
                    try:
                        if v is None or str(v).strip() == "":
                            return None
                        return int(float(str(v)))
                    except (TypeError, ValueError):
                        return None

                status_text = evt.labels.get("stream.status_text")
                peers = _to_int(evt.labels.get("stream.peers"))
                http_peers = _to_int(evt.labels.get("stream.http_peers"))
                progress = _to_int(evt.labels.get("stream.progress"))

                if any(v is not None for v in [status_text, peers, http_peers, progress]):
                    initial_snap = StreamStatSnapshot(
                        ts=self.now(),
                        peers=peers if peers is not None else http_peers,
                        status=status_text,
                    )
                    self.stream_stats.setdefault(stream_id, []).append(initial_snap)

        engine_payload = {
            "engine_key": eng.container_id,
            "container_id": evt.container_id,
            "container_name": container_name,
            "host": eng.host,
            "port": eng.port,
            "labels": dict(eng.labels or {}),
            "forwarded": eng.forwarded,
            "first_seen": eng.first_seen,
            "last_seen": eng.last_seen,
            "vpn_container": eng.vpn_container,
        }
        stream_payload = {
            "id": stream_id,
            "engine_key": eng.container_id,
            "key_type": st.key_type,
            "key": st.key,
            "playback_session_id": st.playback_session_id,
            "stat_url": st.stat_url,
            "command_url": st.command_url,
            "is_live": st.is_live,
            "started_at": st.started_at,
            "status": st.status,
        }

        def db_work(session):
            session.merge(EngineRow(**engine_payload))
            session.merge(StreamRow(**stream_payload))

        self._enqueue_db_task(db_work)
        self.broadcast_state_change(
            "stream_started",
            {
                "stream_id": stream_id,
                "container_id": key,
            },
        )
        return st

    def on_stream_ended(self, evt: StreamEndedEvent) -> Optional[StreamState]:
        engine_became_idle = False
        container_id_for_cleanup = None
        stream_id_for_metrics = None
        ended_stream_payloads: List[Dict[str, object]] = []
        
        with self._lock:
            st: Optional[StreamState] = None
            if evt.stream_id and evt.stream_id in self.streams:
                st = self.streams[evt.stream_id]
            else:
                for s in reversed(list(self.streams.values())):
                    if s.container_id == (evt.container_id or s.container_id) and s.ended_at is None:
                        st = s; break
            if not st: return None
            stream_id_for_metrics = st.id

            # End the primary stream plus any duplicate active rows for the same
            # content key created by reconnect/hot-swap start-event churn.
            target_key = str(st.key or "")
            target_ids = [st.id]
            for candidate_id, candidate in list(self.streams.items()):
                if candidate_id == st.id:
                    continue
                if candidate.ended_at is not None:
                    continue
                if str(candidate.key or "") != target_key:
                    continue
                target_ids.append(candidate_id)

            for target_id in target_ids:
                target_stream = self.streams.get(target_id)
                if not target_stream:
                    continue

                target_stream.ended_at = self.now()
                target_stream.status = "ended"
                ended_stream_payloads.append(
                    {
                        "id": target_stream.id,
                        "ended_at": target_stream.ended_at,
                        "status": target_stream.status,
                    }
                )

                # Remove the stream from the owning engine's stream list.
                eng = self.engines.get(target_stream.container_id)
                if eng and target_stream.id in eng.streams:
                    eng.streams.remove(target_stream.id)
                    if len(eng.streams) == 0:
                        engine_became_idle = True
                        container_id_for_cleanup = target_stream.container_id

                # Immediately remove stream/session rows from in-memory API state.
                self.streams.pop(target_stream.id, None)
                self.stream_stats.pop(target_stream.id, None)
                
        if ended_stream_payloads:
            payloads_for_db = [dict(item) for item in ended_stream_payloads]

            def db_work(session):
                for payload in payloads_for_db:
                    row = session.get(StreamRow, str(payload["id"]))
                    if row:
                        row.ended_at = payload["ended_at"]
                        row.status = str(payload["status"])

            self._enqueue_db_task(db_work)
        
        # Clean up metrics tracking for ended stream
        if stream_id_for_metrics:
            try:
                from ..services.metrics import on_stream_ended as metrics_stream_ended
                metrics_stream_ended(stream_id_for_metrics)
            except Exception as e:
                logger.warning(f"Failed to clean up metrics for stream {stream_id_for_metrics}: {e}")
        
        # CRITICAL: Synchronize proxy cleanup when stream ends
        # This ensures proxy sessions are stopped when streams are removed from state
        # Prevents desynchronization where streams disappear from UI but proxy still serves them
        if st and st.key:
            try:
                # Clean up TS proxy session
                from ..proxy.server import ProxyServer
                proxy_server = ProxyServer.get_instance()
                proxy_server.stop_stream_by_key(st.key)
                logger.debug(f"Synchronized TS proxy cleanup for stream key={st.key}")
            except Exception as e:
                # Don't fail stream ending if proxy cleanup fails
                # Proxy has its own idle cleanup as fallback
                logger.warning(f"Failed to synchronize TS proxy cleanup for stream {st.key}: {e}")
            
            try:
                # Clean up HLS proxy session
                from ..proxy.hls_proxy import HLSProxyServer
                hls_proxy = HLSProxyServer.get_instance()
                hls_proxy.stop_stream_by_key(st.key)
                logger.debug(f"Synchronized HLS proxy cleanup for stream key={st.key}")
            except Exception as e:
                # Don't fail stream ending if HLS proxy cleanup fails
                logger.warning(f"Failed to synchronize HLS proxy cleanup for stream {st.key}: {e}")

            try:
                # Clean up external API-mode HLS segmenter if active.
                from ..services.hls_segmenter import hls_segmenter_service
                hls_segmenter_service.stop_segmenter_nowait(st.key, emit_stream_ended=False)
            except Exception as e:
                logger.warning(f"Failed to schedule external HLS segmenter cleanup for stream {st.key}: {e}")

        if st:
            self.broadcast_state_change(
                "stream_ended",
                {
                    "stream_id": st.id,
                    "container_id": st.container_id,
                },
            )
        
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
        ended_stream_updates: List[Dict[str, object]] = []
        with self._lock:
            removed_engine = self.engines.pop(container_id, None)
            if removed_engine:
                # Transition associated streams to pending_failover for Control Plane recovery
                streams_to_failover = [s_id for s_id, stream in self.streams.items() 
                                   if stream.container_id == container_id and stream.status == "started"]
                for s_id in streams_to_failover:
                    stream = self.streams[s_id]
                    stream.status = "pending_failover"

                    ended_stream_updates.append(
                        {
                            "id": s_id,
                            "ended_at": stream.ended_at,
                            "status": stream.status,
                        }
                    )
                    
                    # DO NOT remove from memory, trigger background recovery directly
                    try:
                        from ..services.recovery import recover_stream
                        recover_stream(s_id, dead_vpn=removed_engine.vpn_container)
                    except Exception as e:
                        logger.error(f"Failed to trigger recovery for orphaned stream {s_id}: {e}")
        
        # Remove from database as well (if database is available)
        if removed_engine:
            updates_payload = [dict(item) for item in ended_stream_updates]
            removed_engine_id = str(container_id)

            def db_work(session):
                for payload in updates_payload:
                    row = session.get(StreamRow, str(payload["id"]))
                    if row:
                        row.ended_at = payload["ended_at"]
                        row.status = str(payload["status"])

                engine_row = session.get(EngineRow, removed_engine_id)
                if engine_row:
                    session.delete(engine_row)

            self._enqueue_db_task(db_work)

            try:
                from .engine_info import invalidate_engine_version_cache
                invalidate_engine_version_cache(container_id)
            except Exception:
                pass

            self.broadcast_state_change(
                "engine_removed",
                {
                    "container_id": removed_engine_id,
                },
            )
        
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

    def reassign_active_streams_to_engine_by_key(
        self,
        *,
        stream_key: str,
        old_container_id: str,
        new_engine: EngineState,
        session_updates: Optional[Dict[str, object]] = None,
    ) -> int:
        """Move active stream ownership from one engine to another for a stream key.

        This is used by proxy hot-swap migrations so draining engines can reach zero active streams
        without dropping frontend client connections.
        """
        normalized_key = str(stream_key or "").strip()
        normalized_old = str(old_container_id or "").strip()
        session_updates = dict(session_updates or {})

        if not normalized_key:
            return 0

        updates: List[Dict[str, object]] = []
        updated_count = 0

        with self._lock:
            target_engine = self.engines.get(new_engine.container_id)
            if target_engine is None:
                target_engine = new_engine.model_copy(deep=True)
                if not target_engine.streams:
                    target_engine.streams = []
                self.engines[target_engine.container_id] = target_engine

            source_engine = self.engines.get(normalized_old) if normalized_old else None

            for stream in self.streams.values():
                if stream.status not in {"started", "pending_failover"}:
                    continue
                if stream.key != normalized_key:
                    continue
                if normalized_old and stream.container_id != normalized_old:
                    continue

                previous_container_id = stream.container_id

                stream.container_id = target_engine.container_id
                stream.container_name = target_engine.container_name or stream.container_name

                playback_session_id = session_updates.get("playback_session_id")
                if playback_session_id:
                    stream.playback_session_id = str(playback_session_id)

                if "stat_url" in session_updates and session_updates.get("stat_url") is not None:
                    stream.stat_url = str(session_updates.get("stat_url") or "")

                if "command_url" in session_updates and session_updates.get("command_url") is not None:
                    stream.command_url = str(session_updates.get("command_url") or "")

                if "is_live" in session_updates and session_updates.get("is_live") is not None:
                    stream.is_live = bool(session_updates.get("is_live"))

                if source_engine and stream.id in source_engine.streams:
                    source_engine.streams.remove(stream.id)
                if stream.id not in target_engine.streams:
                    target_engine.streams.append(stream.id)

                updates.append(
                    {
                        "stream_id": stream.id,
                        "previous_engine": previous_container_id,
                        "new_engine": target_engine.container_id,
                        "container_name": stream.container_name,
                        "playback_session_id": stream.playback_session_id,
                        "stat_url": stream.stat_url,
                        "command_url": stream.command_url,
                        "is_live": stream.is_live,
                    }
                )
                updated_count += 1

        if not updates:
            return 0

        updates_payload = [dict(payload) for payload in updates]

        def db_work(session):
            for payload in updates_payload:
                row = session.get(StreamRow, str(payload["stream_id"]))
                if not row:
                    continue
                row.engine_key = str(payload["new_engine"])
                row.playback_session_id = str(payload["playback_session_id"])
                row.stat_url = str(payload["stat_url"])
                row.command_url = str(payload["command_url"])
                row.is_live = bool(payload["is_live"])

        self._enqueue_db_task(db_work)

        logger.info(
            "Reassigned %s active stream(s) for key=%s from engine=%s to engine=%s",
            updated_count,
            normalized_key,
            normalized_old or "any",
            new_engine.container_id,
        )
        self.broadcast_state_change(
            "streams_reassigned",
            {
                "stream_key": normalized_key,
                "old_container_id": normalized_old,
                "new_container_id": new_engine.container_id,
                "updated_count": updated_count,
            },
        )
        return updated_count

    def set_stream_paused(self, stream_id: str, paused: bool) -> Optional[StreamState]:
        with self._lock:
            stream = self.streams.get(stream_id)
            if not stream:
                return None
            stream.paused = bool(paused)
            return stream.model_copy()
    
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
                        
                        if hasattr(latest_stat, 'proxy_buffer_pieces'):
                            enriched.proxy_buffer_pieces = latest_stat.proxy_buffer_pieces
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
                "stream_stats": dict(self.stream_stats),
                "monitor_sessions": dict(self.monitor_sessions),
                "cache_stats": dict(self.cache_stats)
            }

    def upsert_monitor_session(self, monitor_id: str, session_data: Dict[str, object]):
        with self._lock:
            self.monitor_sessions[monitor_id] = dict(session_data or {})

    def get_monitor_session(self, monitor_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            data = self.monitor_sessions.get(monitor_id)
            return dict(data) if data else None

    def list_monitor_sessions(self) -> List[Dict[str, object]]:
        with self._lock:
            return [dict(v) for v in self.monitor_sessions.values()]

    def get_active_monitor_load_by_engine(self) -> Dict[str, int]:
        """Return active monitor-session counts keyed by engine container_id."""
        with self._lock:
            counts: Dict[str, int] = {}
            for session in self.monitor_sessions.values():
                if (session.get("status") or "") not in ACTIVE_MONITOR_SESSION_STATUSES:
                    continue

                engine = session.get("engine") or {}
                container_id = engine.get("container_id")
                if not container_id:
                    continue

                counts[container_id] = counts.get(container_id, 0) + 1

            return counts

    def get_active_monitor_container_ids(self) -> Set[str]:
        """Return engine container IDs that currently host active monitor sessions."""
        return set(self.get_active_monitor_load_by_engine().keys())

    def remove_monitor_session(self, monitor_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            data = self.monitor_sessions.pop(monitor_id, None)
            return dict(data) if data else None

    def update_cache_stats(self, total_bytes: int, volume_count: int):
        """Update cache statistics in state."""
        with self._lock:
            self.cache_stats["total_bytes"] = total_bytes
            self.cache_stats["volume_count"] = volume_count
            self.cache_stats["last_updated"] = self.now().isoformat()

    def append_stat(self, stream_id: str, snap: StreamStatSnapshot):
        should_broadcast_stats = False
        with self._lock:
            arr = self.stream_stats.setdefault(stream_id, [])
            arr.append(snap)
            from ..core.config import cfg as _cfg
            if len(arr) > _cfg.STATS_HISTORY_MAX:
                del arr[: len(arr) - _cfg.STATS_HISTORY_MAX]

        # Throttle stats broadcast notifications to at most once every 2 seconds.
        with self._state_change_lock:
            now_monotonic = time.monotonic()
            if (
                self._last_stats_broadcast_monotonic <= 0
                or (now_monotonic - self._last_stats_broadcast_monotonic) >= 2.0
            ):
                self._last_stats_broadcast_monotonic = now_monotonic
                should_broadcast_stats = True

        stat_payload = {
            "stream_id": stream_id,
            "ts": snap.ts,
            "peers": snap.peers,
            "speed_down": snap.speed_down,
            "speed_up": snap.speed_up,
            "downloaded": snap.downloaded,
            "uploaded": snap.uploaded,
            "status": snap.status,
        }

        # Note: livepos data is intentionally not persisted to database
        # It's highly transient (updates every 1s) and would cause database bloat.
        # It's only kept in memory for real-time access via /streams endpoint.

        def db_work(session):
            session.add(
                StatRow(
                    stream_id=str(stat_payload["stream_id"]),
                    ts=stat_payload["ts"],
                    peers=stat_payload["peers"],
                    speed_down=stat_payload["speed_down"],
                    speed_up=stat_payload["speed_up"],
                    downloaded=stat_payload["downloaded"],
                    uploaded=stat_payload["uploaded"],
                    status=stat_payload["status"],
                )
            )

        self._enqueue_db_task(db_work)
        if should_broadcast_stats:
            self.broadcast_state_change(
                "stream_stats_updated",
                {
                    "stream_id": stream_id,
                },
            )

    def set_engine_vpn(self, container_id: str, vpn_container: str):
        """Set the VPN container assignment for an engine."""
        update_payload = None
        with self._lock:
            eng = self.engines.get(container_id)
            if eng:
                eng.vpn_container = vpn_container
                update_payload = {
                    "container_id": str(container_id),
                    "vpn_container": str(vpn_container),
                }

        if update_payload:
            def db_work(session):
                engine_row = session.get(EngineRow, update_payload["container_id"])
                if engine_row:
                    engine_row.vpn_container = update_payload["vpn_container"]

            self._enqueue_db_task(db_work)

    def mark_engine_draining(self, container_id: str, reason: str = "manual") -> bool:
        """Mark an engine as draining so it is excluded from new stream placement."""
        update_payload = None

        with self._lock:
            eng = self.engines.get(container_id)
            if not eng:
                return False

            labels = dict(eng.labels or {})
            lifecycle = str(
                labels.get(ENGINE_LIFECYCLE_LABEL)
                or labels.get("engine.lifecycle")
                or ""
            ).strip().lower()
            if lifecycle == "draining":
                return False

            now = self.now().isoformat()
            labels[ENGINE_LIFECYCLE_LABEL] = "draining"
            labels[ENGINE_DRAIN_REASON_LABEL] = str(reason or "manual")
            labels[ENGINE_DRAIN_REQUESTED_AT_LABEL] = now
            eng.labels = labels

            update_payload = {
                "container_id": str(container_id),
                "labels": dict(labels),
            }

        if update_payload:
            def db_work(session):
                engine_row = session.get(EngineRow, update_payload["container_id"])
                if engine_row:
                    engine_row.labels = dict(update_payload["labels"])

            self._enqueue_db_task(db_work)
            self.broadcast_state_change(
                "engine_marked_draining",
                {
                    "container_id": str(container_id),
                    "reason": str(reason or "manual"),
                },
            )

        return True

    def get_engines_by_vpn(self, vpn_container: str) -> List[EngineState]:
        """Get all engines assigned to a specific VPN container."""
        with self._lock:
            return [eng for eng in self.engines.values() if eng.vpn_container == vpn_container]

    def update_vpn_engine_forwarded_port(self, vpn_container: str, forwarded_port: Optional[int], forwarded_only: bool = False) -> int:
        """Update forwarded-port metadata for engines assigned to a VPN container."""
        with self._lock:
            updated = 0
            for engine in self.engines.values():
                if engine.vpn_container != vpn_container:
                    continue
                if forwarded_only and not engine.forwarded:
                    continue

                engine.forwarded_port = forwarded_port
                if forwarded_port is None:
                    engine.labels.pop("acestream.forwarded_port", None)
                else:
                    engine.labels["acestream.forwarded_port"] = str(int(forwarded_port))
                updated += 1

            return updated

    def load_from_db(self):
        from ..models.db_models import EngineRow, StreamRow
        from ..services.db import SessionLocal
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
                api_port = 62062
                api_port_raw = (e.labels or {}).get("host.api_port") or (e.labels or {}).get("acestream.api_port")
                if api_port_raw:
                    try:
                        api_port = int(api_port_raw)
                    except ValueError:
                        api_port = 62062
                
                self.engines[e.engine_key] = EngineState(container_id=e.engine_key, container_name=container_name,
                                                         host=e.host, port=e.port,
                                                         api_port=api_port,
                                                         labels=e.labels or {}, forwarded=forwarded,
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
            self.monitor_sessions.clear()
            self._scaling_intents.clear()
            self._dynamic_vpn_nodes.clear()

            try:
                from ..core.config import cfg
                self._desired_replica_count = cfg.MIN_REPLICAS
            except Exception:
                self._desired_replica_count = 0

            self._desired_vpn_node_count = 0
        
        # Also clear cumulative metrics tracking
        try:
            from ..services.metrics import reset_cumulative_metrics
            reset_cumulative_metrics()
        except Exception as e:
            logger.warning(f"Failed to reset cumulative metrics: {e}")

    def clear_database(self):
        """Clear all database state."""
        from ..models.db_models import EngineRow, StreamRow, StatRow
        from ..services.db import SessionLocal
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

    @staticmethod
    def _list_dynamic_vpn_managed_containers() -> List[object]:
        """List orchestrator-managed dynamic VPN containers (Gluetun nodes)."""
        from ..services.docker_client import get_client

        cli = get_client(timeout=30)
        try:
            return cli.containers.list(
                all=True,
                filters={"label": ["acestream-orchestrator.managed=true", "role=vpn_node"]},
            )
        finally:
            try:
                cli.close()
            except Exception:
                pass

    @classmethod
    def _collect_managed_cleanup_targets(cls) -> Tuple[List[object], int, int]:
        """Collect engine and dynamic VPN containers to remove during shutdown cleanup."""
        from ..services.health import list_managed

        engine_containers: List[object] = []
        vpn_containers: List[object] = []

        try:
            engine_containers = list_managed()
        except Exception as e:
            logger.warning(f"Failed to list managed engine containers: {e}")

        try:
            vpn_containers = cls._list_dynamic_vpn_managed_containers()
        except Exception as e:
            logger.warning(f"Failed to list managed dynamic VPN containers: {e}")

        combined: List[object] = []
        seen_ids: Set[str] = set()
        for container in [*engine_containers, *vpn_containers]:
            container_id = str(getattr(container, "id", "") or "").strip()
            if not container_id or container_id in seen_ids:
                continue
            seen_ids.add(container_id)
            combined.append(container)

        return combined, len(engine_containers), len(vpn_containers)

    def cleanup_all(self):
        """Full cleanup: stop containers, clear database and memory state."""
        logger.info("Starting full cleanup: stopping managed engine and dynamic VPN containers")

        # Stop the async DB worker so no persistence tasks race with cleanup.
        self._stop_db_worker.set()
        self._db_queue.put(None)
        try:
            self._db_worker.join(timeout=5.0)
        except Exception as e:
            logger.warning(f"Failed to join DB worker thread during cleanup: {e}")
        
        # Stop all managed containers in parallel
        containers_stopped = 0
        try:
            from ..services.provisioner import stop_container

            managed_containers, engine_count, vpn_count = self._collect_managed_cleanup_targets()
            if managed_containers:
                logger.info(
                    "Found %s cleanup targets (%s engine containers, %s dynamic VPN containers)",
                    len(managed_containers),
                    engine_count,
                    vpn_count,
                )
            else:
                logger.debug("Cleanup startup found no managed engine/VPN containers")
            
            if managed_containers:
                # Stop containers in parallel using ThreadPoolExecutor
                # This significantly improves shutdown performance
                def stop_single_container(container):
                    """Helper function to stop a single container."""
                    try:
                        logger.info(f"Forcibly destroying container {container.id[:12]}")
                        stop_container(container.id, force=True)
                        logger.info(f"Successfully destroyed container {container.id[:12]}")
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
            logger.warning(f"Failed to stop cleanup target containers: {e}")
        
        if containers_stopped > 0:
            logger.info(f"Stopped {containers_stopped} containers during cleanup")
        else:
            logger.debug("No containers stopped during cleanup")
        
        # Clear database state
        logger.debug("Clearing database state")
        self.clear_database()
        
        # Clear in-memory state
        logger.debug("Clearing in-memory state")
        self.clear_state()
        
        # Clear port allocations to prevent double-counting during reindex
        logger.debug("Clearing port allocations")
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

    def set_desired_replica_count(self, desired: int):
        with self._lock:
            self._desired_replica_count = max(0, int(desired))

    def get_desired_replica_count(self) -> int:
        with self._lock:
            return self._desired_replica_count

    def set_desired_vpn_node_count(self, desired: int):
        with self._lock:
            self._desired_vpn_node_count = max(0, int(desired))

    def get_desired_vpn_node_count(self) -> int:
        with self._lock:
            return self._desired_vpn_node_count

    def emit_scaling_intent(self, intent_type: str, details: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        """Record declarative scaling intents produced by the reconciliation loop."""
        now = self.now()
        intent = {
            "id": str(uuid.uuid4()),
            "intent_type": intent_type,
            "details": dict(details or {}),
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "result": None,
        }

        with self._lock:
            self._scaling_intents.append(intent)
            if len(self._scaling_intents) > self._max_scaling_intents:
                self._scaling_intents = self._scaling_intents[-self._max_scaling_intents :]

            return dict(intent)

    def resolve_scaling_intent(self, intent_id: str, status: str, result: Optional[Dict[str, object]] = None):
        with self._lock:
            for intent in reversed(self._scaling_intents):
                if intent.get("id") != intent_id:
                    continue
                intent["status"] = status
                intent["result"] = dict(result or {})
                intent["updated_at"] = self.now()
                break

    def list_scaling_intents(self, limit: int = 50) -> List[Dict[str, object]]:
        with self._lock:
            items = self._scaling_intents[-max(1, int(limit)) :]
            return [dict(item) for item in items]

    def list_pending_scaling_intents(self, intent_type: Optional[str] = None, limit: int = 200) -> List[Dict[str, object]]:
        with self._lock:
            pending = [
                intent
                for intent in self._scaling_intents
                if intent.get("status") == "pending"
                and (intent_type is None or intent.get("intent_type") == intent_type)
            ]
            if limit > 0:
                pending = pending[-limit:]
            return [dict(item) for item in pending]

    def set_target_engine_config(self, config_hash: str) -> Dict[str, object]:
        normalized_hash = str(config_hash or "").strip()
        if not normalized_hash:
            raise ValueError("config_hash cannot be empty")

        with self._lock:
            changed = normalized_hash != self._target_engine_config_hash
            if changed:
                self._target_engine_generation += 1
                self._target_engine_config_hash = normalized_hash

            return {
                "config_hash": self._target_engine_config_hash,
                "generation": self._target_engine_generation,
                "changed": changed,
            }

    def get_target_engine_config(self) -> Dict[str, object]:
        with self._lock:
            return {
                "config_hash": self._target_engine_config_hash,
                "generation": self._target_engine_generation,
            }

    def update_vpn_node_status(self, vpn_container: str, status: str, metadata: Optional[Dict[str, object]] = None):
        """Track VPN node health/runtime status from Docker events."""
        now = self.now()
        normalized = status.strip().lower()
        healthy = normalized in {"healthy", "running"}
        condition = "ready" if healthy else "notready"
        metadata = dict(metadata or {})

        with self._lock:
            previous_dynamic = self._dynamic_vpn_nodes.get(vpn_container)
            managed_dynamic = bool(metadata.get("managed_dynamic", True))
            previous = previous_dynamic or {}
            last_event_at = previous.get("last_event_at")
            # Only reset event age when readiness condition transitions (or first sighting).
            if previous.get("condition") != condition or last_event_at is None:
                last_event_at = now
            lifecycle = str(
                metadata.get(
                    "lifecycle",
                    previous.get("lifecycle", VPN_NODE_LIFECYCLE_ACTIVE),
                )
                or VPN_NODE_LIFECYCLE_ACTIVE
            ).strip().lower()
            if lifecycle not in {VPN_NODE_LIFECYCLE_ACTIVE, VPN_NODE_LIFECYCLE_DRAINING}:
                lifecycle = VPN_NODE_LIFECYCLE_ACTIVE

            draining_since = previous.get("draining_since")
            if lifecycle == VPN_NODE_LIFECYCLE_DRAINING and draining_since is None:
                draining_since = now
            if lifecycle == VPN_NODE_LIFECYCLE_ACTIVE:
                draining_since = None

            node_payload = {
                "container_name": vpn_container,
                "status": normalized,
                "healthy": healthy,
                "condition": condition,
                "last_event_at": last_event_at,
                "managed_dynamic": managed_dynamic,
                "lifecycle": lifecycle,
                "draining_since": draining_since,
                "provider": metadata.get("provider", previous.get("provider")),
                "protocol": metadata.get("protocol", previous.get("protocol")),
                "credential_id": metadata.get("credential_id", previous.get("credential_id")),
                "forwarded_port": metadata.get("forwarded_port", previous.get("forwarded_port")),
                "port_forwarding_supported": metadata.get(
                    "port_forwarding_supported",
                    previous.get("port_forwarding_supported", False),
                ),
            }
            self._dynamic_vpn_nodes[vpn_container] = node_payload

        self.broadcast_state_change(
            "vpn_node_status",
            {
                "vpn_container": vpn_container,
                "status": normalized,
                "condition": condition,
            },
        )

    def remove_vpn_node(self, vpn_container: str):
        with self._lock:
            self._dynamic_vpn_nodes.pop(vpn_container, None)

    def list_vpn_nodes(self) -> List[Dict[str, object]]:
        with self._lock:
            return [dict(node) for node in self._dynamic_vpn_nodes.values()]

    def get_healthy_vpn_nodes(self) -> List[str]:
        return [
            str(node.get("container_name"))
            for node in self.list_vpn_nodes()
            if node.get("container_name") and bool(node.get("healthy"))
        ]

    def get_ready_vpn_nodes(self) -> List[str]:
        return [
            str(node.get("container_name"))
            for node in self.list_vpn_nodes()
            if node.get("container_name") and str(node.get("condition", "")).lower() == "ready"
        ]

    def list_notready_vpn_nodes(self, dynamic_only: bool = False) -> List[Dict[str, object]]:
        nodes = [
            node
            for node in self.list_vpn_nodes()
            if str(node.get("condition", "")).lower() == "notready"
        ]
        if dynamic_only:
            return [node for node in nodes if bool(node.get("managed_dynamic"))]
        return nodes

    def list_dynamic_vpn_nodes(self) -> List[Dict[str, object]]:
        with self._lock:
            return [dict(node) for node in self._dynamic_vpn_nodes.values()]

    def set_vpn_node_lifecycle(
        self,
        vpn_container: str,
        lifecycle: str,
        metadata: Optional[Dict[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        normalized_lifecycle = str(lifecycle or "").strip().lower()
        if normalized_lifecycle not in {VPN_NODE_LIFECYCLE_ACTIVE, VPN_NODE_LIFECYCLE_DRAINING}:
            return None

        now = self.now()
        metadata = dict(metadata or {})

        with self._lock:
            previous = dict(self._dynamic_vpn_nodes.get(vpn_container) or {})
            if not previous:
                previous = {
                    "container_name": vpn_container,
                    "status": "unknown",
                    "healthy": False,
                    "condition": "notready",
                    "last_event_at": now,
                    "managed_dynamic": True,
                }

            previous["container_name"] = vpn_container
            previous["lifecycle"] = normalized_lifecycle
            previous["last_event_at"] = now

            if normalized_lifecycle == VPN_NODE_LIFECYCLE_DRAINING:
                previous["draining_since"] = previous.get("draining_since") or now
            else:
                previous["draining_since"] = None

            for key, value in metadata.items():
                previous[key] = value

            self._dynamic_vpn_nodes[vpn_container] = previous
            result = dict(previous)

        self.broadcast_state_change(
            "vpn_node_lifecycle",
            {
                "vpn_container": vpn_container,
                "lifecycle": normalized_lifecycle,
            },
        )
        return result

    def is_vpn_node_draining(self, vpn_container: Optional[str]) -> bool:
        if not vpn_container:
            return False

        with self._lock:
            node = self._dynamic_vpn_nodes.get(vpn_container)
            if not node:
                return False
            lifecycle = str(node.get("lifecycle", VPN_NODE_LIFECYCLE_ACTIVE)).strip().lower()
            return lifecycle == VPN_NODE_LIFECYCLE_DRAINING

    def list_draining_vpn_nodes(self, dynamic_only: bool = True) -> List[Dict[str, object]]:
        nodes = [
            node
            for node in self.list_vpn_nodes()
            if str(node.get("lifecycle", VPN_NODE_LIFECYCLE_ACTIVE)).strip().lower() == VPN_NODE_LIFECYCLE_DRAINING
        ]
        if dynamic_only:
            return [node for node in nodes if bool(node.get("managed_dynamic"))]
        return nodes

    def is_engine_draining(self, container_id: str) -> bool:
        with self._lock:
            engine = self.engines.get(container_id)
            if not engine:
                return False

            labels = engine.labels or {}
            lifecycle = str(
                labels.get(ENGINE_LIFECYCLE_LABEL)
                or labels.get("engine.lifecycle")
                or ""
            ).strip().lower()
            if lifecycle == "draining":
                return True

            if not engine.vpn_container:
                return False

            node = self._dynamic_vpn_nodes.get(engine.vpn_container)
            if not node:
                return False
            lifecycle = str(node.get("lifecycle", VPN_NODE_LIFECYCLE_ACTIVE)).strip().lower()
            return lifecycle == VPN_NODE_LIFECYCLE_DRAINING

    @staticmethod
    def _safe_int(value: Optional[str], default: Optional[int]) -> Optional[int]:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def apply_engine_docker_event(self, container_id: str, container_name: Optional[str], action: str, labels: Optional[Dict[str, str]] = None):
        """Apply Docker lifecycle events to in-memory engine state immediately."""
        normalized_action = action.strip().lower()

        if normalized_action in {"destroy", "stop"}:
            self.remove_engine(container_id)
            return

        labels = labels or {}
        engine_snapshot = None

        with self._lock:
            engine = self.engines.get(container_id)
            now = self.now()

            if normalized_action == "start":
                host_http = self._safe_int(labels.get("host.http_port"), 0) or 0
                host_api = self._safe_int(labels.get("host.api_port"), None)
                vpn_container = labels.get("acestream.vpn_container")
                forwarded = str(labels.get("acestream.forwarded", "false")).lower() == "true"
                engine_variant = labels.get("acestream.engine_variant")

                if not engine:
                    engine = EngineState(
                        container_id=container_id,
                        container_name=container_name,
                        host=vpn_container or container_name or "127.0.0.1",
                        port=host_http,
                        api_port=host_api,
                        labels=dict(labels),
                        forwarded=forwarded,
                        first_seen=now,
                        last_seen=now,
                        streams=[],
                        health_status="unknown",
                        last_health_check=None,
                        last_stream_usage=None,
                        vpn_container=vpn_container,
                        engine_variant=engine_variant,
                    )
                    self.engines[container_id] = engine
                else:
                    engine.container_name = container_name or engine.container_name
                    if host_http:
                        engine.port = host_http
                    if host_api is not None:
                        engine.api_port = host_api
                    if labels:
                        engine.labels.update(labels)
                    if vpn_container:
                        engine.vpn_container = vpn_container
                    if engine_variant:
                        engine.engine_variant = engine_variant
                    engine.forwarded = forwarded or engine.forwarded
                    engine.last_seen = now

            elif normalized_action == "die":
                if engine:
                    engine.health_status = "unhealthy"
                    engine.last_health_check = now
                    engine.last_seen = now

            elif normalized_action == "health_status: healthy":
                if engine:
                    engine.last_seen = now
                    engine.health_status = "healthy"
                    engine.last_health_check = now
                    logger.info(f"Engine {container_id[:12]} marked healthy via Docker event")

            elif normalized_action == "health_status: unhealthy":
                if engine:
                    engine.last_seen = now
                    engine.health_status = "unhealthy"
                    engine.last_health_check = now
                    logger.warning(f"Engine {container_id[:12]} marked unhealthy via Docker event")

            if engine:
                engine_snapshot = engine.model_copy(deep=True)

        if not engine_snapshot:
            return

        engine_payload = {
            "engine_key": engine_snapshot.container_id,
            "container_id": engine_snapshot.container_id,
            "container_name": engine_snapshot.container_name,
            "host": engine_snapshot.host,
            "port": engine_snapshot.port,
            "labels": dict(engine_snapshot.labels or {}),
            "forwarded": engine_snapshot.forwarded,
            "first_seen": engine_snapshot.first_seen,
            "last_seen": engine_snapshot.last_seen,
            "vpn_container": engine_snapshot.vpn_container,
        }

        def db_work(session):
            session.merge(EngineRow(**engine_payload))

        self._enqueue_db_task(db_work)
        self.broadcast_state_change(
            "engine_docker_event",
            {
                "container_id": container_id,
                "action": normalized_action,
            },
        )
    
    def set_forwarded_engine(self, container_id: str):
        """Mark an engine as the forwarded engine and clear forwarded flag from others.
        
        If the target engine is assigned to a VPN node, only engines on the same
        VPN have their forwarded flag cleared. Otherwise all forwarded flags are
        cleared before assigning the target.
        """
        db_updates: List[Dict[str, object]] = []
        with self._lock:
            # Get the target engine first to determine its VPN
            target_engine = self.engines.get(container_id)
            if not target_engine:
                logger.warning(f"Cannot set forwarded flag: engine {container_id[:12]} not found")
                return
            
            target_vpn = target_engine.vpn_container
            scope_to_target_vpn = bool(target_vpn)
            
            # Clear forwarded flag from engines
            for engine in self.engines.values():
                if engine.forwarded:
                    should_clear = engine.vpn_container == target_vpn if scope_to_target_vpn else True
                    
                    if should_clear:
                        engine.forwarded = False
                        db_updates.append(
                            {
                                "engine_key": engine.container_id,
                                "container_id": engine.container_id,
                                "container_name": engine.container_name,
                                "host": engine.host,
                                "port": engine.port,
                                "labels": dict(engine.labels or {}),
                                "forwarded": False,
                                "first_seen": engine.first_seen,
                                "last_seen": engine.last_seen,
                                "vpn_container": engine.vpn_container,
                            }
                        )
            
            # Set forwarded flag on the specified engine
            target_engine.forwarded = True
            db_updates.append(
                {
                    "engine_key": target_engine.container_id,
                    "container_id": target_engine.container_id,
                    "container_name": target_engine.container_name,
                    "host": target_engine.host,
                    "port": target_engine.port,
                    "labels": dict(target_engine.labels or {}),
                    "forwarded": True,
                    "first_seen": target_engine.first_seen,
                    "last_seen": target_engine.last_seen,
                    "vpn_container": target_engine.vpn_container,
                }
            )

        if db_updates:
            updates_payload = [dict(payload) for payload in db_updates]

            def db_work(session):
                for payload in updates_payload:
                    session.merge(EngineRow(**payload))

            self._enqueue_db_task(db_work)
            
            if target_vpn:
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
                from ..services.db import SessionLocal
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
