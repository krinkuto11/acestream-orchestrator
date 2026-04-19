import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from ..proxy.utils import sanitize_stream_id


logger = logging.getLogger(__name__)


class ClientTrackingService:
    """Unified in-memory client tracker used by TS and HLS data paths."""

    def __init__(self):
        self._lock = threading.RLock()
        self._clients: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self._rate_state: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
        self._redis = None

    def set_redis_client(self, redis_client: Any):
        """Inject Redis client for PubSub event generation."""
        self._redis = redis_client

    def _publish_client_event(self, event_type: str, stream_id: str, client_row: Dict[str, Any]):
        """Publish client lifecycle event to Redis PubSub for real-time UI updates."""
        if not self._redis or not stream_id:
            return

        try:
            from ..proxy.redis_keys import RedisKeys
            
            # Build event payload compatible with proxy/client_manager.py format
            event_data = {
                "event": event_type,
                "content_id": stream_id,
                "client_id": client_row.get("client_id"),
                "worker_id": client_row.get("worker_id") or "orchestrator",
                "timestamp": time.time(),
                "protocol": client_row.get("protocol"),
                "ip_address": client_row.get("ip_address"),
                "user_agent": client_row.get("user_agent"),
            }

            # Include remaining client count for disconnection events
            if event_type == "client_disconnected":
                event_data["remaining_clients"] = self.count_active_clients(stream_id=stream_id)

            channel = RedisKeys.events_channel(stream_id)
            self._redis.publish(channel, json.dumps(event_data))
        except Exception as e:
            # Metrics/events errors should never compromise data path stability
            pass

    @staticmethod
    def _normalize_protocol(protocol: Optional[str]) -> str:
        value = str(protocol or "").strip().upper()
        return value if value in {"TS", "HLS"} else "TS"

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _key(protocol: str, stream_id: str, client_id: str) -> Tuple[str, str, str]:
        return (protocol, str(stream_id), str(client_id))

    def _emit_connect_metric(self, protocol: str):
        try:
            from ..observability.metrics import observe_proxy_client_connect

            observe_proxy_client_connect(protocol)
        except Exception:
            pass

    def _emit_disconnect_metric(self, protocol: str):
        try:
            from ..observability.metrics import observe_proxy_client_disconnect

            observe_proxy_client_disconnect(protocol)
        except Exception:
            pass

    def register_client(
        self,
        *,
        client_id: str,
        stream_id: str,
        ip_address: str,
        user_agent: str,
        protocol: str,
        connected_at: Optional[float] = None,
        idle_timeout_s: Optional[float] = None,
        worker_id: Optional[str] = None,
        update_last_active: bool = True,
        initial_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = self._safe_float(connected_at, default=time.time())
        normalized_protocol = self._normalize_protocol(protocol)
        normalized_client_id = str(client_id or "unknown")
        normalized_stream_id = sanitize_stream_id(stream_id)
        key = self._key(normalized_protocol, normalized_stream_id, normalized_client_id)

        created = False
        with self._lock:
            current = self._clients.get(key)
            
            # If not in memory but we have Redis, try to pull existing metadata 
            # to avoid 'blind overwrite' from other workers calling position updates.
            if current is None and self._redis:
                try:
                    from ..proxy.redis_keys import RedisKeys
                    client_key = RedisKeys.client_metadata(normalized_stream_id, normalized_client_id)
                    raw_data = self._redis.hgetall(client_key)
                    if raw_data:
                        # Hydrate a base row from Redis so we don't start from 'unknown'
                        current = {}
                        for k, v in raw_data.items():
                            try:
                                decoded_k = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                                decoded_v = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                                current[decoded_k] = decoded_v
                            except Exception:
                                continue
                        
                        # Fix type casting for numeric fields from Redis strings
                        current["bytes_sent"] = self._safe_float(current.get("bytes_sent"), 0.0)
                        current["bps"] = self._safe_float(current.get("bps"), 0.0)
                        current["requests_total"] = self._safe_int(current.get("requests_total"), 0)
                        current["chunks_sent"] = self._safe_int(current.get("chunks_sent"), 0)
                        current["connected_at"] = self._safe_float(current.get("connected_at"), now)
                        current["last_active"] = self._safe_float(current.get("last_active"), now)
                        current["is_prebuffering"] = str(current.get("is_prebuffering", "False")).lower() == "true"
                        
                        self._clients[key] = current
                        self._rate_state[key] = (current["bytes_sent"], current["last_active"])
                        logger.debug("[Telemetry:Registration] Hydrated client %s from Redis", normalized_client_id[:12])
                except Exception as e:
                    logger.debug(f"Failed to hydrate client from Redis during registration: {e}")

            if current is None:
                current = {
                    "client_id": normalized_client_id,
                    "stream_id": normalized_stream_id,
                    "ip_address": str(ip_address or "unknown"),
                    "user_agent": str(user_agent or "unknown"),
                    "protocol": normalized_protocol,
                    "bytes_sent": 0.0,
                    "buffer_seconds_behind": 0.0,
                    "buffer_seconds_behind_source": "initial",
                    "buffer_seconds_behind_confidence": 1.0,
                    "connected_at": now,
                    "last_active": now,
                    "bps": 0.0,
                    "requests_total": 0,
                    "last_request_kind": "",
                    "chunks_sent": 0,
                    "last_sequence": None,
                    "stats_updated_at": now,
                    "worker_id": str(worker_id or ""),
                    "idle_timeout_s": self._safe_float(idle_timeout_s, default=0.0),
                    "is_prebuffering": False,
                }
                if initial_metadata:
                    # Update with initial metadata, converting all to strings for consistency
                    for k, v in initial_metadata.items():
                        if k not in current and v is not None:
                            current[k] = v
                self._clients[key] = current
                self._rate_state[key] = (0.0, now)
                created = True
            else:
                # SMART MERGE: Only update IP/UA if the new value is NOT 'unknown',
                # or if the current value is missing/'unknown'.
                new_ip = str(ip_address or "unknown")
                if new_ip != "unknown" or not current.get("ip_address") or current.get("ip_address") == "unknown":
                    current["ip_address"] = new_ip

                new_ua = str(user_agent or "unknown")
                if new_ua != "unknown" or not current.get("user_agent") or current.get("user_agent") == "unknown":
                    current["user_agent"] = new_ua

                current["protocol"] = normalized_protocol
                if worker_id is not None:
                    current["worker_id"] = str(worker_id)
                if idle_timeout_s is not None:
                    current["idle_timeout_s"] = self._safe_float(idle_timeout_s, default=0.0)
                
                if update_last_active:
                    current["last_active"] = max(now, self._safe_float(current.get("last_active"), default=now))

            row = dict(current)

        if created:
            self._emit_connect_metric(normalized_protocol)
            logger.info(
                "[Telemetry:Registration] New client %s registered for stream %s (Protocol: %s, Worker: %s)",
                normalized_client_id[:12],
                normalized_stream_id,
                normalized_protocol,
                worker_id or "unknown"
            )

        # Redis persistence (cross-worker updates)
        # We perform this even if the client was NOT just created locally,
        # to ensure that Redis sets and TTLs are refreshed if they expired
        # but the client is still active in memory.
        try:
            from ..proxy.constants import EventType
            from ..proxy.redis_keys import RedisKeys
            
            if created:
                self._publish_client_event(EventType.CLIENT_CONNECTED, normalized_stream_id, row)
            
            if self._redis:
                client_key = RedisKeys.client_metadata(normalized_stream_id, normalized_client_id)
                client_set_key = RedisKeys.clients(normalized_stream_id)
                
                # Convert row values to strings for Redis hash
                mapping = {k: str(v) for k, v in row.items() if v is not None}
                self._redis.hset(client_key, mapping=mapping)
                
                # Add to the client set for the stream
                self._redis.sadd(client_set_key, normalized_client_id)
                
                # Use a generous TTL (60s default) to handle client cleanup
                from ..proxy.config_helper import Config
                ttl = int(Config.CLIENT_RECORD_TTL)
                self._redis.expire(client_key, ttl)
                self._redis.expire(client_set_key, ttl)
        except Exception as e:
            logger.debug(f"Failed to persist client registration to Redis: {e}")
            
        return row

    def record_activity(
        self,
        *,
        client_id: str,
        stream_id: str,
        bytes_delta: float,
        protocol: str,
        ip_address: str = "unknown",
        user_agent: str = "unknown",
        request_kind: str = "",
        chunks_delta: int = 0,
        sequence: Optional[int] = None,
        buffer_seconds_behind: Optional[float] = None,
        buffer_seconds_behind_source: Optional[str] = None,
        buffer_seconds_behind_confidence: Optional[float] = None,
        now: Optional[float] = None,
        idle_timeout_s: Optional[float] = None,
        is_prebuffering: Optional[bool] = None,
        worker_id: Optional[str] = None,
        bitrate: Optional[int] = None,
        total_bytes: Optional[float] = None,
    ) -> Dict[str, Any]:
        ts = self._safe_float(now, default=time.time())
        normalized_protocol = self._normalize_protocol(protocol)
        normalized_client_id = str(client_id or "unknown")
        normalized_stream_id = sanitize_stream_id(stream_id)

        byte_delta = self._safe_float(bytes_delta, default=0.0)
        if byte_delta < 0:
            byte_delta = 0.0

        chunk_delta = self._safe_int(chunks_delta, default=0)
        if chunk_delta < 0:
            chunk_delta = 0

        normalized_request_kind = str(request_kind or "").strip().lower()

        self.register_client(
            client_id=normalized_client_id,
            stream_id=normalized_stream_id,
            ip_address=ip_address,
            user_agent=user_agent,
            protocol=normalized_protocol,
            connected_at=ts,
            idle_timeout_s=idle_timeout_s,
            worker_id=worker_id,
            update_last_active=(normalized_request_kind != "heartbeat"),
        )

        key = self._key(normalized_protocol, normalized_stream_id, normalized_client_id)

        with self._lock:
            current = self._clients.get(key)
            if current is None:
                return {}

            # SMART MERGE: Only update IP/UA if the new value is NOT 'unknown',
            # or if the current value is missing/'unknown'.
            new_ip = str(ip_address or "unknown")
            if new_ip != "unknown" or not current.get("ip_address") or current.get("ip_address") == "unknown":
                current["ip_address"] = new_ip

            new_ua = str(user_agent or "unknown")
            if new_ua != "unknown" or not current.get("user_agent") or current.get("user_agent") == "unknown":
                current["user_agent"] = new_ua
            
            if normalized_request_kind != "heartbeat":
                current["last_active"] = ts
                
            if total_bytes is not None:
                current["bytes_sent"] = self._safe_float(total_bytes, 0.0)
            else:
                current["bytes_sent"] = self._safe_float(current.get("bytes_sent"), 0.0) + byte_delta
                
            current["chunks_sent"] = self._safe_int(current.get("chunks_sent"), 0) + chunk_delta
            current["requests_total"] = self._safe_int(current.get("requests_total"), 0) + 1
            current["stats_updated_at"] = ts

            if buffer_seconds_behind is not None:
                current["buffer_seconds_behind"] = max(0.0, self._safe_float(buffer_seconds_behind, default=0.0))
            if buffer_seconds_behind_source is not None:
                current["buffer_seconds_behind_source"] = str(buffer_seconds_behind_source)
            if buffer_seconds_behind_confidence is not None:
                current["buffer_seconds_behind_confidence"] = self._safe_float(buffer_seconds_behind_confidence, default=1.0)

            if idle_timeout_s is not None:
                current["idle_timeout_s"] = self._safe_float(idle_timeout_s, default=0.0)
            if worker_id is not None:
                current["worker_id"] = str(worker_id)

            if normalized_request_kind:
                current["last_request_kind"] = normalized_request_kind

            if is_prebuffering is not None:
                current["is_prebuffering"] = bool(is_prebuffering)

            if bitrate is not None:
                try:
                    current["bitrate"] = int(bitrate)
                except (TypeError, ValueError):
                    pass

            if sequence is not None:
                try:
                    seq = int(sequence)
                    previous = current.get("last_sequence")
                    if previous is None:
                        current["last_sequence"] = seq
                    else:
                        current["last_sequence"] = max(int(previous), seq)
                except (TypeError, ValueError):
                    pass

            previous_bytes, previous_ts = self._rate_state.get(key, (0.0, 0.0))
            if previous_ts == 0.0:
                # First capture for this client. 
                # Use time since connection if available, otherwise assume 500ms bootstrap.
                # If we have a nominal bitrate, use it to prime the initial BPS state.
                initial_bps = float(bitrate) if bitrate is not None and bitrate > 0 else 0.0
                current["bps"] = initial_bps
                
                connected_at = self._safe_float(current.get("connected_at"), default=ts)
                previous_ts = max(connected_at, ts - 0.5)
                previous_bytes = 0.0

            delta_bytes = self._safe_float(current.get("bytes_sent"), 0.0) - previous_bytes
            delta_time = ts - previous_ts
            
            # Use 1ms floor for delta_time to avoid division by zero and capture the first sample.
            # This is particularly important for HLS segments which are large but sparse.
            effective_dt = max(0.001, delta_time)
            instant_bps = max(0.0, delta_bytes / effective_dt)
            
            # Apply smoothing (exponential moving average) to prevent jumpy UI.
            # Alpha 0.3 provides a balance between responsiveness and stability for bursty HLS.
            prev_bps = self._safe_float(current.get("bps"), default=0.0)
            if prev_bps == 0:
                current["bps"] = instant_bps
            else:
                alpha = 0.3
                current["bps"] = (prev_bps * (1 - alpha)) + (instant_bps * alpha)

            self._rate_state[key] = (self._safe_float(current.get("bytes_sent"), 0.0), ts)

            # Update Redis if available to keep state fresh across workers
            if self._redis:
                try:
                    from ..proxy.redis_keys import RedisKeys
                    from ..proxy.config_helper import Config
                    client_key = RedisKeys.client_metadata(normalized_stream_id, normalized_client_id)
                    # Use minimal subset for high-frequency updates to reduce Redis load
                    update_mapping = {
                        "bps": str(current.get("bps")),
                        "bytes_sent": str(current.get("bytes_sent")),
                        "requests_total": str(current.get("requests_total")),
                        "last_active": str(current.get("last_active")),
                        "stats_updated_at": str(current.get("stats_updated_at")),
                    }
                    if is_prebuffering is not None:
                        update_mapping["is_prebuffering"] = str(current.get("is_prebuffering"))
                    
                    self._redis.hset(client_key, mapping=update_mapping)
                    
                    # Refresh TTL for both individual client and the stream's client set
                    ttl = int(Config.CLIENT_RECORD_TTL)
                    self._redis.expire(client_key, ttl)
                    client_set_key = RedisKeys.clients(normalized_stream_id)
                    self._redis.expire(client_set_key, ttl)
                except Exception as e:
                    logger.debug(f"Failed to update client activity in Redis: {e}")

            return dict(current)

    def update_client_position(
        self,
        *,
        client_id: str,
        stream_id: str,
        protocol: str,
        seconds_behind: float,
        source: Optional[str] = None,
        confidence: Optional[float] = None,
        observed_at: Optional[float] = None,
        is_prebuffering: Optional[bool] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update buffer lag for an existing client without incrementing request counters."""
        ts = self._safe_float(now, default=time.time())
        normalized_protocol = self._normalize_protocol(protocol)
        normalized_client_id = str(client_id or "unknown")
        normalized_stream_id = sanitize_stream_id(stream_id)
        key = self._key(normalized_protocol, normalized_stream_id, normalized_client_id)

        # Position updates can arrive while tracker rows are briefly missing
        # during reconnect/failover races. Recreate a minimal row so runway
        # telemetry is never dropped.
        if key not in self._clients:
            self.register_client(
                client_id=normalized_client_id,
                stream_id=normalized_stream_id,
                ip_address="unknown",
                user_agent="unknown",
                protocol=normalized_protocol,
                connected_at=ts,
            )

        with self._lock:
            current = self._clients.get(key)
            if current is None:
                return {}

            normalized_seconds = max(0.0, self._safe_float(seconds_behind, default=0.0))
            current["buffer_seconds_behind"] = normalized_seconds
            
            if source is not None:
                current["buffer_seconds_behind_source"] = str(source)
            if confidence is not None:
                current["buffer_seconds_behind_confidence"] = self._safe_float(confidence, default=1.0)
            
            if is_prebuffering is not None:
                current["is_prebuffering"] = bool(is_prebuffering)

            current["stats_updated_at"] = ts

            # Position updates are valid heartbeat activity even when no bytes
            # are currently flowing (e.g., short upstream starvation/reconnect).
            current["last_active"] = max(ts, self._safe_float(current.get("last_active"), default=ts))
            current["stats_updated_at"] = ts

            # Update Redis if available
            if self._redis:
                try:
                    from ..proxy.redis_keys import RedisKeys
                    client_key = RedisKeys.client_metadata(normalized_stream_id, normalized_client_id)
                    self._redis.hset(client_key, mapping={
                        "buffer_seconds_behind": str(current.get("buffer_seconds_behind")),
                        "buffer_seconds_behind_source": str(current.get("buffer_seconds_behind_source")),
                        "buffer_seconds_behind_confidence": str(current.get("buffer_seconds_behind_confidence")),
                        "stats_updated_at": str(current.get("stats_updated_at")),
                        "last_active": str(current.get("last_active")),
                    })
                except Exception:
                    pass

            return dict(current)

    def prune_stale_clients(self, timeout_s: float) -> int:
        default_timeout = self._safe_float(timeout_s, default=0.0)
        if default_timeout <= 0:
            return 0

        now = time.time()
        removed_rows: List[Dict[str, Any]] = []

        with self._lock:
            stale_keys: List[Tuple[str, str, str]] = []
            for key, row in self._clients.items():
                row_timeout = self._safe_float(row.get("idle_timeout_s"), default=0.0)
                effective_timeout = row_timeout if row_timeout > 0 else default_timeout
                if effective_timeout <= 0:
                    continue

                last_active = self._safe_float(row.get("last_active"), default=now)
                if (now - last_active) > effective_timeout:
                    stale_keys.append(key)

            for key in stale_keys:
                row = self._clients.pop(key, None)
                self._rate_state.pop(key, None)
                if row is not None:
                    removed_rows.append(row)

        for row in removed_rows:
            protocol = self._normalize_protocol(row.get("protocol"))
            self._emit_disconnect_metric(protocol)
            try:
                from ..proxy.constants import EventType
                from ..proxy.redis_keys import RedisKeys
                
                stream_id = str(row.get("stream_id"))
                client_id = str(row.get("client_id"))
                
                self._publish_client_event(EventType.CLIENT_DISCONNECTED, stream_id, row)
                
                # Cleanup Redis if available
                if self._redis:
                    client_key = RedisKeys.client_metadata(stream_id, client_id)
                    client_set_key = RedisKeys.clients(stream_id)
                    
                    self._redis.unlink(client_key)
                    self._redis.srem(client_set_key, client_id)
                
                # Explicit logs for visibility into client timeouts
                logger.info(
                    "[Stream:%s] [Client:%s] Disconnected (Idle timeout: %.0fs)", 
                    stream_id[:12], 
                    client_id[:12],
                    effective_timeout
                )
            except Exception as e:
                logger.debug(f"Failed to cleanup stale client from Redis: {e}")
            except Exception:
                pass
        return len(removed_rows)

    def unregister_client(
        self,
        *,
        client_id: str,
        stream_id: str,
        protocol: Optional[str] = None,
    ) -> int:
        target_protocol = self._normalize_protocol(protocol) if protocol else None
        normalized_stream_id = sanitize_stream_id(stream_id)
        normalized_client_id = str(client_id or "")

        removed_rows: List[Dict[str, Any]] = []
        with self._lock:
            keys_to_remove = []
            for key in self._clients.keys():
                protocol_key, stream_key, client_key = key
                if stream_key != normalized_stream_id:
                    continue
                if client_key != normalized_client_id:
                    continue
                if target_protocol and protocol_key != target_protocol:
                    continue
                keys_to_remove.append(key)

            for key in keys_to_remove:
                row = self._clients.pop(key, None)
                self._rate_state.pop(key, None)
                if row is not None:
                    removed_rows.append(row)

        for row in removed_rows:
            p = self._normalize_protocol(row.get("protocol"))
            self._emit_disconnect_metric(p)
            try:
                from ..proxy.constants import EventType
                from ..proxy.redis_keys import RedisKeys
                
                client_id = str(row.get("client_id"))
                
                self._publish_client_event(EventType.CLIENT_DISCONNECTED, normalized_stream_id, row)

                # Cleanup Redis if available
                if self._redis:
                    client_key = RedisKeys.client_metadata(normalized_stream_id, client_id)
                    client_set_key = RedisKeys.clients(normalized_stream_id)
                    
                    self._redis.unlink(client_key)
                    self._redis.srem(client_set_key, client_id)

                # Explicit logs for visibility into client removal
                logger.info(
                    "[Stream:%s] [Client:%s] Client disconnected", 
                    normalized_stream_id[:12], 
                    client_id[:12]
                )
            except Exception as e:
                logger.debug(f"Failed to cleanup client unregistration from Redis: {e}")
            except Exception:
                pass
        return len(removed_rows)

    def unregister_stream(
        self,
        *,
        stream_id: str,
        protocol: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> int:
        target_protocol = self._normalize_protocol(protocol) if protocol else None
        normalized_stream_id = sanitize_stream_id(stream_id)
        normalized_worker_id = str(worker_id or "") if worker_id is not None else None

        removed_rows: List[Dict[str, Any]] = []
        with self._lock:
            keys_to_remove = []
            for key, row in self._clients.items():
                protocol_key, stream_key, _ = key
                if stream_key != normalized_stream_id:
                    continue
                if target_protocol and protocol_key != target_protocol:
                    continue
                if normalized_worker_id is not None and str(row.get("worker_id") or "") != normalized_worker_id:
                    continue
                keys_to_remove.append(key)

            for key in keys_to_remove:
                row = self._clients.pop(key, None)
                self._rate_state.pop(key, None)
                if row is not None:
                    removed_rows.append(row)

        for row in removed_rows:
            p = self._normalize_protocol(row.get("protocol"))
            self._emit_disconnect_metric(p)
            try:
                from ..proxy.constants import EventType
                from ..proxy.redis_keys import RedisKeys
                
                client_id = str(row.get("client_id"))
                
                self._publish_client_event(EventType.CLIENT_DISCONNECTED, normalized_stream_id, row)

                # Cleanup Redis if available
                if self._redis:
                    client_key = RedisKeys.client_metadata(normalized_stream_id, client_id)
                    client_set_key = RedisKeys.clients(normalized_stream_id)
                    
                    self._redis.unlink(client_key)
                    self._redis.srem(client_set_key, client_id)

                # Explicit logs for visibility into batch client removal
                logger.info(
                    "[Stream:%s] [Client:%s] Client removed (Stream ended)", 
                    normalized_stream_id[:12], 
                    str(row.get("client_id"))[:12]
                )
            except Exception:
                pass
        return len(removed_rows)

    def _to_public_row(self, row: Dict[str, Any], now: float) -> Dict[str, Any]:
        last_active = self._safe_float(row.get("last_active"), default=now)
        protocol = self._normalize_protocol(row.get("protocol"))
        client_id = str(row.get("client_id") or "unknown")
        stream_id = str(row.get("stream_id") or "")
        ip_address = str(row.get("ip_address") or "unknown")
        user_agent = str(row.get("user_agent") or "unknown")

        payload = {
            "id": client_id,
            "client_id": client_id,
            "stream_id": stream_id,
            "ip": ip_address,
            "ip_address": ip_address,
            "ua": user_agent,
            "user_agent": user_agent,
            "type": protocol,
            "protocol": protocol,
            "bps": self._safe_float(row.get("bps"), default=0.0),
            "bytes_sent": self._safe_float(row.get("bytes_sent"), default=0.0),
            "buffer_seconds_behind": self._safe_float(row.get("buffer_seconds_behind"), default=0.0),
            "buffer_seconds_behind_source": str(row.get("buffer_seconds_behind_source") or "unknown"),
            "buffer_seconds_behind_confidence": self._safe_float(row.get("buffer_seconds_behind_confidence"), default=1.0),
            "connected_at": self._safe_float(row.get("connected_at"), default=now),
            "last_active": last_active,
            "inactive_seconds": max(0.0, now - last_active),
            "requests_total": self._safe_int(row.get("requests_total"), default=0),
            "last_request_kind": str(row.get("last_request_kind") or ""),
            "chunks_sent": self._safe_int(row.get("chunks_sent"), default=0),
            "last_sequence": row.get("last_sequence"),
            "stats_updated_at": self._safe_float(row.get("stats_updated_at"), default=last_active),
            "is_prebuffering": bool(row.get("is_prebuffering", False)),
            "worker_id": str(row.get("worker_id") or ""),
        }
        return payload

    def get_all_active_clients(self) -> List[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = [self._to_public_row(row, now) for row in self._clients.values()]

        rows.sort(key=lambda item: self._safe_float(item.get("last_active"), default=0.0), reverse=True)
        return rows

    def get_stream_clients(
        self,
        stream_id: str,
        *,
        protocol: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        normalized_stream_id = sanitize_stream_id(stream_id)
        target_protocol = self._normalize_protocol(protocol) if protocol else None
        target_worker_id = str(worker_id or "") if worker_id is not None else None
        now = time.time()

        with self._lock:
            rows: List[Dict[str, Any]] = []
            
            # 1. Fetch from Redis first for cross-worker parity
            if self._redis:
                try:
                    from ..proxy.redis_keys import RedisKeys
                    client_set_key = RedisKeys.clients(normalized_stream_id)
                    
                    # Get all client IDs for this stream from the set
                    # This is O(N) where N is number of clients in stream, much faster than KEYS *
                    raw_client_ids = self._redis.smembers(client_set_key)
                    if raw_client_ids:
                        # Use pipelining to fetch all metadata hashes in one round-trip
                        pipe = self._redis.pipeline()
                        client_ids = []
                        for rid in raw_client_ids:
                            try:
                                cid = rid.decode("utf-8") if isinstance(rid, bytes) else str(rid)
                                client_ids.append(cid)
                                pipe.hgetall(RedisKeys.client_metadata(normalized_stream_id, cid))
                            except Exception:
                                continue
                        
                        raw_data_list = pipe.execute()
                        
                        for i, raw_data in enumerate(raw_data_list):
                            if not raw_data:
                                continue
                                
                            # Decode Redis bytes to string
                            data = {}
                            for k, v in raw_data.items():
                                try:
                                    decoded_k = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                                    decoded_v = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                                    data[decoded_k] = decoded_v
                                except Exception:
                                    continue
                                    
                            # Filter by worker_id and protocol if requested
                            if target_worker_id is not None and data.get("worker_id") != target_worker_id:
                                continue
                            row_protocol = self._normalize_protocol(data.get("protocol"))
                            if target_protocol and row_protocol != target_protocol:
                                continue
                                
                            # Enrich and format for public return
                            rows.append(self._to_public_row(data, now))
                except Exception as e:
                    logger.warning(f"Failed to aggregate clients from Redis for stream {normalized_stream_id}: {e}")

            # 2. Fallback/Augment with in-memory tracking
            # We use a set of IDs to avoid duplicates if same data exists in both
            seen_client_ids = {str(r.get("id") or "") for r in rows}
            
            for row in self._clients.values():
                row_client_id = str(row.get("client_id") or "")
                if row_client_id in seen_client_ids:
                    continue
                    
                row_stream_id = str(row.get("stream_id") or "")
                if row_stream_id != normalized_stream_id:
                    continue
                row_protocol = self._normalize_protocol(row.get("protocol"))
                if target_protocol and row_protocol != target_protocol:
                    continue
                if target_worker_id is not None and str(row.get("worker_id") or "") != target_worker_id:
                    continue
                rows.append(self._to_public_row(row, now))

        if not rows:
            # Diagnostic logic remains
            with self._lock:
                total_in_memory = len(self._clients)
                similar_keys = [str(k[1]) for k in self._clients.keys() if str(k[1])[:4] == str(normalized_stream_id)[:4]]
                logger.debug(
                    "[Telemetry:Diagnostic] No clients found for stream %s (TargetProtocol: %s). Tracker: %d rows. Similar: %s",
                    normalized_stream_id,
                    target_protocol or "Any",
                    total_in_memory,
                    similar_keys[:5]
                )

        rows.sort(key=lambda item: self._safe_float(item.get("last_active"), default=0.0), reverse=True)
        return rows

    def get_stream_clients_payload(
        self,
        stream_id: str,
        *,
        protocol: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return stream client rows for UI payloads."""
        return {
            "clients": self.get_stream_clients(stream_id, protocol=protocol, worker_id=worker_id)
        }

    def count_active_clients(
        self,
        *,
        stream_id: Optional[str] = None,
        protocol: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> int:
        target_stream_id = sanitize_stream_id(stream_id) if stream_id is not None else None
        target_protocol = self._normalize_protocol(protocol) if protocol else None
        target_worker_id = str(worker_id or "") if worker_id is not None else None

        with self._lock:
            total = 0
            for row in self._clients.values():
                if target_stream_id is not None and str(row.get("stream_id") or "") != target_stream_id:
                    continue
                row_protocol = self._normalize_protocol(row.get("protocol"))
                if target_protocol and row_protocol != target_protocol:
                    continue
                if target_worker_id is not None and str(row.get("worker_id") or "") != target_worker_id:
                    continue
                total += 1
            return total


client_tracking_service = ClientTrackingService()
