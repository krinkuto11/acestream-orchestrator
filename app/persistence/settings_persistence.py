"""Database-backed runtime settings persistence with in-memory cache."""

from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .db import get_session
from ..core.config import cfg
from ..models.db_models import RuntimeSettingsRow, VPNCredentialRow

logger = logging.getLogger(__name__)


class SettingsPersistence:
    """Persist settings in SQLite and serve reads from a hot in-memory cache."""

    SETTINGS_ROW_ID = 1

    _lock = threading.RLock()
    _cache_initialized = False
    _cache: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _default_engine_config() -> Dict[str, Any]:
        return {
            "total_max_download_rate": 0,
            "total_max_upload_rate": 0,
            "live_cache_type": "memory",
            "buffer_time": 10,
            "memory_limit": None,
            "parameters": [],
            "torrent_folder_mount_enabled": False,
            "torrent_folder_host_path": None,
            "torrent_folder_container_path": None,
            "disk_cache_mount_enabled": False,
            "disk_cache_prune_enabled": False,
            "disk_cache_prune_interval": 1440,
        }

    @staticmethod
    def _default_engine_settings() -> Dict[str, Any]:
        return {
            "min_replicas": int(getattr(cfg, "MIN_REPLICAS", 2)),
            "max_replicas": int(getattr(cfg, "MAX_REPLICAS", 6)),
            "auto_delete": bool(getattr(cfg, "AUTO_DELETE", True)),
            "manual_mode": False,
            "manual_engines": [],
        }

    @staticmethod
    def _default_orchestrator_settings() -> Dict[str, Any]:
        return {
            "monitor_interval_s": int(getattr(cfg, "MONITOR_INTERVAL_S", 10)),
            "engine_grace_period_s": int(getattr(cfg, "ENGINE_GRACE_PERIOD_S", 30)),
            "autoscale_interval_s": int(getattr(cfg, "AUTOSCALE_INTERVAL_S", 30)),
            "startup_timeout_s": int(getattr(cfg, "STARTUP_TIMEOUT_S", 25)),
            "idle_ttl_s": int(getattr(cfg, "IDLE_TTL_S", 600)),
            "collect_interval_s": 1,
            "stats_history_max": int(getattr(cfg, "STATS_HISTORY_MAX", 720)),
            "health_check_interval_s": int(getattr(cfg, "HEALTH_CHECK_INTERVAL_S", 20)),
            "health_failure_threshold": int(getattr(cfg, "HEALTH_FAILURE_THRESHOLD", 3)),
            "health_unhealthy_grace_period_s": int(getattr(cfg, "HEALTH_UNHEALTHY_GRACE_PERIOD_S", 60)),
            "health_replacement_cooldown_s": int(getattr(cfg, "HEALTH_REPLACEMENT_COOLDOWN_S", 60)),
            "circuit_breaker_failure_threshold": int(getattr(cfg, "CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5)),
            "circuit_breaker_recovery_timeout_s": int(getattr(cfg, "CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S", 300)),
            "circuit_breaker_replacement_threshold": int(getattr(cfg, "CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD", 3)),
            "circuit_breaker_replacement_timeout_s": int(getattr(cfg, "CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S", 180)),
            "max_concurrent_provisions": int(getattr(cfg, "MAX_CONCURRENT_PROVISIONS", 5)),
            "min_provision_interval_s": float(getattr(cfg, "MIN_PROVISION_INTERVAL_S", 0.5)),
            "port_range_host": str(getattr(cfg, "PORT_RANGE_HOST", "19000-19999")),
            "ace_http_range": str(getattr(cfg, "ACE_HTTP_RANGE", "40000-44999")),
            "ace_https_range": str(getattr(cfg, "ACE_HTTPS_RANGE", "45000-49999")),
            "ace_live_edge_delay": int(getattr(cfg, "ACE_LIVE_EDGE_DELAY", 0)),
            "debug_mode": bool(getattr(cfg, "DEBUG_MODE", False)),
        }

    @staticmethod
    def _default_proxy_settings() -> Dict[str, Any]:
        return {
            "initial_data_wait_timeout": 10,
            "initial_data_check_interval": 0.2,
            "no_data_timeout_checks": 60,
            "no_data_check_interval": 1.0,
            "connection_timeout": 30,
            "upstream_connect_timeout": 3,
            "upstream_read_timeout": 90,
            "stream_timeout": 60,
            "channel_shutdown_delay": 5,
            "proxy_prebuffer_seconds": 3,
            "pacing_bitrate_multiplier": 1.5,
            "max_streams_per_engine": int(getattr(cfg, "MAX_STREAMS_PER_ENGINE", 3)),
            "stream_mode": "TS",
            "control_mode": "api",
            "legacy_api_preflight_tier": "light",
            "ace_live_edge_delay": int(getattr(cfg, "ACE_LIVE_EDGE_DELAY", 0)),
            "hls_max_segments": 20,
            "hls_initial_segments": 3,
            "hls_window_size": 6,
            "hls_buffer_ready_timeout": 30,
            "hls_first_segment_timeout": 30,
            "hls_initial_buffer_seconds": 10,
            "hls_max_initial_segments": 10,
            "hls_segment_fetch_interval": 0.5,
        }

    @staticmethod
    def normalize_proxy_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Backfill and sanitize proxy settings for schema evolution compatibility."""
        defaults = SettingsPersistence._default_proxy_settings()
        normalized = {**defaults, **dict(config or {})}

        int_fields = (
            "initial_data_wait_timeout",
            "no_data_timeout_checks",
            "connection_timeout",
            "upstream_connect_timeout",
            "upstream_read_timeout",
            "stream_timeout",
            "channel_shutdown_delay",
            "proxy_prebuffer_seconds",
            "max_streams_per_engine",
            "ace_live_edge_delay",
            "hls_max_segments",
            "hls_initial_segments",
            "hls_window_size",
            "hls_buffer_ready_timeout",
            "hls_first_segment_timeout",
            "hls_initial_buffer_seconds",
            "hls_max_initial_segments",
        )
        float_fields = (
            "initial_data_check_interval",
            "no_data_check_interval",
            "hls_segment_fetch_interval",
        )

        for key in int_fields:
            try:
                normalized[key] = int(normalized.get(key, defaults[key]))
            except Exception:
                normalized[key] = int(defaults[key])

        for key in float_fields:
            try:
                normalized[key] = float(normalized.get(key, defaults[key]))
            except Exception:
                normalized[key] = float(defaults[key])

        normalized["proxy_prebuffer_seconds"] = max(0, int(normalized.get("proxy_prebuffer_seconds", 0)))
        normalized["ace_live_edge_delay"] = max(0, int(normalized.get("ace_live_edge_delay", 0)))
        normalized["max_streams_per_engine"] = max(1, int(normalized.get("max_streams_per_engine", defaults["max_streams_per_engine"])))

        stream_mode = str(normalized.get("stream_mode") or defaults["stream_mode"]).strip().upper()
        normalized["stream_mode"] = stream_mode if stream_mode in {"TS", "HLS"} else defaults["stream_mode"]

        control_mode = str(normalized.get("control_mode") or defaults["control_mode"]).strip().lower()
        normalized["control_mode"] = control_mode if control_mode in {"http", "api"} else defaults["control_mode"]

        tier = str(normalized.get("legacy_api_preflight_tier") or defaults["legacy_api_preflight_tier"]).strip().lower()
        normalized["legacy_api_preflight_tier"] = tier if tier in {"light", "deep"} else defaults["legacy_api_preflight_tier"]

        return normalized



    @staticmethod
    def _default_vpn_settings() -> Dict[str, Any]:
        return {
            "enabled": False,
            "dynamic_vpn_management": True,
            "preferred_engines_per_vpn": int(getattr(cfg, "PREFERRED_ENGINES_PER_VPN", 10)),
            "protocol": str(getattr(cfg, "VPN_PROTOCOL", "wireguard") or "wireguard"),
            "provider": str(getattr(cfg, "VPN_PROVIDER", "protonvpn") or "protonvpn"),
            "regions": [],
            "api_port": int(getattr(cfg, "GLUETUN_API_PORT", 8001)),
            "health_check_interval_s": int(getattr(cfg, "GLUETUN_HEALTH_CHECK_INTERVAL_S", 5)),
            "port_cache_ttl_s": int(getattr(cfg, "GLUETUN_PORT_CACHE_TTL_S", 60)),
            "restart_engines_on_reconnect": bool(getattr(cfg, "VPN_RESTART_ENGINES_ON_RECONNECT", True)),
            "unhealthy_restart_timeout_s": int(getattr(cfg, "VPN_UNHEALTHY_RESTART_TIMEOUT_S", 60)),
            "vpn_servers_auto_refresh": False,
            "vpn_servers_refresh_period_s": int(getattr(cfg, "VPN_SERVERS_REFRESH_PERIOD_S", 86400)),
            "vpn_servers_refresh_source": str(getattr(cfg, "VPN_SERVERS_REFRESH_SOURCE", "gluetun_official") or "gluetun_official"),
            "vpn_servers_gluetun_json_mode": str(getattr(cfg, "VPN_SERVERS_GLUETUN_JSON_MODE", "update") or "update"),
            "vpn_servers_storage_path": str(getattr(cfg, "VPN_SERVERS_STORAGE_PATH", "") or "").strip() or None,
            "vpn_servers_official_url": str(
                getattr(
                    cfg,
                    "VPN_SERVERS_OFFICIAL_URL",
                    "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json",
                )
                or "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json"
            ),
            "vpn_servers_proton_credentials_source": str(
                getattr(cfg, "VPN_SERVERS_PROTON_CREDENTIALS_SOURCE", "env") or "env"
            ),
            "vpn_servers_proton_username_env": str(getattr(cfg, "VPN_SERVERS_PROTON_USERNAME_ENV", "PROTON_USERNAME") or "PROTON_USERNAME"),
            "vpn_servers_proton_password_env": str(getattr(cfg, "VPN_SERVERS_PROTON_PASSWORD_ENV", "PROTON_PASSWORD") or "PROTON_PASSWORD"),
            "vpn_servers_proton_totp_code_env": str(getattr(cfg, "VPN_SERVERS_PROTON_TOTP_CODE_ENV", "PROTON_TOTP_CODE") or "PROTON_TOTP_CODE"),
            "vpn_servers_proton_totp_secret_env": str(getattr(cfg, "VPN_SERVERS_PROTON_TOTP_SECRET_ENV", "PROTON_TOTP_SECRET") or "PROTON_TOTP_SECRET"),
            "vpn_servers_proton_username": str(getattr(cfg, "VPN_SERVERS_PROTON_USERNAME", "") or "").strip() or None,
            "vpn_servers_proton_password": str(getattr(cfg, "VPN_SERVERS_PROTON_PASSWORD", "") or "").strip() or None,
            "vpn_servers_proton_totp_code": str(getattr(cfg, "VPN_SERVERS_PROTON_TOTP_CODE", "") or "").strip() or None,
            "vpn_servers_proton_totp_secret": str(getattr(cfg, "VPN_SERVERS_PROTON_TOTP_SECRET", "") or "").strip() or None,
            "vpn_servers_filter_ipv6": str(getattr(cfg, "VPN_SERVERS_FILTER_IPV6", "exclude") or "exclude"),
            "vpn_servers_filter_secure_core": str(getattr(cfg, "VPN_SERVERS_FILTER_SECURE_CORE", "include") or "include"),
            "vpn_servers_filter_tor": str(getattr(cfg, "VPN_SERVERS_FILTER_TOR", "include") or "include"),
            "vpn_servers_filter_free_tier": str(getattr(cfg, "VPN_SERVERS_FILTER_FREE_TIER", "include") or "include"),
        }

    @staticmethod
    def _deepcopy(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return copy.deepcopy(data or {})

    @staticmethod
    def _normalize_credential_record(
        credential: Dict[str, Any],
        *,
        default_provider: Optional[str],
        default_protocol: Optional[str],
    ) -> Dict[str, Any]:
        item = dict(credential or {})
        item["id"] = str(item.get("id") or f"cred-{uuid4().hex}")

        provider = str(item.get("provider") or default_provider or "").strip().lower()
        if provider:
            item["provider"] = provider

        protocol = str(item.get("protocol") or default_protocol or "").strip().lower()
        if protocol:
            item["protocol"] = protocol

        return item

    @staticmethod
    def _normalize_credentials(
        credentials: Optional[List[Dict[str, Any]]],
        *,
        default_provider: Optional[str],
        default_protocol: Optional[str],
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for raw in credentials or []:
            if not isinstance(raw, dict):
                continue
            normalized.append(
                SettingsPersistence._normalize_credential_record(
                    raw,
                    default_provider=default_provider,
                    default_protocol=default_protocol,
                )
            )
        return normalized

    @staticmethod
    def normalize_vpn_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Backfill missing VPN settings keys for schema evolution compatibility."""
        defaults = SettingsPersistence._default_vpn_settings()
        normalized = {**defaults, **dict(config or {})}

        normalized["dynamic_vpn_management"] = True

        if "providers" in normalized and not normalized.get("provider"):
            legacy = normalized.get("providers")
            if isinstance(legacy, list) and legacy:
                normalized["provider"] = str(legacy[0]).strip()
        normalized.pop("providers", None)

        for legacy_key in ("vpn_mode", "container_name", "container_name_2", "port_range_1", "port_range_2"):
            normalized.pop(legacy_key, None)

        if not isinstance(normalized.get("regions"), list):
            normalized["regions"] = []

        try:
            normalized["preferred_engines_per_vpn"] = max(1, int(normalized.get("preferred_engines_per_vpn", 10)))
        except Exception:
            normalized["preferred_engines_per_vpn"] = 10

        normalized["provider"] = str(normalized.get("provider") or defaults["provider"]).strip().lower()
        normalized["protocol"] = str(normalized.get("protocol") or defaults["protocol"]).strip().lower()

        for int_field in ("api_port", "health_check_interval_s", "port_cache_ttl_s", "unhealthy_restart_timeout_s"):
            try:
                normalized[int_field] = int(normalized.get(int_field, defaults[int_field]))
            except Exception:
                normalized[int_field] = int(defaults[int_field])

        try:
            normalized["vpn_servers_refresh_period_s"] = max(60, int(normalized.get("vpn_servers_refresh_period_s", defaults["vpn_servers_refresh_period_s"])))
        except Exception:
            normalized["vpn_servers_refresh_period_s"] = int(defaults["vpn_servers_refresh_period_s"])

        normalized["vpn_servers_auto_refresh"] = bool(
            normalized.get("vpn_servers_auto_refresh", defaults["vpn_servers_auto_refresh"])
        )

        normalized["vpn_servers_refresh_source"] = str(
            normalized.get("vpn_servers_refresh_source") or defaults["vpn_servers_refresh_source"]
        ).strip().lower()
        if normalized["vpn_servers_refresh_source"] not in {"proton_paid", "gluetun_official"}:
            normalized["vpn_servers_refresh_source"] = defaults["vpn_servers_refresh_source"]

        normalized["vpn_servers_gluetun_json_mode"] = str(
            normalized.get("vpn_servers_gluetun_json_mode") or defaults["vpn_servers_gluetun_json_mode"]
        ).strip().lower()
        if normalized["vpn_servers_gluetun_json_mode"] not in {"none", "replace", "update"}:
            normalized["vpn_servers_gluetun_json_mode"] = defaults["vpn_servers_gluetun_json_mode"]

        normalized["vpn_servers_proton_credentials_source"] = str(
            normalized.get("vpn_servers_proton_credentials_source") or defaults["vpn_servers_proton_credentials_source"]
        ).strip().lower()
        if normalized["vpn_servers_proton_credentials_source"] not in {"env", "settings"}:
            normalized["vpn_servers_proton_credentials_source"] = defaults["vpn_servers_proton_credentials_source"]

        normalized["vpn_servers_storage_path"] = str(
            normalized.get("vpn_servers_storage_path") or ""
        ).strip() or None
        normalized["vpn_servers_official_url"] = str(
            normalized.get("vpn_servers_official_url") or defaults["vpn_servers_official_url"]
        ).strip()

        for env_field in (
            "vpn_servers_proton_username_env",
            "vpn_servers_proton_password_env",
            "vpn_servers_proton_totp_code_env",
            "vpn_servers_proton_totp_secret_env",
        ):
            normalized[env_field] = str(normalized.get(env_field) or defaults[env_field]).strip() or defaults[env_field]

        for secret_field in (
            "vpn_servers_proton_username",
            "vpn_servers_proton_password",
            "vpn_servers_proton_totp_code",
            "vpn_servers_proton_totp_secret",
        ):
            normalized[secret_field] = str(normalized.get(secret_field) or "").strip() or None

        for filter_field in (
            "vpn_servers_filter_ipv6",
            "vpn_servers_filter_secure_core",
            "vpn_servers_filter_tor",
            "vpn_servers_filter_free_tier",
        ):
            value = str(normalized.get(filter_field) or defaults[filter_field]).strip().lower()
            if value not in {"include", "exclude", "only"}:
                value = defaults[filter_field]
            normalized[filter_field] = value

        normalized["enabled"] = bool(normalized.get("enabled", defaults["enabled"]))
        normalized["restart_engines_on_reconnect"] = bool(
            normalized.get("restart_engines_on_reconnect", defaults["restart_engines_on_reconnect"])
        )

        return normalized

    @staticmethod
    def ensure_config_dir():
        """Compatibility no-op retained for legacy callers."""
        return True

    @classmethod
    def _ensure_settings_row(cls, session) -> RuntimeSettingsRow:
        row = session.get(RuntimeSettingsRow, cls.SETTINGS_ROW_ID)
        if row:
            return row

        row = RuntimeSettingsRow(
            id=cls.SETTINGS_ROW_ID,
            engine_config=cls._default_engine_config(),
            engine_settings=cls._default_engine_settings(),
            orchestrator_settings=cls._default_orchestrator_settings(),
            proxy_settings=cls._default_proxy_settings(),
            vpn_settings=cls._default_vpn_settings(),
        )
        session.add(row)
        session.flush()
        return row

    @classmethod
    def _load_credentials_from_db(cls, session, settings_id: int) -> List[Dict[str, Any]]:
        rows = (
            session.query(VPNCredentialRow)
            .filter(VPNCredentialRow.settings_id == settings_id)
            .order_by(VPNCredentialRow.created_at.asc())
            .all()
        )
        return [dict(r.payload or {}) for r in rows]

    @classmethod
    def _upsert_credentials(cls, session, settings_id: int, credentials: List[Dict[str, Any]]) -> None:
        session.query(VPNCredentialRow).filter(VPNCredentialRow.settings_id == settings_id).delete(synchronize_session=False)

        now = datetime.now(timezone.utc)
        for item in credentials:
            provider = str(item.get("provider") or "").strip().lower() or None
            protocol = str(item.get("protocol") or "").strip().lower() or None
            session.add(
                VPNCredentialRow(
                    id=str(item.get("id") or f"cred-{uuid4().hex}"),
                    settings_id=settings_id,
                    provider=provider,
                    protocol=protocol,
                    payload=dict(item),
                    created_at=now,
                    updated_at=now,
                )
            )

    @classmethod
    def _row_to_cache(cls, session, row: RuntimeSettingsRow) -> Dict[str, Dict[str, Any]]:
        engine_config = cls._deepcopy(row.engine_config) or cls._default_engine_config()
        engine_settings = cls._deepcopy(row.engine_settings) or cls._default_engine_settings()
        orchestrator_settings = cls._deepcopy(row.orchestrator_settings) or cls._default_orchestrator_settings()
        proxy_settings = cls.normalize_proxy_config(cls._deepcopy(row.proxy_settings) or cls._default_proxy_settings())
        vpn_settings = cls.normalize_vpn_config(cls._deepcopy(row.vpn_settings) or cls._default_vpn_settings())

        credentials = cls._normalize_credentials(
            cls._load_credentials_from_db(session, row.id),
            default_provider=vpn_settings.get("provider"),
            default_protocol=vpn_settings.get("protocol"),
        )
        vpn_settings["credentials"] = credentials

        return {
            "engine_config": engine_config,
            "engine_settings": engine_settings,
            "orchestrator_settings": orchestrator_settings,
            "proxy_settings": proxy_settings,
            "vpn_settings": vpn_settings,
        }

    @classmethod
    def _migrate_runtime_settings_row(cls, session, row: RuntimeSettingsRow) -> bool:
        """Apply schema backfills to RuntimeSettings and persist if anything changed."""
        migrated = False

        proxy_before = cls._deepcopy(row.proxy_settings)
        proxy_after = cls.normalize_proxy_config(proxy_before)
        if proxy_after != proxy_before:
            row.proxy_settings = proxy_after
            migrated = True

        if migrated:
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)

        return migrated

    @classmethod
    def initialize_cache(cls, force_reload: bool = False) -> None:
        with cls._lock:
            if cls._cache_initialized and not force_reload:
                return

            try:
                with get_session() as session:
                    row = cls._ensure_settings_row(session)
                    cls._migrate_runtime_settings_row(session, row)
                    session.commit()
                    cls._cache = cls._row_to_cache(session, row)
                    cls._cache_initialized = True
            except Exception as exc:
                logger.error("Failed to initialize settings cache: %s", exc)
                cls._cache = {
                    "engine_config": cls._default_engine_config(),
                    "engine_settings": cls._default_engine_settings(),
                    "orchestrator_settings": cls._default_orchestrator_settings(),
                    "proxy_settings": cls._default_proxy_settings(),
                    "vpn_settings": {**cls._default_vpn_settings(), "credentials": []},
                }
                cls._cache_initialized = True

    @classmethod
    def has_persisted_runtime_settings(cls) -> bool:
        """Return True when runtime settings were already persisted in the database."""
        cls.initialize_cache()
        with cls._lock:
            defaults = {
                "engine_config": cls._default_engine_config(),
                "engine_settings": cls._default_engine_settings(),
                "orchestrator_settings": cls._default_orchestrator_settings(),
                "proxy_settings": cls._default_proxy_settings(),
                "vpn_settings": cls._default_vpn_settings(),
            }

            for category, default_val in defaults.items():
                cached = cls._cache.get(category) or {}
                if category == "vpn_settings":
                    cached_no_creds = {k: v for k, v in cached.items() if k != "credentials"}
                    if cached_no_creds != default_val or cached.get("credentials"):
                        return True
                elif cached != default_val:
                    return True
            return False

    @classmethod
    def _get_cached_category(cls, category: str) -> Dict[str, Any]:
        cls.initialize_cache()
        with cls._lock:
            return cls._deepcopy(cls._cache.get(category) or {})

    @classmethod
    def get_cached_setting(cls, category: str, key: str, default: Any = None) -> Any:
        """Fast in-memory lookup for hot paths that need a single setting value."""
        cls.initialize_cache()
        with cls._lock:
            category_payload = cls._cache.get(category) or {}
            return category_payload.get(key, default)

    @classmethod
    def _save_category(cls, category: str, payload: Dict[str, Any]) -> bool:
        cls.initialize_cache()

        with cls._lock:
            try:
                with get_session() as session:
                    row = cls._ensure_settings_row(session)

                    if category == "vpn_settings":
                        normalized = cls.normalize_vpn_config(payload)
                        credentials = cls._normalize_credentials(
                            payload.get("credentials") if isinstance(payload, dict) else [],
                            default_provider=normalized.get("provider"),
                            default_protocol=normalized.get("protocol"),
                        )
                        row.vpn_settings = {k: v for k, v in normalized.items() if k != "credentials"}
                        cls._upsert_credentials(session, row.id, credentials)
                        cls._cache["vpn_settings"] = {**normalized, "credentials": credentials}
                    elif category == "proxy_settings":
                        normalized_proxy = cls.normalize_proxy_config(payload)
                        row.proxy_settings = normalized_proxy
                        cls._cache[category] = normalized_proxy
                    else:
                        cleaned = cls._deepcopy(payload)
                        setattr(row, category, cleaned)
                        cls._cache[category] = cleaned

                    row.updated_at = datetime.now(timezone.utc)
                    session.add(row)
                    session.commit()
                    return True
            except Exception as exc:
                logger.error("Failed saving %s settings: %s", category, exc)
                return False

    @classmethod
    def load_all_settings(cls) -> Dict[str, Dict[str, Any]]:
        cls.initialize_cache()
        with cls._lock:
            return copy.deepcopy(cls._cache)

    @staticmethod
    def load_engine_config() -> Optional[Dict[str, Any]]:
        return SettingsPersistence._get_cached_category("engine_config")

    @staticmethod
    def save_engine_config(config: Dict[str, Any]) -> bool:
        return SettingsPersistence._save_category("engine_config", config)

    @staticmethod
    def load_proxy_config() -> Optional[Dict[str, Any]]:
        return SettingsPersistence._get_cached_category("proxy_settings")

    @staticmethod
    def save_proxy_config(config: Dict[str, Any]) -> bool:
        return SettingsPersistence._save_category("proxy_settings", config)



    @staticmethod
    def load_engine_settings() -> Optional[Dict[str, Any]]:
        return SettingsPersistence._get_cached_category("engine_settings")

    @staticmethod
    def save_engine_settings(config: Dict[str, Any]) -> bool:
        return SettingsPersistence._save_category("engine_settings", config)

    @staticmethod
    def load_orchestrator_config() -> Optional[Dict[str, Any]]:
        return SettingsPersistence._get_cached_category("orchestrator_settings")

    @staticmethod
    def save_orchestrator_config(config: Dict[str, Any]) -> bool:
        payload = dict(config or {})
        if "collect_interval_s" in payload:
            payload["collect_interval_s"] = 1
        return SettingsPersistence._save_category("orchestrator_settings", payload)

    @staticmethod
    def load_vpn_config() -> Optional[Dict[str, Any]]:
        return SettingsPersistence._get_cached_category("vpn_settings")

    @staticmethod
    def save_vpn_config(config: Dict[str, Any]) -> bool:
        return SettingsPersistence._save_category("vpn_settings", config)
