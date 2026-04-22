from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..models.schemas import EngineState, StreamState, StreamStartedEvent, StreamEndedEvent, StreamStatSnapshot
from ..shared.proxy_modes import normalize_proxy_mode
from ..models.db_models import EngineRow, StreamRow, StatRow
from ..shared.state_store import (
    StateStore,
    ACTIVE_MONITOR_SESSION_STATUSES,
    VPN_NODE_LIFECYCLE_ACTIVE,
    VPN_NODE_LIFECYCLE_DRAINING,
    ENGINE_LIFECYCLE_LABEL,
    ENGINE_DRAIN_REASON_LABEL,
    ENGINE_DRAIN_REQUESTED_AT_LABEL,
)
from ..shared.db_writer import DbWriter

logger = logging.getLogger(__name__)


class State(StateStore):
    """
    Full application state: pure in-memory StateStore + DB persistence + cross-plane lazy imports.
    """

    def __init__(self):
        super().__init__()
        self._db_writer = DbWriter()

    def _enqueue_db_task(self, task):
        self._db_writer.enqueue(task)

    # ------------------------------------------------------------------
    # Stream lifecycle (cross-cutting: inspect, metrics, proxy, hls)
    # ------------------------------------------------------------------

    def on_stream_started(self, evt: StreamStartedEvent) -> StreamState:
        container_name = None
        if evt.container_id:
            try:
                from ..infrastructure.inspect import get_container_name
                container_name = get_container_name(evt.container_id)
            except Exception:
                container_name = None

        with self._lock:
            eng = None
            if evt.container_id:
                eng = self.engines.get(evt.container_id)

                if not eng:
                    short_id = evt.container_id[:12]
                    for eid, engine in self.engines.items():
                        if (len(evt.container_id) >= 12 and eid.startswith(short_id)) or \
                           (evt.container_id == engine.container_name):
                            eng = engine
                            break

            if not eng:
                target_host = evt.engine.host
                target_port = evt.engine.port
                for existing_eng in self.engines.values():
                    if existing_eng.host == target_host and existing_eng.port == target_port:
                        eng = existing_eng
                        break

            if eng:
                key = eng.container_id
            else:
                key = evt.container_id or f"{evt.engine.host}:{evt.engine.port}"

            if evt.container_id:
                if not container_name:
                    container_name = f"container-{evt.container_id[:12]}"
            else:
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
                eng = EngineState(
                    container_id=key, container_name=container_name,
                    host=evt.engine.host, port=evt.engine.port,
                    api_port=api_port, labels=evt.labels or {}, forwarded=False,
                    first_seen=self.now(), last_seen=self.now(), streams=[],
                    health_status="unknown", last_health_check=None,
                    last_stream_usage=self.now(), vpn_container=None,
                )
                self.engines[key] = eng
            else:
                eng.host = evt.engine.host
                eng.port = evt.engine.port
                eng.last_seen = self.now()
                eng.last_stream_usage = self.now()
                if container_name and not eng.container_name:
                    eng.container_name = container_name
                if evt.labels:
                    eng.labels.update(evt.labels)
                if evt.labels and eng.api_port is None:
                    api_port_raw = evt.labels.get("host.api_port") or evt.labels.get("acestream.api_port")
                    if api_port_raw:
                        try:
                            eng.api_port = int(api_port_raw)
                        except ValueError:
                            pass

            stream_id = (evt.labels.get("stream_id") if evt.labels else None) or f"{evt.stream.key}|{evt.session.playback_session_id}"
            st = StreamState(
                id=stream_id, key_type=evt.stream.key_type, key=evt.stream.key,
                file_indexes=evt.stream.file_indexes,
                seekback=evt.stream.seekback,
                live_delay=(evt.stream.live_delay if evt.stream.live_delay is not None else evt.stream.seekback),
                control_mode=normalize_proxy_mode((evt.labels or {}).get("proxy.control_mode"), default=None),
                container_id=key, container_name=eng.container_name if eng else container_name,
                playback_session_id=evt.session.playback_session_id,
                stat_url=str(evt.session.stat_url or ""), command_url=str(evt.session.command_url or ""),
                is_live=bool(evt.session.is_live),
                bitrate=evt.session.bitrate,
                started_at=self.now(), status="started",
            )

            existing_stream = self.streams.get(stream_id)
            if existing_stream and existing_stream.key:
                existing_ids = self._streams_by_key.get(existing_stream.key)
                if existing_ids is not None:
                    existing_ids.discard(existing_stream.id)
                    if not existing_ids:
                        self._streams_by_key.pop(existing_stream.key, None)

            self.streams[stream_id] = st
            if st.key:
                self._streams_by_key[st.key].add(st.id)
            if stream_id not in eng.streams:
                eng.streams.append(stream_id)
            
            # Recalculate engine totals immediately on new stream
            self._recalculate_engine_aggregates(key)

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
            "bitrate": st.bitrate,
            "started_at": st.started_at,
            "status": st.status,
        }

        def db_work(session):
            session.merge(EngineRow(**engine_payload))
            session.merge(StreamRow(**stream_payload))

        self._enqueue_db_task(db_work)
        self.broadcast_state_change(
            "stream_started",
            {"stream_id": stream_id, "container_id": key},
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
                        st = s
                        break
            if not st:
                return None
            stream_id_for_metrics = st.id

            target_key = str(st.key or "")
            target_ids = [st.id]
            for candidate_id in set(self._streams_by_key.get(target_key, set())):
                if candidate_id == st.id:
                    continue
                candidate = self.streams.get(candidate_id)
                if not candidate:
                    continue
                if candidate.ended_at is not None:
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

                eng = self.engines.get(target_stream.container_id)
                if eng and target_stream.id in eng.streams:
                    eng.streams.remove(target_stream.id)
                    if len(eng.streams) == 0:
                        engine_became_idle = True
                        container_id_for_cleanup = target_stream.container_id

                    # Ensure engine aggregates are zeroed/updated when a stream leaves
                    self._recalculate_engine_aggregates(target_stream.container_id)

                self.streams.pop(target_stream.id, None)
                if target_stream.key and target_stream.key in self._streams_by_key:
                    indexed_ids = self._streams_by_key[target_stream.key]
                    indexed_ids.discard(target_stream.id)
                    if not indexed_ids:
                        self._streams_by_key.pop(target_stream.key, None)
                self.stream_stats.pop(target_stream.id, None)

                # Proactively clear monitor sessions for this stream to avoid "stuck yellow pipes"
                self.monitor_sessions.pop(target_stream.id, None)

        if ended_stream_payloads:
            payloads_for_db = [dict(item) for item in ended_stream_payloads]

            def db_work(session):
                for payload in payloads_for_db:
                    row = session.get(StreamRow, str(payload["id"]))
                    if row:
                        row.ended_at = payload["ended_at"]
                        row.status = str(payload["status"])

            self._enqueue_db_task(db_work)

        if stream_id_for_metrics:
            try:
                from ..observability.metrics import on_stream_ended as metrics_stream_ended
                metrics_stream_ended(stream_id_for_metrics)
            except Exception as e:
                logger.warning(f"Failed to clean up metrics for stream {stream_id_for_metrics}: {e}")



        if st:
            self.broadcast_state_change(
                "stream_ended",
                {"stream_id": st.id, "container_id": st.container_id},
            )

        return st

    # ------------------------------------------------------------------
    # Engine lifecycle (cross-cutting: recovery, engine_info, db_models)
    # ------------------------------------------------------------------

    def remove_engine(self, container_id: str) -> Optional[EngineState]:
        ended_stream_updates: List[Dict[str, object]] = []
        with self._lock:
            removed_engine = self.engines.pop(container_id, None)
            if removed_engine:
                streams_to_failover = [
                    s_id for s_id, stream in self.streams.items()
                    if stream.container_id == container_id and stream.status == "started"
                ]
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

                    try:
                        from ..control_plane.recovery import recover_stream
                        recover_stream(s_id, dead_vpn=removed_engine.vpn_container, failure_reason="engine_removed")
                    except Exception as e:
                        logger.error(f"Failed to trigger recovery for orphaned stream {s_id}: {e}")

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
                from ..infrastructure.engine_info import invalidate_engine_version_cache
                invalidate_engine_version_cache(container_id)
            except Exception:
                pass

            self.broadcast_state_change(
                "engine_removed",
                {"container_id": removed_engine_id},
            )

        return removed_engine

    def reassign_active_streams_to_engine_by_key(
        self,
        *,
        stream_key: str,
        old_container_id: str,
        new_engine: EngineState,
        session_updates: Optional[Dict[str, object]] = None,
    ) -> int:
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

            for stream_id in set(self._streams_by_key.get(normalized_key, set())):
                stream = self.streams.get(stream_id)
                if not stream:
                    continue
                if stream.status not in {"started", "pending_failover"}:
                    continue
                if str(stream.key or "") != normalized_key:
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

    def append_stat(self, stream_id: str, snap: StreamStatSnapshot):
        should_broadcast_stats = False
        with self._lock:
            arr = self.stream_stats.setdefault(stream_id, [])
            arr.append(snap)

            st = self.streams.get(stream_id)
            if st:
                st.peers = snap.peers if snap.peers is not None else st.peers
                st.speed_down = snap.speed_down if snap.speed_down is not None else st.speed_down
                st.speed_up = snap.speed_up if snap.speed_up is not None else st.speed_up
                st.downloaded = snap.downloaded if snap.downloaded is not None else st.downloaded
                st.uploaded = snap.uploaded if snap.uploaded is not None else st.uploaded
                if snap.status:
                    st.status = "started"
                if snap.bitrate is not None:
                    st.bitrate = snap.bitrate
                if snap.livepos:
                    st.livepos = snap.livepos
                if snap.proxy_buffer_pieces is not None:
                    st.proxy_buffer_pieces = snap.proxy_buffer_pieces

            if st and st.container_id:
                self._recalculate_engine_aggregates(st.container_id)

            from ..core.config import cfg as _cfg
            if len(arr) > _cfg.STATS_HISTORY_MAX:
                del arr[: len(arr) - _cfg.STATS_HISTORY_MAX]

        import time as _time
        with self._state_change_lock:
            now_monotonic = _time.monotonic()
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
                {"stream_id": stream_id},
            )
    def _recalculate_engine_aggregates(self, engine_id: str):
        """Recalculate total throughput and peer counts for an engine. Internal use only (lock expected)."""
        eng = self.engines.get(engine_id)
        if not eng:
            return

        engine_streams = [
            self.streams.get(sid) for sid in eng.streams
            if sid in self.streams
        ]
        active_streams = [s for s in engine_streams if s and s.status == "started"]

        eng.total_speed_down = sum(int(s.speed_down or 0) for s in active_streams)
        eng.total_speed_up = sum(int(s.speed_up or 0) for s in active_streams)
        eng.total_peers = sum(int(s.peers or 0) for s in active_streams)
        eng.stream_count = len(active_streams)

    def set_engine_vpn(self, container_id: str, vpn_container: str):
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

    def set_forwarded_engine(self, container_id: str):
        db_updates: List[Dict[str, object]] = []
        with self._lock:
            target_engine = self.engines.get(container_id)
            if not target_engine:
                logger.warning(f"Cannot set forwarded flag: engine {container_id[:12]} not found")
                return

            target_vpn = target_engine.vpn_container
            scope_to_target_vpn = bool(target_vpn)

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

    # ------------------------------------------------------------------
    # VPN node status (cross-cutting: autoscaler nudge)
    # ------------------------------------------------------------------

    def update_vpn_node_status(self, vpn_container: str, status: str, metadata: Optional[Dict[str, object]] = None):
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
                "assigned_hostname": metadata.get("assigned_hostname", previous.get("assigned_hostname")),
                "forwarded_port": metadata.get("forwarded_port", previous.get("forwarded_port")),
                "port_forwarding_supported": metadata.get(
                    "port_forwarding_supported",
                    previous.get("port_forwarding_supported", False),
                ),
            }
            self._dynamic_vpn_nodes[vpn_container] = node_payload

        if previous_dynamic and previous_dynamic.get("condition") != condition and condition == "ready":
            try:
                from ..control_plane.autoscaler import engine_controller
                engine_controller.request_reconcile(reason=f"vpn_ready:{vpn_container}")
            except Exception as e:
                logger.debug(f"Failed to nudge autoscaler on VPN readiness: {e}")

        self.broadcast_state_change(
            "vpn_node_status",
            {
                "vpn_container": vpn_container,
                "status": normalized,
                "condition": condition,
            },
        )

    # ------------------------------------------------------------------
    # Docker event handler (cross-cutting: autoscaler nudge, db_models)
    # ------------------------------------------------------------------

    def apply_engine_docker_event(self, container_id: str, container_name: Optional[str], action: str, labels: Optional[Dict[str, str]] = None):
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
                    previous_status = engine.health_status
                    engine.last_seen = now
                    engine.health_status = "healthy"
                    engine.last_health_check = now
                    logger.info(f"Engine {container_id[:12]} marked healthy via Docker event")

                    if previous_status != "healthy":
                        try:
                            from ..control_plane.autoscaler import engine_controller
                            engine_controller.request_reconcile(reason=f"engine_healthy:{container_id[:12]}")
                        except Exception as e:
                            logger.debug(f"Failed to nudge autoscaler on engine health: {e}")

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

    # ------------------------------------------------------------------
    # Health bulk update (cross-cutting: health module)
    # ------------------------------------------------------------------

    def update_engines_health(self):
        from ..control_plane.health import check_acestream_health

        with self._lock:
            targets = [
                (engine.container_id, engine.host, engine.port)
                for engine in self.engines.values()
            ]

        health_updates = {}
        for container_id, host, port in targets:
            health_updates[container_id] = check_acestream_health(host, port)

        with self._lock:
            for container_id, health_status in health_updates.items():
                engine = self.engines.get(container_id)
                if not engine:
                    continue
                engine.health_status = health_status
                engine.last_health_check = self.now()

    # ------------------------------------------------------------------
    # DB load / clear / cleanup
    # ------------------------------------------------------------------

    def load_from_db(self):
        from ..models.db_models import EngineRow, StreamRow
        from ..persistence.db import SessionLocal
        with SessionLocal() as s:
            for e in s.query(EngineRow).all():
                first_seen = e.first_seen.replace(tzinfo=timezone.utc) if e.first_seen.tzinfo is None else e.first_seen
                last_seen = e.last_seen.replace(tzinfo=timezone.utc) if e.last_seen.tzinfo is None else e.last_seen
                container_name = getattr(e, "container_name", None)
                if not container_name and e.container_id:
                    from ..infrastructure.inspect import get_container_name
                    container_name = get_container_name(e.container_id)
                    if not container_name:
                        container_name = f"container-{e.container_id[:12]}"
                elif not container_name:
                    container_name = f"engine-{e.host}-{e.port}"

                forwarded = getattr(e, "forwarded", False)
                vpn_container = getattr(e, "vpn_container", None)
                api_port = 62062
                api_port_raw = (e.labels or {}).get("host.api_port") or (e.labels or {}).get("acestream.api_port")
                if api_port_raw:
                    try:
                        api_port = int(api_port_raw)
                    except ValueError:
                        api_port = 62062

                self.engines[e.engine_key] = EngineState(
                    container_id=e.engine_key, container_name=container_name,
                    host=e.host, port=e.port,
                    api_port=api_port,
                    labels=e.labels or {}, forwarded=forwarded,
                    first_seen=first_seen, last_seen=last_seen, streams=[],
                    health_status="unknown", last_health_check=None, last_stream_usage=None,
                    vpn_container=vpn_container,
                )

            for r in s.query(StreamRow).filter(StreamRow.status == "started").all():
                started_at = r.started_at.replace(tzinfo=timezone.utc) if r.started_at.tzinfo is None else r.started_at
                ended_at = r.ended_at.replace(tzinfo=timezone.utc) if r.ended_at and r.ended_at.tzinfo is None else r.ended_at
                st = StreamState(
                    id=r.id, key_type=r.key_type, key=r.key, container_id=r.engine_key,
                    playback_session_id=r.playback_session_id, stat_url=r.stat_url,
                    command_url=r.command_url, is_live=r.is_live,
                    started_at=started_at, ended_at=ended_at, status=r.status,
                )
                self.streams[st.id] = st
                if st.key:
                    self._streams_by_key[st.key].add(st.id)
                eng = self.engines.get(r.engine_key)
                if eng and st.id not in eng.streams:
                    eng.streams.append(st.id)

    def clear_state(self):
        with self._lock:
            self.engines.clear()
            self.streams.clear()
            self._streams_by_key.clear()
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

        try:
            from ..observability.metrics import reset_cumulative_metrics
            reset_cumulative_metrics()
        except Exception as e:
            logger.warning(f"Failed to reset cumulative metrics: {e}")

    def clear_database(self):
        from ..models.db_models import EngineRow, StreamRow, StatRow
        from ..persistence.db import SessionLocal
        with SessionLocal() as s:
            try:
                s.query(StatRow).delete()
                s.query(StreamRow).delete()
                s.query(EngineRow).delete()
                s.commit()
            except Exception as e:
                s.rollback()
                logger.debug(f"Database cleanup skipped (tables may not exist): {e}")

    def cleanup_ended_streams(self, max_age_seconds: int = 3600) -> int:
        from datetime import timedelta

        with self._lock:
            now = self.now()
            cutoff_time = now - timedelta(seconds=max_age_seconds)

            streams_to_remove = []
            for stream_id, stream in self.streams.items():
                if stream.status == "ended" and stream.ended_at and stream.ended_at < cutoff_time:
                    streams_to_remove.append(stream_id)

            for stream_id in streams_to_remove:
                stream = self.streams.get(stream_id)
                del self.streams[stream_id]
                if stream and stream.key and stream.key in self._streams_by_key:
                    indexed_ids = self._streams_by_key[stream.key]
                    indexed_ids.discard(stream_id)
                    if not indexed_ids:
                        self._streams_by_key.pop(stream.key, None)
                if stream_id in self.stream_stats:
                    del self.stream_stats[stream_id]

        if streams_to_remove:
            try:
                from ..models.db_models import StreamRow, StatRow
                from ..persistence.db import SessionLocal
                with SessionLocal() as s:
                    s.query(StatRow).filter(StatRow.stream_id.in_(streams_to_remove)).delete(synchronize_session=False)
                    s.query(StreamRow).filter(StreamRow.id.in_(streams_to_remove)).delete(synchronize_session=False)
                    s.commit()
                    logger.info(f"Cleaned up {len(streams_to_remove)} ended streams older than {max_age_seconds}s")
            except Exception as e:
                logger.warning(f"Failed to clean up ended streams from database: {e}")

        return len(streams_to_remove)

    # ------------------------------------------------------------------
    # Shutdown cleanup (cross-cutting: provisioner, vpn_provisioner, ports)
    # ------------------------------------------------------------------

    @staticmethod
    def _list_dynamic_vpn_managed_containers() -> List[object]:
        from ..infrastructure.docker_client import get_client

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
        from ..control_plane.health import list_managed

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

        return combined, engine_containers, vpn_containers

    async def cleanup_all(self):
        logger.info("Starting full cleanup: stopping managed engine and dynamic VPN containers")

        self._db_writer.stop()
        try:
            await asyncio.to_thread(self._db_writer.join, 5.0)
        except Exception as e:
            logger.warning(f"Failed to join DB worker thread during cleanup: {e}")

        containers_stopped = 0
        try:
            from ..control_plane.provisioner import stop_container
            from ..vpn.vpn_provisioner import vpn_provisioner

            _, engine_containers, vpn_containers = self._collect_managed_cleanup_targets()

            tasks = []

            for container in engine_containers:
                cid = str(getattr(container, "id", "")).strip()
                if not cid:
                    continue

                async def _destroy_engine(c_id):
                    try:
                        logger.info(f"Forcibly destroying engine container {c_id[:12]}")
                        await asyncio.to_thread(stop_container, c_id, force=True)
                        logger.info(f"Successfully destroyed engine container {c_id[:12]}")
                        return True
                    except Exception as e:
                        logger.warning(f"Failed to stop engine container {c_id}: {e}")
                        return False

                tasks.append(_destroy_engine(cid))

            for container in vpn_containers:
                cid = str(getattr(container, "id", "")).strip()
                if not cid:
                    continue

                async def _destroy_vpn(c_id):
                    try:
                        logger.info(f"Forcibly destroying VPN node {c_id[:12]} (releasing lease)")
                        await vpn_provisioner.destroy_node(c_id, force=True, release_credential=True)
                        logger.info(f"Successfully destroyed VPN node {c_id[:12]} and released lease")
                        return True
                    except Exception as e:
                        logger.warning(f"Failed to destroy VPN node {c_id}: {e}")
                        return False

                tasks.append(_destroy_vpn(cid))

            if tasks:
                logger.info(
                    "Executing parallel cleanup for %s targets (%s engines, %s VPN nodes)",
                    len(tasks), len(engine_containers), len(vpn_containers),
                )
                results = await asyncio.gather(*tasks, return_exceptions=True)
                containers_stopped = sum(1 for r in results if r is True)
            else:
                logger.debug("Cleanup found no managed engine/VPN containers")

        except Exception as e:
            logger.warning(f"Failed to stop cleanup target containers: {e}")

        if containers_stopped > 0:
            logger.info(f"Stopped {containers_stopped} containers during cleanup")
        else:
            logger.debug("No containers stopped during cleanup")

        logger.debug("Clearing database state")
        await asyncio.to_thread(self.clear_database)

        logger.debug("Clearing in-memory state")
        self.clear_state()

        logger.debug("Clearing port allocations")
        try:
            from ..infrastructure.ports import alloc
            alloc.clear_all_allocations()
        except Exception as e:
            logger.warning(f"Failed to clear port allocations: {e}")

        logger.info("Full cleanup completed")


state = State()


def load_state_from_db():
    state.load_from_db()


async def cleanup_on_shutdown():
    await state.cleanup_all()
