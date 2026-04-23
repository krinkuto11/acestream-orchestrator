from __future__ import annotations

from collections import defaultdict
import threading
import time
import uuid
import logging
from typing import Any, Callable, Dict, List, Optional, Set
from datetime import datetime, timezone

from ..models.schemas import EngineState, StreamState, StreamStatSnapshot

logger = logging.getLogger(__name__)

ACTIVE_MONITOR_SESSION_STATUSES: Set[str] = {"starting", "running", "stuck", "reconnecting"}
VPN_NODE_LIFECYCLE_ACTIVE = "active"
VPN_NODE_LIFECYCLE_DRAINING = "draining"
ENGINE_LIFECYCLE_LABEL = "acestream.lifecycle"
ENGINE_DRAIN_REASON_LABEL = "acestream.drain_reason"
ENGINE_DRAIN_REQUESTED_AT_LABEL = "acestream.drain_requested_at"


class StateStore:
    """
    Pure in-memory state container.

    Holds all in-process data structures and pub/sub machinery.
    No imports from persistence, proxy, control_plane, or vpn planes.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.engines: Dict[str, EngineState] = {}
        self.streams: Dict[str, StreamState] = {}
        self._streams_by_key: Dict[str, Set[str]] = defaultdict(set)
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
        self._lookahead_layer: Optional[int] = None
        self.cache_stats = {
            "total_bytes": 0,
            "volume_count": 0,
            "last_updated": None,
        }

        try:
            from ..core.config import cfg
            self._desired_replica_count = cfg.MIN_REPLICAS
        except Exception:
            self._desired_replica_count = 0

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _safe_non_negative_float(value: Any, default: Optional[float] = None) -> Optional[float]:
        if value is None:
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed or parsed in (float("inf"), float("-inf")):
            return default
        return max(0.0, parsed)

    @staticmethod
    def _safe_int(value: Optional[str], default: Optional[int]) -> Optional[int]:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Engines
    # ------------------------------------------------------------------

    def list_engines(self) -> List[EngineState]:
        with self._lock:
            return list(self.engines.values())

    def get_engine(self, container_id: str) -> Optional[EngineState]:
        with self._lock:
            return self.engines.get(container_id)

    def get_engines_by_vpn(self, vpn_container: str) -> List[EngineState]:
        with self._lock:
            return [eng for eng in self.engines.values() if eng.vpn_container == vpn_container]

    def update_vpn_engine_forwarded_port(self, vpn_container: str, forwarded_port: Optional[int], forwarded_only: bool = False) -> int:
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

    def update_engine_health(self, container_id: str, health_status: str):
        with self._lock:
            engine = self.engines.get(container_id)
            if engine:
                engine.health_status = health_status
                engine.last_health_check = self.now()

    def get_forwarded_engine(self) -> Optional[EngineState]:
        with self._lock:
            for engine in self.engines.values():
                if engine.forwarded:
                    return engine
            return None

    def has_forwarded_engine(self) -> bool:
        return self.get_forwarded_engine() is not None

    def get_forwarded_engine_for_vpn(self, vpn_container: str) -> Optional[EngineState]:
        with self._lock:
            for engine in self.engines.values():
                if engine.forwarded and engine.vpn_container == vpn_container:
                    return engine
            return None

    def has_forwarded_engine_for_vpn(self, vpn_container: str) -> bool:
        return self.get_forwarded_engine_for_vpn(vpn_container) is not None

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

    # ------------------------------------------------------------------
    # Streams
    # ------------------------------------------------------------------

    def list_streams(self, status: Optional[str] = None, container_id: Optional[str] = None) -> List[StreamState]:
        with self._lock:
            res = list(self.streams.values())
            if status:
                res = [s for s in res if s.status == status]
            if container_id:
                res = [s for s in res if s.container_id == container_id]
            return res

    def get_stream(self, stream_id: str) -> Optional[StreamState]:
        with self._lock:
            return self.streams.get(stream_id)

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
        audio_codec: Optional[str] = None,
    ):
        with self._lock:
            st = self.streams.get(stream_id)
            if not st:
                logger.warning(f"Cannot update metadata for unknown stream {stream_id}")
                return

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

    def get_stream_stats(self, stream_id: str):
        with self._lock:
            return self.stream_stats.get(stream_id, [])

    def list_streams_with_stats(self, status: Optional[str] = None, container_id: Optional[str] = None) -> List[StreamState]:
        with self._lock:
            streams = list(self.streams.values())
            if status:
                streams = [s for s in streams if s.status == status]
            if container_id:
                streams = [s for s in streams if s.container_id == container_id]

            enriched_streams = []
            for stream in streams:
                enriched = stream.model_copy()

                if stream.status == "started":
                    stats = self.stream_stats.get(stream.id, [])
                    if stats:
                        latest_stat = stats[-1]
                        enriched.peers = latest_stat.peers
                        enriched.speed_down = latest_stat.speed_down
                        enriched.speed_up = latest_stat.speed_up
                        enriched.downloaded = latest_stat.downloaded
                        enriched.uploaded = latest_stat.uploaded
                        enriched.livepos = latest_stat.livepos

                        if hasattr(latest_stat, "proxy_buffer_pieces"):
                            enriched.proxy_buffer_pieces = latest_stat.proxy_buffer_pieces
                else:
                    enriched.peers = None
                    enriched.speed_down = None
                    enriched.speed_up = None
                    enriched.livepos = None
                    stats = self.stream_stats.get(stream.id, [])
                    if stats:
                        latest_stat = stats[-1]
                        enriched.downloaded = latest_stat.downloaded
                        enriched.uploaded = latest_stat.uploaded

                enriched_streams.append(enriched)

            return enriched_streams

    # ------------------------------------------------------------------
    # Monitor sessions
    # ------------------------------------------------------------------

    def get_realtime_snapshot(self):
        with self._lock:
            return {
                "engines": list(self.engines.values()),
                "streams": list(self.streams.values()),
                "stream_stats": dict(self.stream_stats),
                "monitor_sessions": dict(self.monitor_sessions),
                "cache_stats": dict(self.cache_stats),
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
        return set(self.get_active_monitor_load_by_engine().keys())

    def remove_monitor_session(self, monitor_id: str) -> Optional[Dict[str, object]]:
        with self._lock:
            data = self.monitor_sessions.pop(monitor_id, None)
            return dict(data) if data else None

    def update_cache_stats(self, total_bytes: int, volume_count: int):
        with self._lock:
            self.cache_stats["total_bytes"] = total_bytes
            self.cache_stats["volume_count"] = volume_count
            self.cache_stats["last_updated"] = self.now().isoformat()

    # ------------------------------------------------------------------
    # Desired replica / VPN counts
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Scaling intents
    # ------------------------------------------------------------------

    def emit_scaling_intent(self, intent_type: str, details: Optional[Dict[str, object]] = None) -> Dict[str, object]:
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
                self._scaling_intents = self._scaling_intents[-self._max_scaling_intents:]

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
            items = self._scaling_intents[-max(1, int(limit)):]
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

    def is_forwarded_engine_pending(self, vpn_container: str) -> bool:
        with self._lock:
            for intent in self._scaling_intents:
                if intent.get("status") == "pending" and intent.get("intent_type") == "create_request":
                    details = intent.get("details", {})
                    if details.get("vpn_container") == vpn_container and details.get("forwarded"):
                        return True
            return False

    def has_pending_forwarded_engine(self) -> bool:
        with self._lock:
            for intent in self._scaling_intents:
                if intent.get("status") == "pending" and intent.get("intent_type") == "create_request":
                    details = intent.get("details", {})
                    if not details.get("vpn_container") and details.get("forwarded"):
                        return True
            return False

    # ------------------------------------------------------------------
    # Target engine config
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # VPN nodes
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Lookahead layer
    # ------------------------------------------------------------------

    def set_lookahead_layer(self, layer: int) -> None:
        with self._lock:
            self._lookahead_layer = layer
            logger.info(f"Lookahead layer set to {layer} - next lookahead trigger will wait until all engines reach layer {layer}")

    def get_lookahead_layer(self) -> Optional[int]:
        with self._lock:
            return self._lookahead_layer

    def reset_lookahead_layer(self) -> None:
        with self._lock:
            if self._lookahead_layer is not None:
                logger.info(f"Resetting lookahead layer (was {self._lookahead_layer})")
            self._lookahead_layer = None
