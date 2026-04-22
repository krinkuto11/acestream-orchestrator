"""
Minimal AceStream legacy API client.
Moved from app/proxy to app/data_plane for legacy monitoring support.
"""

import hashlib
import json
import logging
import math
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote, unquote
from ..shared.utils import sanitize_stream_id

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
        self._recv_buffer = b""
        self._authenticated = False
        self._download_stopped_event: Optional[Dict[str, str]] = None
        self._async_event_lock = threading.Lock()

        # Default profile used when engine asks for USERDATA.
        self._gender = 1
        self._age = 3

    @staticmethod
    def _sanitize_id(val: str) -> str:
        return sanitize_stream_id(val)

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        self._sock.settimeout(self.response_timeout)
        self._recv_buffer = b""
        logger.debug("Connected to legacy AceStream API at %s:%s", self.host, self.port)

    def close(self):
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._recv_buffer = b""
        self._authenticated = False

    def authenticate(self):
        self._write("HELLOBG version=4")
        _, parts, kv = self._wait_for("HELLOTS", timeout=self.response_timeout)

        request_key = kv.get("key", "")
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

    def resolve_content(self, content_id: str, session_id: str = "0") -> Tuple[Dict, str]:
        content_ref = self._sanitize_id(content_id)
        self._write(f"LOADASYNC {session_id} PID {content_ref}")
        load_resp = self._wait_for_loadresp()
        return load_resp, "content_id"

    def start_stream(self, content_id: str, mode: str, stream_type: str = "output_format=http", file_indexes: str = "0", live_delay: int = 0) -> Dict[str, str]:
        content_id = self._sanitize_id(content_id)
        cmd = f"START PID {content_id} {file_indexes} {stream_type}"
        self._write(cmd)
        _, parts, _ = self._wait_for("START", timeout=self.response_timeout * 3)
        return self._parse_start_params(parts)

    def stop_stream(self):
        self._write("STOP")

    def shutdown(self):
        try:
            self._write("SHUTDOWN")
        except Exception:
            pass
        self.close()

    def collect_status_samples(self, samples: int = 1, interval_s: float = 0.5, per_sample_timeout_s: float = 2.0) -> Dict[str, Any]:
        self._write("STATUS")
        _, parts, _ = self._wait_for("STATUS", timeout=per_sample_timeout_s)
        raw = " ".join(parts)
        parsed = self.parse_status_line(raw)
        
        # Also try to get livepos
        livepos_payload = None
        deadline = time.time() + 1.0
        while time.time() < deadline:
            try:
                cmd, parts, _ = self._read_message(timeout=0.1)
                if cmd == "EVENT":
                    parsed_event = self.parse_event_line(" ".join(parts))
                    if parsed_event.get("event") == "livepos":
                        livepos_payload = parsed_event
                        break
                self._handle_async(cmd, parts)
            except:
                break

        return {
            "status": parsed.get("status"),
            "peers": int(parsed.get("peers", 0)) if parsed.get("peers") else 0,
            "speed_down": int(parsed.get("speed_down", 0)) if parsed.get("speed_down") else 0,
            "livepos": livepos_payload
        }

    def consume_download_stopped_event(self) -> Optional[Dict[str, str]]:
        with self._async_event_lock:
            event = self._download_stopped_event
            self._download_stopped_event = None
            return event

    def _wait_for_loadresp(self) -> Dict:
        _, parts, _ = self._wait_for("LOADRESP", timeout=self.response_timeout * 2)
        payload = unquote(" ".join(parts[2:]))
        return json.loads(payload)

    def _write(self, msg: str):
        if not self._sock: raise AceLegacyApiError("Not connected")
        self._sock.sendall((msg + "\r\n").encode("utf-8"))

    def _read_message(self, timeout: float) -> Tuple[str, list, Dict[str, str]]:
        self._sock.settimeout(timeout)
        while True:
            if b"\r\n" in self._recv_buffer:
                line_bytes, self._recv_buffer = self._recv_buffer.split(b"\r\n", 1)
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line: continue
                parts = line.split()
                cmd = parts[0].upper()
                kv = {}
                for p in parts[1:]:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        kv[k] = v
                return cmd, parts, kv
            chunk = self._sock.recv(4096)
            if not chunk: raise AceLegacyApiError("Connection closed")
            self._recv_buffer += chunk

    def _wait_for(self, expected_cmd: str, timeout: float) -> Tuple[str, list, Dict[str, str]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            cmd, parts, kv = self._read_message(timeout=max(0.05, deadline - time.time()))
            if cmd == expected_cmd: return cmd, parts, kv
            self._handle_async(cmd, parts)
        raise AceLegacyApiError(f"Timeout waiting for {expected_cmd}")

    def _handle_async(self, cmd: str, parts: list):
        if cmd == "EVENT" and len(parts) > 1 and parts[1] == "download_stopped":
            self._download_stopped_event = self.parse_event_line(" ".join(parts))

    @staticmethod
    def parse_status_line(status_line: str) -> Dict[str, str]:
        parts = status_line.split(" ", 1)[1].split(";")
        parsed = {}
        keys = ["status", "total_progress", "immediate_progress", "speed_down", "http_speed_down", "speed_up", "peers"]
        for k, v in zip(keys, parts):
            parsed[k] = v.split(":", 1)[1] if "main:" in v else v
        return parsed

    @staticmethod
    def parse_event_line(line: str) -> Dict[str, str]:
        parts = line.split()
        parsed = {"event": parts[1] if len(parts) > 1 else ""}
        for p in parts[2:]:
            if "=" in p:
                k, v = p.split("=", 1)
                parsed[k] = unquote(v)
        return parsed

    @staticmethod
    def _parse_start_params(parts: list) -> Dict[str, str]:
        params = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k] = unquote(v)
        return params
