"""
Go control plane state bridge.

Subscribes to the Redis pub/sub channel published by the Go control plane
and keeps Python's in-memory engine/VPN-node state in sync.

Also writes stream counts to Redis so the Go autoscaler has accurate data.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..services.state import State

logger = logging.getLogger(__name__)

# Redis key prefixes (mirrors Go internal/rediskeys/keys.go)
_CP_ENGINE_PREFIX = "cp:engine:"
_CP_VPN_NODE_PREFIX = "cp:vpn_node:"
_CP_ENGINES_INDEX = "cp:engines:all"
_CP_VPN_NODES_INDEX = "cp:vpn_nodes:all"
_CP_STATE_CHANGED = "cp:state_changed"
_CP_STREAM_COUNTS = "cp:stream_counts"


class GoStateBridge:
    """
    Subscribes to cp:state_changed and syncs Go-managed engine/VPN state
    into Python's in-memory state store.
    """

    def __init__(self, state: "State", redis_client):
        self._state = state
        self._rdb = redis_client
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        # Bootstrap state from existing Redis entries written by Go.
        self._bootstrap()
        self._thread = threading.Thread(target=self._run_subscribe, daemon=True, name="go-state-bridge")
        self._thread.start()
        logger.info("GoStateBridge started")

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("GoStateBridge stopped")

    # ── Bootstrap ─────────────────────────────────────────────────────────

    def _bootstrap(self):
        """Read all Go-written engine/VPN entries from Redis on startup."""
        try:
            rdb = self._rdb
            # Use a decode_responses=True view for JSON parsing
            engine_ids = rdb.smembers(_CP_ENGINES_INDEX)
            for raw_id in engine_ids:
                cid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
                self._sync_engine(cid)

            vpn_names = rdb.smembers(_CP_VPN_NODES_INDEX)
            for raw_name in vpn_names:
                name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name
                self._sync_vpn_node(name)

            logger.info(
                "GoStateBridge bootstrap: %d engines, %d VPN nodes",
                len(engine_ids),
                len(vpn_names),
            )
        except Exception as exc:
            logger.warning("GoStateBridge bootstrap failed: %s", exc)

    # ── Subscription loop ─────────────────────────────────────────────────

    def _run_subscribe(self):
        while self._running and not self._stop_event.is_set():
            try:
                pubsub = self._rdb.pubsub()
                pubsub.subscribe(_CP_STATE_CHANGED)
                logger.debug("GoStateBridge subscribed to %s", _CP_STATE_CHANGED)
                for message in pubsub.listen():
                    if self._stop_event.is_set():
                        break
                    if message["type"] != "message":
                        continue
                    raw = message["data"]
                    payload = raw.decode() if isinstance(raw, bytes) else raw
                    self._handle_message(payload)
            except Exception as exc:
                if self._running:
                    logger.warning("GoStateBridge subscription error: %s; reconnecting in 2s", exc)
                    self._stop_event.wait(timeout=2)

    def _handle_message(self, payload: str):
        try:
            if payload.startswith("engine_updated:"):
                cid = payload[len("engine_updated:"):]
                self._sync_engine(cid)
            elif payload.startswith("engine_removed:"):
                cid = payload[len("engine_removed:"):]
                self._remove_engine(cid)
            elif payload.startswith("vpn_updated:"):
                name = payload[len("vpn_updated:"):]
                self._sync_vpn_node(name)
            elif payload.startswith("vpn_removed:"):
                name = payload[len("vpn_removed:"):]
                self._remove_vpn_node(name)
        except Exception as exc:
            logger.debug("GoStateBridge: error handling message '%s': %s", payload, exc)

    # ── Engine sync ───────────────────────────────────────────────────────

    def _sync_engine(self, container_id: str):
        try:
            raw = self._rdb.get(_CP_ENGINE_PREFIX + container_id)
            if not raw:
                return
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            self._upsert_engine(container_id, data)
        except Exception as exc:
            logger.debug("GoStateBridge: failed to sync engine %s: %s", container_id[:12], exc)

    def _upsert_engine(self, container_id: str, data: dict):
        from ..models.schemas import EngineState

        now = datetime.now(timezone.utc)
        with self._state._lock:
            existing = self._state.engines.get(container_id)
            if existing:
                # Preserve stream tracking from Python side; update metadata from Go.
                existing.container_name = data.get("container_name") or existing.container_name
                existing.host = data.get("host", existing.host)
                existing.port = data.get("port", existing.port)
                existing.api_port = data.get("api_port") or existing.api_port
                existing.forwarded = data.get("forwarded", existing.forwarded)
                existing.vpn_container = data.get("vpn_container") or existing.vpn_container
                _hs = data.get("health_status", "")
                if _hs in ("healthy", "unhealthy", "unknown"):
                    existing.health_status = _hs
                existing.last_seen = now
                existing.labels = data.get("labels") or existing.labels
            else:
                _hs = data.get("health_status", "unknown")
                if _hs not in ("healthy", "unhealthy", "unknown"):
                    _hs = "unknown"
                eng = EngineState(
                    container_id=container_id,
                    container_name=data.get("container_name", ""),
                    host=data.get("host", ""),
                    port=data.get("port", 6878),
                    api_port=data.get("api_port"),
                    labels=data.get("labels") or {},
                    forwarded=data.get("forwarded", False),
                    vpn_container=data.get("vpn_container"),
                    health_status=_hs,
                    first_seen=now,
                    last_seen=now,
                )
                self._state.engines[container_id] = eng

    def _remove_engine(self, container_id: str):
        with self._state._lock:
            if container_id in self._state.engines:
                del self._state.engines[container_id]
                logger.debug("GoStateBridge: removed engine %s", container_id[:12])

    # ── VPN node sync ─────────────────────────────────────────────────────

    def _sync_vpn_node(self, name: str):
        try:
            raw = self._rdb.get(_CP_VPN_NODE_PREFIX + name)
            if not raw:
                return
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            self._upsert_vpn_node(name, data)
        except Exception as exc:
            logger.debug("GoStateBridge: failed to sync VPN node %s: %s", name, exc)

    def _upsert_vpn_node(self, name: str, data: dict):
        with self._state._lock:
            existing = self._state._dynamic_vpn_nodes.get(name)
            node = dict(existing) if existing else {}
            node.update({
                "container_name": data.get("container_name", name),
                "container_id": data.get("container_id", ""),
                "status": data.get("status", "running"),
                "healthy": data.get("healthy", False),
                "condition": data.get("condition", ""),
                "provider": data.get("provider", ""),
                "managed_dynamic": data.get("managed_dynamic", False),
                "port_forwarding_supported": data.get("port_forwarding_supported", False),
                "lifecycle": data.get("lifecycle", "active"),
            })
            self._state._dynamic_vpn_nodes[name] = node

    def _remove_vpn_node(self, name: str):
        with self._state._lock:
            self._state._dynamic_vpn_nodes.pop(name, None)
            logger.debug("GoStateBridge: removed VPN node %s", name)


# ── Stream count publisher ─────────────────────────────────────────────────────

def publish_stream_count(redis_client, container_id: str, count: int):
    """Write per-engine stream count to Redis for Go autoscaler consumption."""
    try:
        if count > 0:
            redis_client.hset(_CP_STREAM_COUNTS, container_id, str(count))
        else:
            redis_client.hdel(_CP_STREAM_COUNTS, container_id)
    except Exception as exc:
        logger.debug("publish_stream_count failed for %s: %s", container_id[:12], exc)
