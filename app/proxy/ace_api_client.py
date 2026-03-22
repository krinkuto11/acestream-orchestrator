"""
Minimal AceStream legacy API client.

Implements the telnet-style control protocol used on the AceStream API port.
This module is optional and only used when proxy control mode is LEGACY_API.
"""

import hashlib
import json
import logging
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote

logger = logging.getLogger(__name__)

DEFAULT_ACE_PRODUCT_KEY = "n51LvQoTlJzNGaFxseRK-uvnvX-sD4Vm5Axwmc4UcoD-jruxmKsuJaH0eVgE"


class AceLegacyApiError(Exception):
    """Raised when the AceStream legacy API fails."""


class AceLegacyApiClient:
    """Simple line-based client for the AceStream legacy API port."""

    _canonical_cache: Dict[str, Tuple[str, float]] = {}
    _canonical_cache_lock = threading.Lock()
    _canonical_cache_ttl_s = 600

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout: float = 10.0,
        response_timeout: float = 10.0,
        product_key: str = DEFAULT_ACE_PRODUCT_KEY,
    ):
        self.host = host
        self.port = int(port)
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.product_key = product_key

        self._sock: Optional[socket.socket] = None
        self._reader = None
        self._writer = None
        self._authenticated = False

        # Default profile used when engine asks for USERDATA.
        self._gender = 1
        self._age = 3

    @staticmethod
    def _normalize_session_id(session_id: Optional[str]) -> str:
        """Legacy engine requires a numeric session identifier for LOADASYNC."""
        if session_id is None:
            return "0"
        normalized = str(session_id).strip()
        return normalized if normalized.isdigit() else "0"

    @classmethod
    def _get_cached_canonical_infohash(cls, content_id: str) -> Optional[str]:
        now = time.time()
        with cls._canonical_cache_lock:
            item = cls._canonical_cache.get(content_id)
            if not item:
                return None
            infohash, ts = item
            if now - ts > cls._canonical_cache_ttl_s:
                cls._canonical_cache.pop(content_id, None)
                return None
            return infohash

    @classmethod
    def _set_cached_canonical_infohash(cls, content_id: str, infohash: str):
        if not content_id or not infohash:
            return
        now = time.time()
        with cls._canonical_cache_lock:
            cls._canonical_cache[content_id] = (infohash, now)
            cls._canonical_cache[infohash] = (infohash, now)

    def connect(self):
        """Open TCP connection to AceStream API."""
        self._sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        self._sock.settimeout(self.response_timeout)
        self._reader = self._sock.makefile("r", encoding="utf-8", newline="\n")
        self._writer = self._sock.makefile("w", encoding="utf-8", newline="\n")
        logger.debug("Connected to legacy AceStream API at %s:%s", self.host, self.port)

    def close(self):
        """Close API connection."""
        try:
            if self._writer:
                self._writer.close()
        except Exception:
            pass
        try:
            if self._reader:
                self._reader.close()
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

        self._writer = None
        self._reader = None
        self._sock = None
        self._authenticated = False

    def authenticate(self):
        """Run HELLOBG/READY handshake and authenticate the session."""
        self._write("HELLOBG version=4")
        _, parts, kv = self._wait_for("HELLOTS", timeout=self.response_timeout)

        request_key = kv.get("key", "")
        version_code = int(kv.get("version_code", "0") or "0")

        digest = hashlib.sha1((request_key + self.product_key).encode("utf-8")).hexdigest()
        ready_key = f"{self.product_key.split('-')[0]}-{digest}"
        self._write(f"READY key={ready_key}")

        deadline = time.time() + self.response_timeout
        while time.time() < deadline:
            cmd, parts, _ = self._read_message(timeout=max(0.05, deadline - time.time()))
            if cmd == "AUTH":
                self._authenticated = True
                break
            if cmd == "NOTREADY":
                raise AceLegacyApiError("Engine responded NOTREADY during authentication")
            self._handle_async(cmd, parts)

        if not self._authenticated:
            raise AceLegacyApiError("Authentication timeout (AUTH not received)")

        if version_code >= 3003600:
            self._write("SETOPTIONS use_stop_notifications=1")

    def resolve_content(self, content_id: str, session_id: Optional[str] = None) -> Tuple[Dict, str]:
        """
        Resolve metadata using LOADASYNC.

        Returns tuple of (load_response_json, mode) where mode is one of
        {"content_id", "infohash"} and should be reused for START.
        """
        normalized_session_id = self._normalize_session_id(session_id)

        cached_infohash = self._get_cached_canonical_infohash(content_id)
        if cached_infohash and cached_infohash != content_id:
            cached_resp = self._loadasync_infohash(cached_infohash, normalized_session_id)
            if cached_resp.get("status") in (1, 2):
                self._set_cached_canonical_infohash(content_id, cached_infohash)
                return cached_resp, "infohash"

        # First try PID/content_id mode.
        load_resp = self._loadasync_pid(content_id, normalized_session_id)
        if load_resp.get("status") in (1, 2):
            self._set_cached_canonical_infohash(content_id, load_resp.get("infohash") or content_id)
            return load_resp, "content_id"

        # Fallback to INFOHASH mode.
        load_resp = self._loadasync_infohash(content_id, normalized_session_id)
        if load_resp.get("status") in (1, 2):
            self._set_cached_canonical_infohash(content_id, load_resp.get("infohash") or content_id)
            return load_resp, "infohash"

        message = load_resp.get("message", "content unavailable")
        raise AceLegacyApiError(f"LOADASYNC failed for '{content_id}': {message}")

    def start_stream(self, content_id: str, mode: str, stream_type: str = "output_format=http") -> Dict[str, str]:
        """Start stream and return parsed START key/value response."""
        if mode == "content_id":
            cmd = f"START PID {content_id} 0 {stream_type}"
        elif mode == "infohash":
            cmd = f"START INFOHASH {content_id} 0 0 0 0 {stream_type}"
        else:
            raise AceLegacyApiError(f"Unsupported START mode: {mode}")

        self._write(cmd)
        _, parts, _ = self._wait_for("START", timeout=self.response_timeout * 3)

        return self._parse_start_params(parts)

    @staticmethod
    def _parse_start_params(parts: list) -> Dict[str, str]:
        """Parse START command payload into key/value params."""

        params = {}
        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                params[key] = value
        return params

    def live_seek(self, timestamp: int):
        """Seek live playback to a specific timestamp."""
        self._write(f"LIVESEEK {int(timestamp)}")

    def start_stream_with_seekback(
        self,
        content_id: str,
        mode: str,
        stream_type: str = "output_format=http",
        seekback_seconds: int = 0,
        event_timeout_s: float = 2.0,
    ) -> Dict[str, str]:
        """
        Start stream and optionally perform LIVESEEK on live HTTP streams.

        This mirrors HTTPAceProxy's stabilization strategy: for live streams,
        wait for first livepos EVENT, seek back a few seconds, then use the
        second START URL returned by the engine.
        """
        start_params = self.start_stream(content_id, mode=mode, stream_type=stream_type)

        if int(seekback_seconds or 0) <= 0:
            return start_params

        try:
            is_live = int(start_params.get("stream", "0") or 0) == 1
        except Exception:
            is_live = False

        playback_url = start_params.get("url", "") or ""
        if not is_live or playback_url.endswith(".m3u8"):
            return start_params

        livepos_event: Optional[Dict[str, str]] = None
        deadline = time.time() + max(0.5, float(event_timeout_s))

        while time.time() < deadline:
            try:
                cmd, parts, _ = self._read_message(timeout=max(0.05, deadline - time.time()))
            except Exception:
                break
            if cmd == "EVENT":
                raw = " ".join(parts)
                parsed_event = self.parse_event_line(raw)
                self._handle_async(cmd, parts)
                if parsed_event.get("event") == "livepos":
                    livepos_event = parsed_event
                    break
                continue
            self._handle_async(cmd, parts)

        if not livepos_event:
            return start_params

        last_raw = (
            livepos_event.get("last")
            or livepos_event.get("live_last")
            or livepos_event.get("last_ts")
        )
        if not last_raw:
            return start_params

        try:
            last_ts = int(float(last_raw))
        except (TypeError, ValueError):
            return start_params

        target_ts = max(0, last_ts - int(seekback_seconds))

        try:
            self.live_seek(target_ts)
            _, seek_start_parts, _ = self._wait_for("START", timeout=self.response_timeout * 3)
            return self._parse_start_params(seek_start_parts)
        except Exception as e:
            logger.debug("LIVESEEK follow-up START failed, keeping original URL: %s", e)
            return start_params

    @staticmethod
    def parse_status_line(status_line: str) -> Dict[str, str]:
        """Parse STATUS payload using HTTPAceProxy-compatible normalization rules."""
        if not status_line.startswith("STATUS "):
            return {}

        recvbuffer = status_line.split(" ", 1)[1].split(";")

        if any(x in ["main:wait", "main:seekprebuf"] for x in recvbuffer):
            if len(recvbuffer) > 1:
                del recvbuffer[1]
        elif any(x in ["main:buf", "main:prebuf"] for x in recvbuffer):
            if len(recvbuffer) > 2:
                del recvbuffer[1:3]

        keys = [
            "status",
            "total_progress",
            "immediate_progress",
            "speed_down",
            "http_speed_down",
            "speed_up",
            "peers",
            "http_peers",
            "downloaded",
            "http_downloaded",
            "uploaded",
        ]
        parsed: Dict[str, str] = {}
        for key, value in zip(keys, recvbuffer):
            parsed[key] = value.split(":", 1)[1] if "main:" in value else value
        return parsed

    @staticmethod
    def parse_event_line(event_line: str) -> Dict[str, str]:
        """Parse EVENT line into a flat key/value dictionary."""
        if not event_line.startswith("EVENT "):
            return {}

        parts = event_line.split()
        parsed: Dict[str, str] = {"event": parts[1] if len(parts) > 1 else ""}
        for token in parts[2:]:
            if "=" in token:
                key, value = token.split("=", 1)
                parsed[key] = value
        return parsed

    def collect_status_samples(
        self,
        samples: int = 3,
        interval_s: float = 0.5,
        per_sample_timeout_s: float = 2.0,
    ) -> Dict[str, Any]:
        """Collect STATUS and livepos EVENT data after START for deep availability checks."""
        status_lines = []
        event_lines = []
        last_status: Dict[str, str] = {}
        last_livepos: Dict[str, str] = {}

        for idx in range(max(1, samples)):
            self._write("STATUS")

            deadline = time.time() + max(0.2, per_sample_timeout_s)
            while time.time() < deadline:
                cmd, parts, _ = self._read_message(timeout=max(0.05, deadline - time.time()))

                if cmd == "STATUS":
                    raw = " ".join(parts)
                    status_lines.append(raw)
                    parsed = self.parse_status_line(raw)
                    if parsed:
                        last_status = parsed
                    break

                if cmd == "EVENT":
                    raw = " ".join(parts)
                    event_lines.append(raw)
                    parsed_event = self.parse_event_line(raw)
                    if parsed_event.get("event") == "livepos":
                        last_livepos = parsed_event
                    self._handle_async(cmd, parts)
                    continue

                self._handle_async(cmd, parts)

            if idx < samples - 1:
                time.sleep(max(0.0, interval_s))

        def _to_int(value: Optional[str]) -> Optional[int]:
            if value is None or value == "":
                return None
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

        progress_value = last_status.get("immediate_progress") or last_status.get("total_progress")

        livepos_payload = None
        if last_livepos:
            livepos_payload = {
                "pos": last_livepos.get("pos"),
                "live_first": last_livepos.get("live_first") or last_livepos.get("first_ts"),
                "live_last": last_livepos.get("live_last") or last_livepos.get("last_ts"),
                "first_ts": last_livepos.get("first_ts"),
                "last_ts": last_livepos.get("last_ts"),
                "buffer_pieces": _to_int(last_livepos.get("buffer_pieces")),
                "is_live": _to_int(last_livepos.get("is_live")),
            }

        return {
            "status_text": last_status.get("status"),
            "status": last_status.get("status"),
            "total_progress": _to_int(last_status.get("total_progress")),
            "immediate_progress": _to_int(last_status.get("immediate_progress")),
            "progress": _to_int(progress_value),
            "speed_down": _to_int(last_status.get("speed_down")),
            "http_speed_down": _to_int(last_status.get("http_speed_down")),
            "speed_up": _to_int(last_status.get("speed_up")),
            "peers": _to_int(last_status.get("peers")),
            "http_peers": _to_int(last_status.get("http_peers")),
            "downloaded": _to_int(last_status.get("downloaded")),
            "http_downloaded": _to_int(last_status.get("http_downloaded")),
            "uploaded": _to_int(last_status.get("uploaded")),
            "livepos": livepos_payload,
            "raw_status_lines": status_lines,
            "raw_event_lines": event_lines,
        }

    def preflight(self, content_id: str, tier: str = "light") -> Dict[str, Any]:
        """Run light/deep availability checks and return canonicalized metadata."""
        tier_value = (tier or "light").strip().lower()
        if tier_value not in {"light", "deep"}:
            raise AceLegacyApiError("tier must be either 'light' or 'deep'")

        loadresp, _ = self.resolve_content(content_id, session_id="0")
        status_code = loadresp.get("status")
        available = status_code in (1, 2)
        resolved_infohash = loadresp.get("infohash") or content_id

        payload: Dict[str, Any] = {
            "tier": tier_value,
            "available": available,
            "status_code": status_code,
            "infohash": resolved_infohash,
            "loadresp": loadresp,
            "can_retry": True,
            "should_wait": bool(status_code == 2),
        }

        if not available:
            payload["message"] = loadresp.get("message", "content unavailable")
            return payload

        if tier_value == "deep":
            start_info = self.start_stream(resolved_infohash, mode="infohash")
            status_probe = self.collect_status_samples(samples=3, interval_s=0.5, per_sample_timeout_s=2.0)
            payload["start"] = start_info
            payload["status_probe"] = status_probe

            peers = status_probe.get("peers") or 0
            http_peers = status_probe.get("http_peers") or 0
            status_text = status_probe.get("status_text")
            payload["available"] = bool(peers > 0 or http_peers > 0 or status_text in {"dl", "buf", "prebuf"})

            self.stop_stream()

        return payload

    def stop_stream(self):
        """Stop active playback session on this API connection."""
        self._write("STOP")

    def shutdown(self):
        """Gracefully close remote session then local socket."""
        try:
            self._write("SHUTDOWN")
        except Exception:
            pass
        self.close()

    def _loadasync_pid(self, content_id: str, session_id: str) -> Dict:
        self._write(f"LOADASYNC {session_id} PID {content_id}")
        return self._wait_for_loadresp()

    def _loadasync_infohash(self, infohash: str, session_id: str) -> Dict:
        self._write(f"LOADASYNC {session_id} INFOHASH {infohash} 0 0 0")
        return self._wait_for_loadresp()

    def _wait_for_loadresp(self) -> Dict:
        _, parts, _ = self._wait_for("LOADRESP", timeout=self.response_timeout * 2)
        if len(parts) < 3:
            raise AceLegacyApiError("Malformed LOADRESP from engine")

        payload = unquote(" ".join(parts[2:]))
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AceLegacyApiError(f"Invalid LOADRESP JSON payload: {exc}") from exc

    def _wait_for(self, expected_cmd: str, timeout: float) -> Tuple[str, list, Dict[str, str]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            cmd, parts, kv = self._read_message(timeout=max(0.05, deadline - time.time()))
            if cmd == expected_cmd:
                return cmd, parts, kv

            if cmd == "EVENT" and len(parts) > 2 and parts[1] == "showdialog":
                dialog_text = ""
                for token in parts[2:]:
                    if token.startswith("text="):
                        dialog_text = unquote(token.split("=", 1)[1])
                        break
                if dialog_text:
                    raise AceLegacyApiError(f"Engine dialog error: {dialog_text}")

            self._handle_async(cmd, parts)
        raise AceLegacyApiError(f"Timeout waiting for {expected_cmd}")

    def _handle_async(self, cmd: str, parts: list):
        # Answer data collection profile request; ignore other async signals.
        if cmd == "EVENT" and "getuserdata" in parts:
            self._write(f"USERDATA [{{\"gender\": {self._gender}}}, {{\"age\": {self._age}}}]")

    def _read_message(self, timeout: float) -> Tuple[str, list, Dict[str, str]]:
        if not self._reader:
            raise AceLegacyApiError("API client is not connected")

        if self._sock:
            self._sock.settimeout(timeout)

        line = self._reader.readline()
        if not line:
            raise AceLegacyApiError("Connection closed by AceStream API")

        line = line.strip()
        if not line:
            raise AceLegacyApiError("Received empty line from AceStream API")

        parts = line.split()
        cmd = parts[0]
        kv = {}
        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                kv[key] = value

        logger.debug("[ACE API] <<< %s", line)
        return cmd, parts, kv

    def _write(self, message: str):
        if not self._writer:
            raise AceLegacyApiError("API client is not connected")

        logger.debug("[ACE API] >>> %s", message)
        self._writer.write(f"{message}\r\n")
        self._writer.flush()
