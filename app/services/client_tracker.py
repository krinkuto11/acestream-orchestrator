from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple


class ClientTrackingService:
    """Unified in-memory client tracker used by TS and HLS data paths."""

    def __init__(self):
        self._lock = threading.RLock()
        self._clients: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self._rate_state: Dict[Tuple[str, str, str], Tuple[float, float]] = {}

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
            from .metrics import observe_proxy_client_connect

            observe_proxy_client_connect(protocol)
        except Exception:
            pass

    def _emit_disconnect_metric(self, protocol: str):
        try:
            from .metrics import observe_proxy_client_disconnect

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
    ) -> Dict[str, Any]:
        now = self._safe_float(connected_at, default=time.time())
        normalized_protocol = self._normalize_protocol(protocol)
        normalized_client_id = str(client_id or "unknown")
        normalized_stream_id = str(stream_id or "")
        key = self._key(normalized_protocol, normalized_stream_id, normalized_client_id)

        created = False
        with self._lock:
            current = self._clients.get(key)
            if current is None:
                current = {
                    "client_id": normalized_client_id,
                    "stream_id": normalized_stream_id,
                    "ip_address": str(ip_address or "unknown"),
                    "user_agent": str(user_agent or "unknown"),
                    "protocol": normalized_protocol,
                    "bytes_sent": 0.0,
                    "buffer_seconds_behind": 0.0,
                    "client_runway_seconds": 0.0,
                    "stream_buffer_window_seconds": 0.0,
                    "position_source": "",
                    "position_confidence": 0.0,
                    "position_observed_at": now,
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
                }
                self._clients[key] = current
                self._rate_state[key] = (0.0, now)
                created = True
            else:
                current["ip_address"] = str(ip_address or current.get("ip_address") or "unknown")
                current["user_agent"] = str(user_agent or current.get("user_agent") or "unknown")
                current["protocol"] = normalized_protocol
                current.setdefault("client_runway_seconds", self._safe_float(current.get("buffer_seconds_behind"), default=0.0))
                current.setdefault("stream_buffer_window_seconds", 0.0)
                current.setdefault("position_source", "")
                current.setdefault("position_confidence", 0.0)
                current.setdefault("position_observed_at", now)
                if worker_id is not None:
                    current["worker_id"] = str(worker_id)
                if idle_timeout_s is not None:
                    current["idle_timeout_s"] = self._safe_float(idle_timeout_s, default=0.0)
                current["last_active"] = max(now, self._safe_float(current.get("last_active"), default=now))

            row = dict(current)

        if created:
            self._emit_connect_metric(normalized_protocol)
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
        client_runway_seconds: Optional[float] = None,
        stream_buffer_window_seconds: Optional[float] = None,
        position_source: Optional[str] = None,
        position_confidence: Optional[float] = None,
        position_observed_at: Optional[float] = None,
        now: Optional[float] = None,
        idle_timeout_s: Optional[float] = None,
        worker_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts = self._safe_float(now, default=time.time())
        normalized_protocol = self._normalize_protocol(protocol)
        normalized_client_id = str(client_id or "unknown")
        normalized_stream_id = str(stream_id or "")

        byte_delta = self._safe_float(bytes_delta, default=0.0)
        if byte_delta < 0:
            byte_delta = 0.0

        chunk_delta = self._safe_int(chunks_delta, default=0)
        if chunk_delta < 0:
            chunk_delta = 0

        self.register_client(
            client_id=normalized_client_id,
            stream_id=normalized_stream_id,
            ip_address=ip_address,
            user_agent=user_agent,
            protocol=normalized_protocol,
            connected_at=ts,
            idle_timeout_s=idle_timeout_s,
            worker_id=worker_id,
        )

        key = self._key(normalized_protocol, normalized_stream_id, normalized_client_id)

        with self._lock:
            current = self._clients.get(key)
            if current is None:
                return {}

            current["ip_address"] = str(ip_address or current.get("ip_address") or "unknown")
            current["user_agent"] = str(user_agent or current.get("user_agent") or "unknown")
            current["last_active"] = ts
            current["bytes_sent"] = self._safe_float(current.get("bytes_sent"), 0.0) + byte_delta
            current["chunks_sent"] = self._safe_int(current.get("chunks_sent"), 0) + chunk_delta
            current["requests_total"] = self._safe_int(current.get("requests_total"), 0) + 1
            current["stats_updated_at"] = ts

            normalized_request_kind = str(request_kind or "").strip().lower()

            inferred_source = str(position_source or "").strip().lower()
            if not inferred_source:
                if normalized_request_kind == "segment":
                    inferred_source = "hls_segment_delta"
                elif normalized_request_kind == "manifest":
                    inferred_source = "hls_manifest_window"
                elif normalized_request_kind == "stream":
                    inferred_source = "ts_cursor_ema"

            observed_at_ts = self._safe_float(position_observed_at, default=ts)
            if observed_at_ts <= 0:
                observed_at_ts = ts

            confidence = self._safe_float(position_confidence, default=-1.0)
            if confidence < 0.0:
                if inferred_source == "hls_segment_delta":
                    confidence = 0.85
                elif inferred_source == "hls_manifest_window":
                    confidence = 0.35
                elif inferred_source == "ts_cursor_ema":
                    confidence = 0.75
                else:
                    confidence = 0.60
            confidence = max(0.0, min(1.0, confidence))

            if stream_buffer_window_seconds is not None and normalized_request_kind == "manifest":
                current["stream_buffer_window_seconds"] = max(
                    0.0,
                    self._safe_float(stream_buffer_window_seconds, default=0.0),
                )

            runway_value = None
            if normalized_request_kind != "manifest" and client_runway_seconds is not None:
                runway_value = max(0.0, self._safe_float(client_runway_seconds, default=0.0))
            elif buffer_seconds_behind is not None and normalized_request_kind in {"segment", "stream"}:
                runway_value = max(0.0, self._safe_float(buffer_seconds_behind, default=0.0))

            if runway_value is not None:
                current["client_runway_seconds"] = runway_value
                # Keep legacy field for backward compatibility with existing UI/API clients.
                current["buffer_seconds_behind"] = runway_value
            elif buffer_seconds_behind is not None and normalized_request_kind == "manifest":
                # Legacy compatibility for callers that still pass manifest lag
                # through buffer_seconds_behind.
                current["stream_buffer_window_seconds"] = max(
                    0.0,
                    self._safe_float(buffer_seconds_behind, default=0.0),
                )

            if inferred_source:
                current["position_source"] = inferred_source
            current["position_confidence"] = confidence
            current["position_observed_at"] = observed_at_ts

            if idle_timeout_s is not None:
                current["idle_timeout_s"] = self._safe_float(idle_timeout_s, default=0.0)
            if worker_id is not None:
                current["worker_id"] = str(worker_id)

            if normalized_request_kind:
                current["last_request_kind"] = normalized_request_kind

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

            previous_bytes, previous_ts = self._rate_state.get(key, (0.0, ts))
            delta_bytes = self._safe_float(current.get("bytes_sent"), 0.0) - previous_bytes
            delta_time = ts - self._safe_float(previous_ts, ts)
            if delta_time > 0 and delta_bytes >= 0:
                current["bps"] = delta_bytes / delta_time
            else:
                current["bps"] = 0.0
            self._rate_state[key] = (self._safe_float(current.get("bytes_sent"), 0.0), ts)

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
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update buffer lag for an existing client without incrementing request counters."""
        ts = self._safe_float(now, default=time.time())
        normalized_protocol = self._normalize_protocol(protocol)
        normalized_client_id = str(client_id or "unknown")
        normalized_stream_id = str(stream_id or "")
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
            current["client_runway_seconds"] = normalized_seconds

            normalized_source = str(source or "").strip().lower()
            if normalized_source:
                current["position_source"] = normalized_source

            if confidence is not None:
                current["position_confidence"] = max(0.0, min(1.0, self._safe_float(confidence, default=0.0)))

            observed_ts = self._safe_float(observed_at, default=ts)
            if observed_ts > 0:
                current["position_observed_at"] = observed_ts

            # Position updates are valid heartbeat activity even when no bytes
            # are currently flowing (e.g., short upstream starvation/reconnect).
            current["last_active"] = max(ts, self._safe_float(current.get("last_active"), default=ts))
            current["stats_updated_at"] = ts
            return dict(current)

    def prune_stale_clients(self, timeout_s: float) -> int:
        default_timeout = self._safe_float(timeout_s, default=0.0)
        if default_timeout <= 0:
            return 0

        now = time.time()
        removed_protocols: List[str] = []

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
                    removed_protocols.append(self._normalize_protocol(row.get("protocol")))

        for protocol in removed_protocols:
            self._emit_disconnect_metric(protocol)
        return len(removed_protocols)

    def unregister_client(
        self,
        *,
        client_id: str,
        stream_id: str,
        protocol: Optional[str] = None,
    ) -> int:
        target_protocol = self._normalize_protocol(protocol) if protocol else None
        normalized_stream_id = str(stream_id or "")
        normalized_client_id = str(client_id or "")

        removed_protocols: List[str] = []
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
                    removed_protocols.append(self._normalize_protocol(row.get("protocol")))

        for p in removed_protocols:
            self._emit_disconnect_metric(p)
        return len(removed_protocols)

    def unregister_stream(
        self,
        *,
        stream_id: str,
        protocol: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> int:
        target_protocol = self._normalize_protocol(protocol) if protocol else None
        normalized_stream_id = str(stream_id or "")
        normalized_worker_id = str(worker_id or "") if worker_id is not None else None

        removed_protocols: List[str] = []
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
                    removed_protocols.append(self._normalize_protocol(row.get("protocol")))

        for p in removed_protocols:
            self._emit_disconnect_metric(p)
        return len(removed_protocols)

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
            "client_runway_seconds": self._safe_float(row.get("client_runway_seconds"), default=self._safe_float(row.get("buffer_seconds_behind"), default=0.0)),
            "stream_buffer_window_seconds": self._safe_float(row.get("stream_buffer_window_seconds"), default=0.0),
            "position_source": str(row.get("position_source") or ""),
            "position_confidence": self._safe_float(row.get("position_confidence"), default=0.0),
            "position_observed_at": self._safe_float(row.get("position_observed_at"), default=last_active),
            "connected_at": self._safe_float(row.get("connected_at"), default=now),
            "last_active": last_active,
            "inactive_seconds": max(0.0, now - last_active),
            "requests_total": self._safe_int(row.get("requests_total"), default=0),
            "last_request_kind": str(row.get("last_request_kind") or ""),
            "chunks_sent": self._safe_int(row.get("chunks_sent"), default=0),
            "last_sequence": row.get("last_sequence"),
            "stats_updated_at": self._safe_float(row.get("stats_updated_at"), default=last_active),
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
        normalized_stream_id = str(stream_id or "")
        target_protocol = self._normalize_protocol(protocol) if protocol else None
        target_worker_id = str(worker_id or "") if worker_id is not None else None
        now = time.time()

        with self._lock:
            rows: List[Dict[str, Any]] = []
            for row in self._clients.values():
                if str(row.get("stream_id") or "") != normalized_stream_id:
                    continue
                row_protocol = self._normalize_protocol(row.get("protocol"))
                if target_protocol and row_protocol != target_protocol:
                    continue
                if target_worker_id is not None and str(row.get("worker_id") or "") != target_worker_id:
                    continue
                rows.append(self._to_public_row(row, now))

        rows.sort(key=lambda item: self._safe_float(item.get("last_active"), default=0.0), reverse=True)
        return rows

    def _get_stream_failover_telemetry(self, stream_id: str) -> Dict[str, Any]:
        normalized_stream_id = str(stream_id or "").strip()
        if not normalized_stream_id:
            return {}

        try:
            from .state import state

            telemetry = state.get_stream_failover_telemetry(stream_key=normalized_stream_id)
            return dict(telemetry or {})
        except Exception:
            return {}

    def get_stream_clients_payload(
        self,
        stream_id: str,
        *,
        protocol: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return stream client rows with dynamic failover telemetry for UI payloads."""
        payload: Dict[str, Any] = {
            "clients": self.get_stream_clients(stream_id, protocol=protocol, worker_id=worker_id)
        }
        payload.update(self._get_stream_failover_telemetry(stream_id))
        return payload

    def count_active_clients(
        self,
        *,
        stream_id: Optional[str] = None,
        protocol: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> int:
        target_stream_id = str(stream_id or "") if stream_id is not None else None
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
