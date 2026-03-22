"""
Minimal AceStream legacy API client.

Implements the telnet-style control protocol used on the AceStream API port.
This module is optional and only used when proxy control mode is LEGACY_API.
"""

import hashlib
import json
import logging
import socket
import time
from typing import Dict, Optional, Tuple
from urllib.parse import unquote

logger = logging.getLogger(__name__)

DEFAULT_ACE_PRODUCT_KEY = "n51LvQoTlJzNGaFxseRK-uvnvX-sD4Vm5Axwmc4UcoD-jruxmKsuJaH0eVgE"


class AceLegacyApiError(Exception):
    """Raised when the AceStream legacy API fails."""


class AceLegacyApiClient:
    """Simple line-based client for the AceStream legacy API port."""

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

    def resolve_content(self, content_id: str, session_id: str) -> Tuple[Dict, str]:
        """
        Resolve metadata using LOADASYNC.

        Returns tuple of (load_response_json, mode) where mode is one of
        {"content_id", "infohash"} and should be reused for START.
        """
        # First try PID/content_id mode.
        load_resp = self._loadasync_pid(content_id, session_id)
        if load_resp.get("status") in (1, 2):
            return load_resp, "content_id"

        # Fallback to INFOHASH mode.
        load_resp = self._loadasync_infohash(content_id, session_id)
        if load_resp.get("status") in (1, 2):
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

        params = {}
        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                params[key] = value
        return params

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
