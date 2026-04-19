from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # Python 3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

import httpx

from .proton_updater import ProtonFilterConfig, ProtonServerUpdater
from ..persistence.settings_persistence import SettingsPersistence
from . import gluetun_servers_volume

logger = logging.getLogger(__name__)

OFFICIAL_GLUETUN_SERVERS_URL = (
    "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json"
)

_REFRESH_SOURCES = {"proton_paid", "gluetun_official"}
_JSON_MODES = {"none", "replace", "update"}
_CREDENTIAL_SOURCES = {"env", "settings"}
_FILTER_VALUES = {"include", "exclude", "only"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_now_iso() -> str:
    """Return a local-timezone-aware ISO 8601 timestamp respecting the TZ env var."""
    tz_name = str(os.getenv("TZ", "")).strip()
    if tz_name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
            return datetime.now(tz).isoformat()
        except (ZoneInfoNotFoundError, Exception):
            pass
    return datetime.now(timezone.utc).isoformat()


def _validate_env_var_name(name: str) -> bool:
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)


class VPNServersRefreshService:
    """Automatic VPN provider servers list refresh loop with manual trigger support."""

    def __init__(self):
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._refresh_lock = asyncio.Lock()
        self._status: Dict[str, Any] = {
            "running": False,
            "in_progress": False,
            "last_started_at": None,
            "last_finished_at": None,
            "last_ok": None,
            "last_reason": None,
            "last_source": None,
            "last_error": None,
            "last_result": None,
            "official_url": OFFICIAL_GLUETUN_SERVERS_URL,
        }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="vpn-servers-refresh")
        self._status["running"] = True
        logger.info("VPN servers refresh service started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        self._status["running"] = False
        logger.info("VPN servers refresh service stopped")

    def get_status(self) -> Dict[str, Any]:
        settings = SettingsPersistence.load_vpn_config() or {}
        return {
            **self._status,
            "config": {
                "auto_refresh": bool(settings.get("vpn_servers_auto_refresh", False)),
                "refresh_period_s": int(settings.get("vpn_servers_refresh_period_s", 86400)),
                "source": str(settings.get("vpn_servers_refresh_source", "gluetun_official")),
                "gluetun_json_mode": str(settings.get("vpn_servers_gluetun_json_mode", "update")),
                "proton_credentials_source": str(
                    settings.get("vpn_servers_proton_credentials_source", "env")
                ),
            },
        }

    async def refresh_now(
        self,
        *,
        reason: str = "manual",
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self._refresh_lock.locked():
            return {
                "ok": False,
                "detail": "refresh already in progress",
                "in_progress": True,
                "status": self.get_status(),
            }

        async with self._refresh_lock:
            started = time.monotonic()
            self._status["in_progress"] = True
            self._status["last_started_at"] = _local_now_iso()
            self._status["last_reason"] = str(reason or "manual")
            self._status["last_error"] = None

            try:
                effective = self._effective_settings(overrides)
                source = effective["vpn_servers_refresh_source"]
                self._status["last_source"] = source

                if source == "proton_paid":
                    result = await self._refresh_proton(effective)
                else:
                    result = await self._refresh_official_gluetun(effective)

                duration_s = round(time.monotonic() - started, 3)
                payload = {
                    "ok": True,
                    "reason": self._status["last_reason"],
                    "source": source,
                    "duration_s": duration_s,
                    **result,
                }
                self._status["last_ok"] = True
                self._status["last_result"] = payload
                return payload
            except Exception as exc:
                duration_s = round(time.monotonic() - started, 3)
                self._status["last_ok"] = False
                self._status["last_error"] = str(exc)
                self._status["last_result"] = {
                    "ok": False,
                    "reason": self._status["last_reason"],
                    "source": self._status.get("last_source"),
                    "duration_s": duration_s,
                    "error": str(exc),
                }
                logger.exception("VPN servers refresh failed")
                raise
            finally:
                self._status["in_progress"] = False
                self._status["last_finished_at"] = _local_now_iso()

    async def _run(self) -> None:
        while not self._stop.is_set():
            settings = SettingsPersistence.load_vpn_config() or {}
            enabled = bool(settings.get("vpn_servers_auto_refresh", False))
            interval_s = max(60, int(settings.get("vpn_servers_refresh_period_s", 86400)))

            timeout = interval_s if enabled else 30
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                if not enabled:
                    continue
                try:
                    await self.refresh_now(reason="scheduled")
                except Exception as exc:
                    logger.warning("Scheduled VPN servers refresh failed: %s", exc)

    def _effective_settings(self, overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        base = SettingsPersistence.load_vpn_config() or {}
        merged = dict(base)
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                if value is not None:
                    merged[key] = value

        merged.setdefault("vpn_servers_refresh_source", "gluetun_official")
        merged.setdefault("vpn_servers_gluetun_json_mode", "update")
        merged.setdefault("vpn_servers_proton_credentials_source", "env")
        merged.setdefault("vpn_servers_official_url", OFFICIAL_GLUETUN_SERVERS_URL)
        merged.setdefault("vpn_servers_filter_ipv6", "exclude")
        merged.setdefault("vpn_servers_filter_secure_core", "include")
        merged.setdefault("vpn_servers_filter_tor", "include")
        merged.setdefault("vpn_servers_filter_free_tier", "include")

        source = str(merged.get("vpn_servers_refresh_source") or "").strip().lower()
        if source not in _REFRESH_SOURCES:
            raise ValueError("vpn_servers_refresh_source must be one of: proton_paid|gluetun_official")
        merged["vpn_servers_refresh_source"] = source

        json_mode = str(merged.get("vpn_servers_gluetun_json_mode") or "").strip().lower()
        if json_mode not in _JSON_MODES:
            raise ValueError("vpn_servers_gluetun_json_mode must be one of: none|replace|update")
        merged["vpn_servers_gluetun_json_mode"] = json_mode

        credentials_source = str(
            merged.get("vpn_servers_proton_credentials_source") or ""
        ).strip().lower()
        if credentials_source not in _CREDENTIAL_SOURCES:
            raise ValueError("vpn_servers_proton_credentials_source must be one of: env|settings")
        merged["vpn_servers_proton_credentials_source"] = credentials_source

        for key in (
            "vpn_servers_filter_ipv6",
            "vpn_servers_filter_secure_core",
            "vpn_servers_filter_tor",
            "vpn_servers_filter_free_tier",
        ):
            value = str(merged.get(key) or "").strip().lower()
            if value not in _FILTER_VALUES:
                raise ValueError(f"{key} must be one of: include|exclude|only")
            merged[key] = value

        for key, fallback in (
            ("vpn_servers_proton_username_env", "PROTON_USERNAME"),
            ("vpn_servers_proton_password_env", "PROTON_PASSWORD"),
            ("vpn_servers_proton_totp_code_env", "PROTON_TOTP_CODE"),
            ("vpn_servers_proton_totp_secret_env", "PROTON_TOTP_SECRET"),
        ):
            raw = str(merged.get(key) or fallback).strip()
            if not _validate_env_var_name(raw):
                raise ValueError(f"{key} must be a valid environment variable name")
            merged[key] = raw

        return merged

    @staticmethod
    def _resolve_storage_dir(settings: Dict[str, Any]) -> Path:
        configured = str(settings.get("vpn_servers_storage_path") or "").strip()
        if not configured:
            configured = str(os.getenv("GLUETUN_SERVERS_JSON_PATH", "")).strip()

        if configured:
            path = Path(configured)
            if path.suffix:
                return path.parent
            return path

        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, indent=2)

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as temp_file:
            temp_file.write(data)
            temp_name = temp_file.name

        os.replace(temp_name, path)

    @staticmethod
    def _load_existing_json(path: Path) -> Dict[str, Any]:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            logger.warning("Invalid existing JSON at %s; replacing", path)
        return {"version": 1}

    async def _refresh_official_gluetun(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        source_url = str(settings.get("vpn_servers_official_url") or OFFICIAL_GLUETUN_SERVERS_URL).strip()
        if not source_url:
            raise ValueError("vpn_servers_official_url cannot be empty")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(source_url)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict) or not payload:
            raise ValueError("Official Gluetun servers payload is not a JSON object")

        storage_dir = self._resolve_storage_dir(settings)
        storage_dir.mkdir(parents=True, exist_ok=True)

        mode = settings["vpn_servers_gluetun_json_mode"]
        official_file = storage_dir / "servers-official.json"
        merged_file = storage_dir / "servers.json"

        self._atomic_write_json(official_file, payload)

        if mode == "replace":
            self._atomic_write_json(merged_file, payload)
        elif mode == "update":
            existing = self._load_existing_json(merged_file)
            existing["version"] = payload.get("version", existing.get("version", 1))

            # If Proton API refresh is the primary source, we preserve the
            # protonvpn key in our local servers.json to prevent it from
            # being overwritten by stale data from the official repository.
            preserve_proton = (
                settings.get("vpn_servers_refresh_source") == "proton_paid"
                and "protonvpn" in existing
            )

            for provider_key, provider_value in payload.items():
                if provider_key == "version":
                    continue
                if provider_key == "protonvpn" and preserve_proton:
                    # If we are in Proton Paid mode, we favor the local dedicated catalog
                    # which likely contains more nodes (specifically HA/Paid nodes) than
                    # the official Gluetun list.
                    proton_dedicated_file = storage_dir / "servers-proton.json"
                    proton_data = self._load_existing_json(proton_dedicated_file)
                    if "protonvpn" in proton_data:
                        existing["protonvpn"] = proton_data["protonvpn"]
                        logger.info("Injected %d Proton Paid servers from dedicated catalog into merged list", len(proton_data["protonvpn"].get("servers", [])))
                    else:
                        logger.debug("Dedicated Proton catalog not found or empty; falling back to official list for 'protonvpn'")
                        existing["protonvpn"] = provider_value
                    continue

                existing[provider_key] = provider_value
            self._atomic_write_json(merged_file, existing)

        provider_count = max(0, len([k for k in payload.keys() if k != "version"]))
        result = {
            "storage_path": str(storage_dir),
            "source_url": source_url,
            "gluetun_json_mode": mode,
            "servers_official_file": str(official_file),
            "servers_file": str(merged_file),
            "stats": {
                "providers": provider_count,
            },
        }

        # Propagate the updated catalog into the shared Gluetun volume so that
        # newly-provisioned Gluetun containers validate hostnames against the
        # refreshed list rather than their stale bundled catalog.
        if mode != "none":
            await asyncio.to_thread(gluetun_servers_volume.sync, merged_file)

        return result

    async def _refresh_proton(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        credentials_source = settings["vpn_servers_proton_credentials_source"]

        proton_username = None
        proton_password = None
        proton_totp_code = None
        proton_totp_secret = None

        if credentials_source == "settings":
            proton_username = str(settings.get("vpn_servers_proton_username") or "").strip() or None
            proton_password = str(settings.get("vpn_servers_proton_password") or "").strip() or None
            proton_totp_code = str(settings.get("vpn_servers_proton_totp_code") or "").strip() or None
            proton_totp_secret = str(settings.get("vpn_servers_proton_totp_secret") or "").strip() or None
        else:
            proton_username = os.getenv(settings["vpn_servers_proton_username_env"])
            proton_password = os.getenv(settings["vpn_servers_proton_password_env"])
            proton_totp_code = os.getenv(settings["vpn_servers_proton_totp_code_env"])
            proton_totp_secret = os.getenv(settings["vpn_servers_proton_totp_secret_env"])

        updater = ProtonServerUpdater(storage_path=settings.get("vpn_servers_storage_path"))
        filters = ProtonFilterConfig(
            ipv6=settings["vpn_servers_filter_ipv6"],
            secure_core=settings["vpn_servers_filter_secure_core"],
            tor=settings["vpn_servers_filter_tor"],
            free_tier=settings["vpn_servers_filter_free_tier"],
        )

        result = await updater.update(
            proton_username=proton_username,
            proton_password=proton_password,
            proton_totp_code=proton_totp_code,
            proton_totp_secret=proton_totp_secret,
            filters=filters,
            gluetun_json_mode=settings["vpn_servers_gluetun_json_mode"],
        )

        # Propagate the updated catalog into the shared Gluetun volume.
        merged_file = result.get("servers_file")
        if merged_file and settings["vpn_servers_gluetun_json_mode"] != "none":
            await asyncio.to_thread(gluetun_servers_volume.sync, Path(merged_file))

        return {
            **result,
            "credentials_source": credentials_source,
        }


vpn_servers_refresh_service = VPNServersRefreshService()
