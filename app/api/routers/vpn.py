"""VPN endpoints."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...core.config import cfg
from ...api.auth import require_api_key
from ...vpn.vpn_credentials import credential_manager
from ...vpn.vpn_servers_refresh import vpn_servers_refresh_service
from ...vpn.gluetun import get_vpn_status
from ...persistence.cache import get_cache
from ...vpn.proton_updater import ProtonServerUpdater, ProtonFilterConfig
from ...models.schemas import VPNSettingsUpdate, VPNSettingsResponse
from ...utils.wireguard_parser import parse_wireguard_conf
from ...api.sse_helpers import _format_sse_message, _validate_sse_api_key

logger = logging.getLogger(__name__)

router = APIRouter()


class WireguardParseRequest(BaseModel):
    file_content: str


class ProtonServersRefreshRequest(BaseModel):
    proton_username: Optional[str] = None
    proton_password: Optional[str] = None
    proton_totp_code: Optional[str] = None
    proton_totp_secret: Optional[str] = None
    storage_path: Optional[str] = None
    gluetun_json_mode: str = "update"
    ipv6: str = "exclude"
    secure_core: str = "include"
    tor: str = "include"
    free_tier: str = "include"


class VPNServersRefreshRequest(BaseModel):
    source: Optional[str] = None
    gluetun_json_mode: Optional[str] = None
    reason: Optional[str] = None


class VPNCredentialUpsert(BaseModel):
    """Single VPN credential payload for dedicated credential lifecycle endpoints."""

    id: Optional[str] = None
    provider: Optional[str] = None
    protocol: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    openvpn_user: Optional[str] = None
    openvpn_password: Optional[str] = None
    private_key: Optional[str] = None
    wireguard_private_key: Optional[str] = None
    public_key: Optional[str] = None
    wireguard_public_key: Optional[str] = None
    preshared_key: Optional[str] = None
    wireguard_preshared_key: Optional[str] = None
    mtu: Optional[str] = None
    wireguard_mtu: Optional[str] = None
    addresses: Optional[str] = None
    wireguard_addresses: Optional[str] = None
    endpoint: Optional[str] = None
    endpoints: Optional[str] = None
    wireguard_endpoints: Optional[str] = None
    endpoint_ip: Optional[str] = None
    endpoint_port: Optional[str] = None
    source: Optional[str] = None
    port_forwarding: Optional[bool] = True
    # Aliases for parser compatibility
    PublicKey: Optional[str] = None
    PresharedKey: Optional[str] = None
    MTU: Optional[str] = None
    PrivateKey: Optional[str] = None
    Address: Optional[str] = None
    Endpoint: Optional[str] = None
    
    class Config:
        extra = "allow"


def _load_vpn_settings_for_credential_ops() -> Dict[str, Any]:
    from ...persistence.settings_persistence import SettingsPersistence

    return SettingsPersistence.load_vpn_config() or {
        "enabled": False,
        "dynamic_vpn_management": True,
        "preferred_engines_per_vpn": int(cfg.PREFERRED_ENGINES_PER_VPN),
        "protocol": str(cfg.VPN_PROTOCOL or "wireguard"),
        "provider": str(cfg.VPN_PROVIDER or "protonvpn"),
        "regions": [],
        "credentials": [],
        "api_port": cfg.GLUETUN_API_PORT,
        "health_check_interval_s": cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S,
        "port_cache_ttl_s": cfg.GLUETUN_PORT_CACHE_TTL_S,
        "restart_engines_on_reconnect": cfg.VPN_RESTART_ENGINES_ON_RECONNECT,
        "unhealthy_restart_timeout_s": cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S,
        "vpn_servers_auto_refresh": False,
        "vpn_servers_refresh_period_s": 86400,
        "vpn_servers_refresh_source": "gluetun_official",
        "vpn_servers_gluetun_json_mode": "update",
        "vpn_servers_storage_path": None,
        "vpn_servers_official_url": "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json",
        "vpn_servers_proton_credentials_source": "env",
        "vpn_servers_proton_username_env": "PROTON_USERNAME",
        "vpn_servers_proton_password_env": "PROTON_PASSWORD",
        "vpn_servers_proton_totp_code_env": "PROTON_TOTP_CODE",
        "vpn_servers_proton_totp_secret_env": "PROTON_TOTP_SECRET",
        "vpn_servers_proton_username": None,
        "vpn_servers_proton_password": None,
        "vpn_servers_proton_totp_code": None,
        "vpn_servers_proton_totp_secret": None,
        "vpn_servers_filter_ipv6": "exclude",
        "vpn_servers_filter_secure_core": "include",
        "vpn_servers_filter_tor": "include",
        "vpn_servers_filter_free_tier": "include",
    }


@router.get("/vpn/status")
async def get_vpn_status_endpoint():
    """Get VPN (Gluetun) status information with location data (cached for 0.5 seconds)."""
    cache = get_cache()
    cache_key = "vpn:status"

    cached_value = cache.get(cache_key)
    if cached_value is not None:
        return cached_value

    vpn_status = get_vpn_status()
    cache.set(cache_key, vpn_status, ttl=0.5)
    return vpn_status


@router.post("/vpn/parse-wireguard")
def parse_wireguard_config(payload: WireguardParseRequest):
    """Parse Wireguard .conf text and return key fields used by VPN credential forms."""
    parsed = parse_wireguard_conf(payload.file_content)
    if not parsed.get("is_valid"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_wireguard_conf",
                "message": "Unable to parse required Wireguard fields (PrivateKey, Address, Endpoint)",
                "parsed": parsed,
            },
        )
    return parsed


@router.post("/vpn/proton/refresh", dependencies=[Depends(require_api_key)])
async def refresh_proton_servers(payload: ProtonServersRefreshRequest):
    """Fetch Proton server catalog and update local Gluetun-compatible servers files."""
    updater = ProtonServerUpdater(storage_path=payload.storage_path)
    filters = ProtonFilterConfig(
        ipv6=payload.ipv6,
        secure_core=payload.secure_core,
        tor=payload.tor,
        free_tier=payload.free_tier,
    )

    try:
        result = await updater.update(
            proton_username=payload.proton_username,
            proton_password=payload.proton_password,
            proton_totp_code=payload.proton_totp_code,
            proton_totp_secret=payload.proton_totp_secret,
            filters=filters,
            gluetun_json_mode=payload.gluetun_json_mode,
        )
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/vpn/servers/refresh", dependencies=[Depends(require_api_key)])
async def refresh_vpn_servers(payload: Optional[VPNServersRefreshRequest] = None):
    """Refresh VPN provider servers list using current VPN settings or request overrides."""
    request_payload = payload or VPNServersRefreshRequest()
    overrides = {
        "vpn_servers_refresh_source": request_payload.source,
        "vpn_servers_gluetun_json_mode": request_payload.gluetun_json_mode,
    }

    try:
        result = await vpn_servers_refresh_service.refresh_now(
            reason=str(request_payload.reason or "manual").strip() or "manual",
            overrides=overrides,
        )
        if not result.get("ok", False):
            raise HTTPException(status_code=409, detail=result.get("detail") or "refresh already in progress")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/vpn/servers/refresh/status")
def get_vpn_servers_refresh_status():
    """Return current VPN servers refresh scheduler status and last result."""
    return vpn_servers_refresh_service.get_status()


@router.get("/vpn/leases")
async def get_vpn_credential_leases():
    """Return VPN credential pool lease status used by the settings dashboard."""
    return await credential_manager.summary()


@router.get("/vpn/publicip")
def get_vpn_publicip_endpoint():
    """Get effective public IP address (VPN egress when enabled, host egress otherwise)."""
    from ...vpn.gluetun import get_effective_public_ip

    public_ip = get_effective_public_ip()
    if public_ip:
        return {"public_ip": public_ip}
    else:
        raise HTTPException(status_code=503, detail="Unable to retrieve public IP")


@router.get("/vpn/config", response_model=VPNSettingsResponse)
def get_vpn_config_legacy_alias():
    """Legacy alias for VPN settings endpoint."""
    return get_vpn_settings()


@router.post("/vpn/config", dependencies=[Depends(require_api_key)])
async def update_vpn_config_legacy_alias(settings: VPNSettingsUpdate):
    """Legacy alias for VPN settings endpoint."""
    return await update_vpn_settings(settings)


@router.get("/settings/vpn", response_model=VPNSettingsResponse)
def get_vpn_settings():
    """Get current dynamic VPN configuration settings."""
    from ...persistence.settings_persistence import SettingsPersistence

    defaults: Dict[str, Any] = {
        "enabled": False,
        "dynamic_vpn_management": True,
        "preferred_engines_per_vpn": int(cfg.PREFERRED_ENGINES_PER_VPN),
        "protocol": str(cfg.VPN_PROTOCOL or "wireguard"),
        "provider": str(cfg.VPN_PROVIDER or "protonvpn"),
        "regions": [],
        "credentials": [],
        "api_port": cfg.GLUETUN_API_PORT,
        "health_check_interval_s": cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S,
        "port_cache_ttl_s": cfg.GLUETUN_PORT_CACHE_TTL_S,
        "restart_engines_on_reconnect": cfg.VPN_RESTART_ENGINES_ON_RECONNECT,
        "unhealthy_restart_timeout_s": cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S,
        "vpn_servers_auto_refresh": False,
        "vpn_servers_refresh_period_s": 86400,
        "vpn_servers_refresh_source": "gluetun_official",
        "vpn_servers_gluetun_json_mode": "update",
        "vpn_servers_storage_path": None,
        "vpn_servers_official_url": "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json",
        "vpn_servers_proton_credentials_source": "env",
        "vpn_servers_proton_username_env": "PROTON_USERNAME",
        "vpn_servers_proton_password_env": "PROTON_PASSWORD",
        "vpn_servers_proton_totp_code_env": "PROTON_TOTP_CODE",
        "vpn_servers_proton_totp_secret_env": "PROTON_TOTP_SECRET",
        "vpn_servers_proton_username": None,
        "vpn_servers_proton_password": None,
        "vpn_servers_proton_totp_code": None,
        "vpn_servers_proton_totp_secret": None,
        "vpn_servers_filter_ipv6": "exclude",
        "vpn_servers_filter_secure_core": "include",
        "vpn_servers_filter_tor": "include",
        "vpn_servers_filter_free_tier": "include",
    }

    persisted = SettingsPersistence.load_vpn_config()
    if persisted:
        merged = {**defaults, **persisted}

        legacy_providers = merged.pop("providers", None)
        if not merged.get("provider") and isinstance(legacy_providers, list) and legacy_providers:
            merged["provider"] = str(legacy_providers[0]).strip()

        try:
            merged["preferred_engines_per_vpn"] = max(1, int(merged.get("preferred_engines_per_vpn", 10)))
        except Exception:
            merged["preferred_engines_per_vpn"] = 10

        merged["dynamic_vpn_management"] = True
        merged["provider"] = str(merged.get("provider") or "").strip().lower() or defaults["provider"]
        merged["protocol"] = str(merged.get("protocol") or "").strip().lower() or defaults["protocol"]

        if not isinstance(merged.get("regions"), list):
            merged["regions"] = []
        if not isinstance(merged.get("credentials"), list):
            merged["credentials"] = []

        merged["vpn_servers_auto_refresh"] = bool(merged.get("vpn_servers_auto_refresh", False))
        try:
            merged["vpn_servers_refresh_period_s"] = max(60, int(merged.get("vpn_servers_refresh_period_s", 86400)))
        except Exception:
            merged["vpn_servers_refresh_period_s"] = 86400

        refresh_source = str(merged.get("vpn_servers_refresh_source") or "gluetun_official").strip().lower()
        if refresh_source not in {"proton_paid", "gluetun_official"}:
            refresh_source = "gluetun_official"
        merged["vpn_servers_refresh_source"] = refresh_source

        json_mode = str(merged.get("vpn_servers_gluetun_json_mode") or "update").strip().lower()
        if json_mode not in {"none", "replace", "update"}:
            json_mode = "update"
        merged["vpn_servers_gluetun_json_mode"] = json_mode

        credentials_source = str(merged.get("vpn_servers_proton_credentials_source") or "env").strip().lower()
        if credentials_source not in {"env", "settings"}:
            credentials_source = "env"
        merged["vpn_servers_proton_credentials_source"] = credentials_source

        for env_key, fallback in (
            ("vpn_servers_proton_username_env", "PROTON_USERNAME"),
            ("vpn_servers_proton_password_env", "PROTON_PASSWORD"),
            ("vpn_servers_proton_totp_code_env", "PROTON_TOTP_CODE"),
            ("vpn_servers_proton_totp_secret_env", "PROTON_TOTP_SECRET"),
        ):
            merged[env_key] = str(merged.get(env_key) or fallback).strip() or fallback

        merged["vpn_servers_storage_path"] = str(merged.get("vpn_servers_storage_path") or "").strip() or None
        merged["vpn_servers_official_url"] = (
            str(merged.get("vpn_servers_official_url") or defaults["vpn_servers_official_url"]).strip()
            or defaults["vpn_servers_official_url"]
        )

        for secret_key in (
            "vpn_servers_proton_username",
            "vpn_servers_proton_password",
            "vpn_servers_proton_totp_code",
            "vpn_servers_proton_totp_secret",
        ):
            merged[secret_key] = str(merged.get(secret_key) or "").strip() or None

        for filter_key, fallback in (
            ("vpn_servers_filter_ipv6", "exclude"),
            ("vpn_servers_filter_secure_core", "include"),
            ("vpn_servers_filter_tor", "include"),
            ("vpn_servers_filter_free_tier", "include"),
        ):
            value = str(merged.get(filter_key) or fallback).strip().lower()
            merged[filter_key] = value if value in {"include", "exclude", "only"} else fallback
        return merged

    try:
        SettingsPersistence.save_vpn_config(defaults)
    except Exception:
        pass
    return defaults


@router.post("/settings/vpn", dependencies=[Depends(require_api_key)])
async def update_vpn_settings(settings: VPNSettingsUpdate):
    """Update dynamic VPN configuration settings at runtime and persist."""
    from ...persistence.settings_persistence import SettingsPersistence
    from ...vpn.vpn_controller import vpn_controller
    from ...control_plane.autoscaler import engine_controller

    current: Dict[str, Any] = SettingsPersistence.load_vpn_config() or {
        "enabled": False,
        "dynamic_vpn_management": True,
        "preferred_engines_per_vpn": int(cfg.PREFERRED_ENGINES_PER_VPN),
        "protocol": str(cfg.VPN_PROTOCOL or "wireguard"),
        "provider": str(cfg.VPN_PROVIDER or "protonvpn"),
        "regions": [],
        "credentials": [],
        "api_port": cfg.GLUETUN_API_PORT,
        "health_check_interval_s": cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S,
        "port_cache_ttl_s": cfg.GLUETUN_PORT_CACHE_TTL_S,
        "restart_engines_on_reconnect": cfg.VPN_RESTART_ENGINES_ON_RECONNECT,
        "unhealthy_restart_timeout_s": cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S,
        "vpn_servers_auto_refresh": False,
        "vpn_servers_refresh_period_s": 86400,
        "vpn_servers_refresh_source": "gluetun_official",
        "vpn_servers_gluetun_json_mode": "update",
        "vpn_servers_storage_path": None,
        "vpn_servers_official_url": "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json",
        "vpn_servers_proton_credentials_source": "env",
        "vpn_servers_proton_username_env": "PROTON_USERNAME",
        "vpn_servers_proton_password_env": "PROTON_PASSWORD",
        "vpn_servers_proton_totp_code_env": "PROTON_TOTP_CODE",
        "vpn_servers_proton_totp_secret_env": "PROTON_TOTP_SECRET",
        "vpn_servers_proton_username": None,
        "vpn_servers_proton_password": None,
        "vpn_servers_proton_totp_code": None,
        "vpn_servers_proton_totp_secret": None,
        "vpn_servers_filter_ipv6": "exclude",
        "vpn_servers_filter_secure_core": "include",
        "vpn_servers_filter_tor": "include",
        "vpn_servers_filter_free_tier": "include",
    }

    previously_enabled = bool(current.get("enabled", False))
    from ...services.state import state

    for legacy_key in ("vpn_mode", "container_name", "container_name_2", "port_range_1", "port_range_2"):
        current.pop(legacy_key, None)

    legacy_providers = current.pop("providers", None)
    if not current.get("provider") and isinstance(legacy_providers, list) and legacy_providers:
        current["provider"] = str(legacy_providers[0]).strip()

    if settings.enabled is not None:
        current["enabled"] = bool(settings.enabled)

    if settings.api_port is not None:
        if not (1 <= settings.api_port <= 65535):
            raise HTTPException(status_code=400, detail="api_port must be 1-65535")
        current["api_port"] = settings.api_port
        cfg.GLUETUN_API_PORT = settings.api_port

    if settings.health_check_interval_s is not None:
        if settings.health_check_interval_s < 1:
            raise HTTPException(status_code=400, detail="health_check_interval_s must be >= 1")
        current["health_check_interval_s"] = settings.health_check_interval_s
        cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S = settings.health_check_interval_s

    if settings.port_cache_ttl_s is not None:
        if settings.port_cache_ttl_s < 1:
            raise HTTPException(status_code=400, detail="port_cache_ttl_s must be >= 1")
        current["port_cache_ttl_s"] = settings.port_cache_ttl_s
        cfg.GLUETUN_PORT_CACHE_TTL_S = settings.port_cache_ttl_s

    if settings.restart_engines_on_reconnect is not None:
        current["restart_engines_on_reconnect"] = settings.restart_engines_on_reconnect
        cfg.VPN_RESTART_ENGINES_ON_RECONNECT = settings.restart_engines_on_reconnect

    if settings.unhealthy_restart_timeout_s is not None:
        if settings.unhealthy_restart_timeout_s < 1:
            raise HTTPException(status_code=400, detail="unhealthy_restart_timeout_s must be >= 1")
        current["unhealthy_restart_timeout_s"] = settings.unhealthy_restart_timeout_s
        cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S = settings.unhealthy_restart_timeout_s

    def _valid_env_name(name: str) -> bool:
        if not name:
            return False
        if not (name[0].isalpha() or name[0] == "_"):
            return False
        return all(ch.isalnum() or ch == "_" for ch in name)

    if settings.vpn_servers_auto_refresh is not None:
        current["vpn_servers_auto_refresh"] = bool(settings.vpn_servers_auto_refresh)

    if settings.vpn_servers_refresh_period_s is not None:
        if settings.vpn_servers_refresh_period_s < 60:
            raise HTTPException(status_code=400, detail="vpn_servers_refresh_period_s must be >= 60")
        current["vpn_servers_refresh_period_s"] = int(settings.vpn_servers_refresh_period_s)

    if settings.vpn_servers_refresh_source is not None:
        source = str(settings.vpn_servers_refresh_source).strip().lower()
        if source not in {"proton_paid", "gluetun_official"}:
            raise HTTPException(
                status_code=400,
                detail="vpn_servers_refresh_source must be 'proton_paid' or 'gluetun_official'",
            )
        current["vpn_servers_refresh_source"] = source

    if settings.vpn_servers_gluetun_json_mode is not None:
        json_mode = str(settings.vpn_servers_gluetun_json_mode).strip().lower()
        if json_mode not in {"none", "replace", "update"}:
            raise HTTPException(
                status_code=400,
                detail="vpn_servers_gluetun_json_mode must be 'none', 'replace', or 'update'",
            )
        current["vpn_servers_gluetun_json_mode"] = json_mode

    if settings.vpn_servers_storage_path is not None:
        current["vpn_servers_storage_path"] = str(settings.vpn_servers_storage_path or "").strip() or None

    if settings.vpn_servers_official_url is not None:
        official_url = str(settings.vpn_servers_official_url or "").strip()
        if not official_url:
            raise HTTPException(status_code=400, detail="vpn_servers_official_url cannot be empty")
        if not (official_url.startswith("http://") or official_url.startswith("https://")):
            raise HTTPException(
                status_code=400, detail="vpn_servers_official_url must start with http:// or https://"
            )
        current["vpn_servers_official_url"] = official_url

    if settings.vpn_servers_proton_credentials_source is not None:
        credentials_source = str(settings.vpn_servers_proton_credentials_source).strip().lower()
        if credentials_source not in {"env", "settings"}:
            raise HTTPException(
                status_code=400,
                detail="vpn_servers_proton_credentials_source must be 'env' or 'settings'",
            )
        current["vpn_servers_proton_credentials_source"] = credentials_source

    for env_key in (
        "vpn_servers_proton_username_env",
        "vpn_servers_proton_password_env",
        "vpn_servers_proton_totp_code_env",
        "vpn_servers_proton_totp_secret_env",
    ):
        value = getattr(settings, env_key)
        if value is None:
            continue
        normalized = str(value).strip()
        if not _valid_env_name(normalized):
            raise HTTPException(
                status_code=400, detail=f"{env_key} must be a valid environment variable name"
            )
        current[env_key] = normalized

    for key in (
        "vpn_servers_proton_username",
        "vpn_servers_proton_password",
        "vpn_servers_proton_totp_code",
        "vpn_servers_proton_totp_secret",
    ):
        value = getattr(settings, key)
        if value is not None:
            current[key] = str(value).strip() or None

    for filter_key in (
        "vpn_servers_filter_ipv6",
        "vpn_servers_filter_secure_core",
        "vpn_servers_filter_tor",
        "vpn_servers_filter_free_tier",
    ):
        value = getattr(settings, filter_key)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized not in {"include", "exclude", "only"}:
            raise HTTPException(
                status_code=400, detail=f"{filter_key} must be one of: include|exclude|only"
            )
        current[filter_key] = normalized

    current["dynamic_vpn_management"] = True

    if settings.preferred_engines_per_vpn is not None:
        if settings.preferred_engines_per_vpn < 1:
            raise HTTPException(status_code=400, detail="preferred_engines_per_vpn must be >= 1")
        current["preferred_engines_per_vpn"] = int(settings.preferred_engines_per_vpn)
        cfg.PREFERRED_ENGINES_PER_VPN = int(settings.preferred_engines_per_vpn)

    if settings.provider is not None:
        provider = str(settings.provider).strip().lower()
        current["provider"] = provider
        if provider:
            cfg.VPN_PROVIDER = provider

    if settings.protocol is not None:
        protocol = str(settings.protocol).strip().lower()
        if protocol and protocol not in ("wireguard", "openvpn"):
            raise HTTPException(status_code=400, detail="protocol must be 'wireguard' or 'openvpn'")
        current["protocol"] = protocol
        if protocol:
            cfg.VPN_PROTOCOL = protocol

    if settings.regions is not None:
        if isinstance(settings.regions, str):
            current["regions"] = [
                str(region).strip()
                for region in settings.regions.split(",")
                if str(region).strip()
            ]
        else:
            current["regions"] = [str(region).strip() for region in settings.regions if str(region).strip()]

    if settings.credentials is not None:
        if not all(isinstance(credential, dict) for credential in settings.credentials):
            raise HTTPException(status_code=400, detail="credentials must be a list of JSON objects")
        current["credentials"] = credential_manager.normalize_credentials_for_storage(settings.credentials)

    cfg.DYNAMIC_VPN_MANAGEMENT = True

    provider_value = str(current.get("provider") or "").strip().lower()
    providers = [provider_value] if provider_value else []

    lease_summary = await credential_manager.configure(
        dynamic_vpn_management=True,
        providers=providers,
        protocol=current.get("protocol"),
        regions=current.get("regions", []),
        credentials=current.get("credentials", []),
    )
    logger.info(
        "VPN credential manager updated: dynamic=%s max_vpn_capacity=%s available=%s leased=%s",
        lease_summary.get("dynamic_vpn_management"),
        lease_summary.get("max_vpn_capacity"),
        lease_summary.get("available"),
        lease_summary.get("leased"),
    )

    dynamic_enabled = bool(current.get("enabled", False))
    if dynamic_enabled:
        if not vpn_controller.is_running():
            await vpn_controller.start()
            logger.info("Dynamic VPN controller started after VPN settings update")
    else:
        state.set_desired_vpn_node_count(0)
        if not vpn_controller.is_running():
            await vpn_controller.start()
            logger.info("Dynamic VPN controller started for disable cleanup")
        vpn_controller.request_reconcile(reason="vpn_disabled_cleanup")
        logger.info("Dynamic VPN controller reconcile requested for disable cleanup")

    migration_marked = 0
    migration_requested = bool(getattr(settings, "trigger_migration", False))
    migration_should_run = migration_requested and dynamic_enabled != previously_enabled
    if migration_should_run:
        target_vpn_enabled = dynamic_enabled
        for engine_state in state.list_engines():
            engine_is_vpn_bound = bool(engine_state.vpn_container)
            if engine_is_vpn_bound == target_vpn_enabled:
                continue

            container_id = str(engine_state.container_id or "").strip()
            if not container_id:
                continue

            if state.mark_engine_draining(container_id, reason="vpn_enable_migration"):
                migration_marked += 1

        logger.info(
            "Graceful VPN migration requested: marked_non_vpn_engines_draining=%s",
            migration_marked,
        )
        if migration_marked > 0:
            engine_controller.request_reconcile(
                reason=f"vpn_migration_toggle:{'enabled' if target_vpn_enabled else 'disabled'}"
            )

    if SettingsPersistence.save_vpn_config(current):
        logger.info("VPN settings persisted")
    else:
        logger.warning("Failed to persist VPN settings")

    return {
        "message": "VPN settings updated and persisted",
        "migration_requested": migration_requested,
        "migration_marked_engines": migration_marked,
        **current,
    }


@router.post("/settings/vpn/credentials", dependencies=[Depends(require_api_key)])
async def add_vpn_credential(credential: VPNCredentialUpsert):
    """Add a single VPN credential and persist immediately."""
    from ...persistence.settings_persistence import SettingsPersistence

    current = _load_vpn_settings_for_credential_ops()
    credentials = list(current.get("credentials") or [])

    payload = credential.model_dump(exclude_none=True)
    payload["id"] = str(payload.get("id") or uuid4())

    provider = str(
        payload.get("provider") or current.get("provider") or cfg.VPN_PROVIDER or ""
    ).strip().lower()
    if provider:
        payload["provider"] = provider

    protocol = str(
        payload.get("protocol") or current.get("protocol") or cfg.VPN_PROTOCOL or "wireguard"
    ).strip().lower()
    if protocol not in ("wireguard", "openvpn"):
        raise HTTPException(status_code=400, detail="protocol must be 'wireguard' or 'openvpn'")
    payload["protocol"] = protocol

    credentials.append(payload)
    current["credentials"] = credential_manager.normalize_credentials_for_storage(credentials)

    provider_value = str(current.get("provider") or "").strip().lower()
    providers = [provider_value] if provider_value else []
    await credential_manager.configure(
        dynamic_vpn_management=True,
        providers=providers,
        protocol=current.get("protocol"),
        regions=current.get("regions", []),
        credentials=current.get("credentials", []),
    )

    if not SettingsPersistence.save_vpn_config(current):
        raise HTTPException(status_code=500, detail="failed to persist vpn credential")

    return {
        "message": "VPN credential added",
        "credential": payload,
        "credentials_count": len(current.get("credentials", [])),
    }


@router.delete("/settings/vpn/credentials/{credential_id}", dependencies=[Depends(require_api_key)])
async def delete_vpn_credential(credential_id: str):
    """Delete a VPN credential by ID and persist immediately."""
    from ...persistence.settings_persistence import SettingsPersistence

    current = _load_vpn_settings_for_credential_ops()
    credentials = list(current.get("credentials") or [])
    remaining = [c for c in credentials if str(c.get("id") or "") != credential_id]

    if len(remaining) == len(credentials):
        raise HTTPException(status_code=404, detail="credential not found")

    current["credentials"] = credential_manager.normalize_credentials_for_storage(remaining)

    provider_value = str(current.get("provider") or "").strip().lower()
    providers = [provider_value] if provider_value else []
    await credential_manager.configure(
        dynamic_vpn_management=True,
        providers=providers,
        protocol=current.get("protocol"),
        regions=current.get("regions", []),
        credentials=current.get("credentials", []),
    )

    if not SettingsPersistence.save_vpn_config(current):
        raise HTTPException(status_code=500, detail="failed to persist vpn credential changes")

    return {
        "message": "VPN credential deleted",
        "credentials_count": len(current.get("credentials", [])),
    }


@router.get("/api/v1/vpn/leases/stream")
async def stream_vpn_lease_summary(
    request: Request,
    interval_seconds: float = Query(5.0, ge=1.0, le=30.0),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for VPN credential lease summary updates."""
    _validate_sse_api_key(request, api_key)

    async def _event_generator():
        last_digest: Optional[str] = None
        while True:
            if await request.is_disconnected():
                break

            try:
                payload = jsonable_encoder(await credential_manager.summary())
                digest = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)

                if digest != last_digest:
                    last_digest = digest
                    message = {
                        "type": "vpn_leases_snapshot",
                        "payload": payload,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    yield _format_sse_message(message, event_name="vpn_leases_snapshot")
                else:
                    yield ": keep-alive\n\n"
            except Exception as exc:
                message = {
                    "type": "vpn_leases_error",
                    "payload": {
                        "detail": f"vpn_lease_summary_error: {exc}",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="vpn_leases_error")

            await asyncio.sleep(interval_seconds)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)
