"""Settings endpoints (engine, orchestrator, proxy, import/export)."""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...core.config import cfg
from ...api.auth import require_api_key
from ...control_plane.autoscaler import engine_controller
from ...services.state import state
from ...models.schemas import EngineState

logger = logging.getLogger(__name__)

router = APIRouter()

# Backup format version - increment when backup structure changes
BACKUP_FORMAT_VERSION = "2.0"


class OrchestratorSettingsUpdate(BaseModel):
    """Model for updating orchestrator core settings."""
    monitor_interval_s: Optional[int] = None
    engine_grace_period_s: Optional[int] = None
    autoscale_interval_s: Optional[int] = None
    startup_timeout_s: Optional[int] = None
    idle_ttl_s: Optional[int] = None
    collect_interval_s: Optional[int] = None
    stats_history_max: Optional[int] = None
    health_check_interval_s: Optional[int] = None
    health_failure_threshold: Optional[int] = None
    health_unhealthy_grace_period_s: Optional[int] = None
    health_replacement_cooldown_s: Optional[int] = None
    circuit_breaker_failure_threshold: Optional[int] = None
    circuit_breaker_recovery_timeout_s: Optional[int] = None
    circuit_breaker_replacement_threshold: Optional[int] = None
    circuit_breaker_replacement_timeout_s: Optional[int] = None
    max_concurrent_provisions: Optional[int] = None
    min_provision_interval_s: Optional[float] = None
    port_range_host: Optional[str] = None
    ace_http_range: Optional[str] = None
    ace_https_range: Optional[str] = None
    debug_mode: Optional[bool] = None


class EngineSettingsUpdate(BaseModel):
    """Model for updating engine settings."""
    min_replicas: Optional[int] = None
    max_replicas: Optional[int] = None
    auto_delete: Optional[bool] = None
    manual_mode: Optional[bool] = None
    manual_engines: Optional[List[Dict[str, Any]]] = None

    total_max_download_rate: Optional[int] = None
    total_max_upload_rate: Optional[int] = None
    live_cache_type: Optional[str] = None
    buffer_time: Optional[int] = None
    max_peers: Optional[int] = None
    memory_limit: Optional[str] = None
    parameters: Optional[List[Dict[str, Any]]] = None
    torrent_folder_mount_enabled: Optional[bool] = None
    torrent_folder_host_path: Optional[str] = None
    torrent_folder_container_path: Optional[str] = None
    disk_cache_mount_enabled: Optional[bool] = None
    disk_cache_prune_enabled: Optional[bool] = None
    disk_cache_prune_interval: Optional[int] = None


class SettingsBundleUpdate(BaseModel):
    engine_config: Optional[Dict[str, Any]] = None
    engine_settings: Optional[Dict[str, Any]] = None
    orchestrator_settings: Optional[Dict[str, Any]] = None
    proxy_settings: Optional[Dict[str, Any]] = None
    vpn_settings: Optional[Dict[str, Any]] = None


@router.get("/settings/engine/config")
def get_engine_config_endpoint():
    """Get the single global engine customization payload."""
    from ...infrastructure.engine_config import (
        EngineConfig,
        detect_platform,
        get_config as get_engine_config,
        resolve_engine_image,
    )

    engine_config = get_engine_config()
    if not engine_config:
        raise HTTPException(status_code=500, detail="Failed to load engine configuration")

    platform_arch = detect_platform()
    return {
        **engine_config.model_dump(mode="json"),
        "platform": platform_arch,
        "image": resolve_engine_image(platform_arch),
    }


@router.post("/settings/engine/config", dependencies=[Depends(require_api_key)])
def update_engine_config_endpoint(config):
    """Update the global engine customization payload."""
    from ...infrastructure.engine_config import (
        RESTRICTED_FLAGS,
        EngineConfig,
        reload_config as reload_engine_config,
        save_config as save_engine_config,
    )
    from ..routers.provisioning import _trigger_engine_generation_rollout

    for parameter in config.parameters:
        if parameter.enabled and parameter.name in RESTRICTED_FLAGS:
            raise HTTPException(
                status_code=400,
                detail=f"restricted parameter '{parameter.name}' is managed by the orchestrator",
            )

    if not save_engine_config(config):
        raise HTTPException(status_code=500, detail="Failed to save engine configuration")

    reload_engine_config()
    rollout = _trigger_engine_generation_rollout(reason="engine_config_update")
    return {
        "message": "Engine configuration saved successfully",
        "config": config.model_dump(mode="json"),
        "rolling_update": {
            "changed": bool(rollout.get("changed")),
            "target_generation": rollout.get("generation"),
            "target_hash": rollout.get("config_hash"),
        },
    }


@router.get("/custom-variant/config")
def get_custom_variant_config():
    return get_engine_config_endpoint()


@router.post("/custom-variant/config", dependencies=[Depends(require_api_key)])
def update_custom_variant_config(config):
    return update_engine_config_endpoint(config)


@router.get("/engine/config")
def get_engine_config_legacy_alias():
    """Legacy alias for engine config endpoint."""
    return get_engine_config_endpoint()


@router.post("/engine/config", dependencies=[Depends(require_api_key)])
def update_engine_config_legacy_alias(config):
    """Legacy alias for engine config endpoint."""
    return update_engine_config_endpoint(config)


@router.get("/settings/orchestrator")
def get_orchestrator_settings():
    """Get current orchestrator core configuration settings."""
    from ...persistence.settings_persistence import SettingsPersistence

    defaults = {
        "monitor_interval_s": cfg.MONITOR_INTERVAL_S,
        "engine_grace_period_s": cfg.ENGINE_GRACE_PERIOD_S,
        "autoscale_interval_s": cfg.AUTOSCALE_INTERVAL_S,
        "startup_timeout_s": cfg.STARTUP_TIMEOUT_S,
        "idle_ttl_s": cfg.IDLE_TTL_S,
        "collect_interval_s": cfg.COLLECT_INTERVAL_S,
        "stats_history_max": cfg.STATS_HISTORY_MAX,
        "health_check_interval_s": cfg.HEALTH_CHECK_INTERVAL_S,
        "health_failure_threshold": cfg.HEALTH_FAILURE_THRESHOLD,
        "health_unhealthy_grace_period_s": cfg.HEALTH_UNHEALTHY_GRACE_PERIOD_S,
        "health_replacement_cooldown_s": cfg.HEALTH_REPLACEMENT_COOLDOWN_S,
        "circuit_breaker_failure_threshold": cfg.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        "circuit_breaker_recovery_timeout_s": cfg.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S,
        "circuit_breaker_replacement_threshold": cfg.CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD,
        "circuit_breaker_replacement_timeout_s": cfg.CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S,
        "max_concurrent_provisions": cfg.MAX_CONCURRENT_PROVISIONS,
        "min_provision_interval_s": cfg.MIN_PROVISION_INTERVAL_S,
        "port_range_host": cfg.PORT_RANGE_HOST,
        "ace_http_range": cfg.ACE_HTTP_RANGE,
        "ace_https_range": cfg.ACE_HTTPS_RANGE,
        "debug_mode": cfg.DEBUG_MODE,
    }

    persisted = SettingsPersistence.load_orchestrator_config()
    if persisted:
        merged = {**defaults, **persisted}
        merged["collect_interval_s"] = 1
        merged.pop("ace_live_edge_delay", None)
        return merged

    try:
        SettingsPersistence.save_orchestrator_config(defaults)
    except Exception:
        pass
    return defaults


@router.post("/settings/orchestrator", dependencies=[Depends(require_api_key)])
async def update_orchestrator_settings(settings: OrchestratorSettingsUpdate):
    """Update orchestrator core configuration settings at runtime and persist."""
    from ...persistence.settings_persistence import SettingsPersistence

    current = SettingsPersistence.load_orchestrator_config() or {
        "monitor_interval_s": cfg.MONITOR_INTERVAL_S,
        "engine_grace_period_s": cfg.ENGINE_GRACE_PERIOD_S,
        "autoscale_interval_s": cfg.AUTOSCALE_INTERVAL_S,
        "startup_timeout_s": cfg.STARTUP_TIMEOUT_S,
        "idle_ttl_s": cfg.IDLE_TTL_S,
        "collect_interval_s": cfg.COLLECT_INTERVAL_S,
        "stats_history_max": cfg.STATS_HISTORY_MAX,
        "health_check_interval_s": cfg.HEALTH_CHECK_INTERVAL_S,
        "health_failure_threshold": cfg.HEALTH_FAILURE_THRESHOLD,
        "health_unhealthy_grace_period_s": cfg.HEALTH_UNHEALTHY_GRACE_PERIOD_S,
        "health_replacement_cooldown_s": cfg.HEALTH_REPLACEMENT_COOLDOWN_S,
        "circuit_breaker_failure_threshold": cfg.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        "circuit_breaker_recovery_timeout_s": cfg.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S,
        "circuit_breaker_replacement_threshold": cfg.CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD,
        "circuit_breaker_replacement_timeout_s": cfg.CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S,
        "max_concurrent_provisions": cfg.MAX_CONCURRENT_PROVISIONS,
        "min_provision_interval_s": cfg.MIN_PROVISION_INTERVAL_S,
        "port_range_host": cfg.PORT_RANGE_HOST,
        "ace_http_range": cfg.ACE_HTTP_RANGE,
        "ace_https_range": cfg.ACE_HTTPS_RANGE,
        "debug_mode": cfg.DEBUG_MODE,
    }
    current.pop("ace_live_edge_delay", None)
    current["collect_interval_s"] = 1

    def _validate_port_range(v: str, field: str):
        try:
            start, end = v.split("-")
            s, e = int(start), int(end)
            if not (1 <= s <= 65535 and 1 <= e <= 65535):
                raise HTTPException(status_code=400, detail=f"{field}: ports must be 1-65535")
            if s > e:
                raise HTTPException(status_code=400, detail=f"{field}: start must be <= end")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{field}: expected 'start-end' format")

    if settings.monitor_interval_s is not None:
        if settings.monitor_interval_s < 1:
            raise HTTPException(status_code=400, detail="monitor_interval_s must be >= 1")
        current["monitor_interval_s"] = settings.monitor_interval_s
        cfg.MONITOR_INTERVAL_S = settings.monitor_interval_s

    if settings.engine_grace_period_s is not None:
        if settings.engine_grace_period_s < 1:
            raise HTTPException(status_code=400, detail="engine_grace_period_s must be >= 1")
        current["engine_grace_period_s"] = settings.engine_grace_period_s
        cfg.ENGINE_GRACE_PERIOD_S = settings.engine_grace_period_s

    if settings.autoscale_interval_s is not None:
        if settings.autoscale_interval_s < 1:
            raise HTTPException(status_code=400, detail="autoscale_interval_s must be >= 1")
        current["autoscale_interval_s"] = settings.autoscale_interval_s
        cfg.AUTOSCALE_INTERVAL_S = settings.autoscale_interval_s

    if settings.startup_timeout_s is not None:
        if settings.startup_timeout_s < 1:
            raise HTTPException(status_code=400, detail="startup_timeout_s must be >= 1")
        current["startup_timeout_s"] = settings.startup_timeout_s
        cfg.STARTUP_TIMEOUT_S = settings.startup_timeout_s

    if settings.idle_ttl_s is not None:
        if settings.idle_ttl_s < 1:
            raise HTTPException(status_code=400, detail="idle_ttl_s must be >= 1")
        current["idle_ttl_s"] = settings.idle_ttl_s
        cfg.IDLE_TTL_S = settings.idle_ttl_s

    if settings.collect_interval_s is not None:
        if settings.collect_interval_s < 1:
            raise HTTPException(status_code=400, detail="collect_interval_s must be >= 1")
        current["collect_interval_s"] = 1
        cfg.COLLECT_INTERVAL_S = 1

    if settings.stats_history_max is not None:
        if settings.stats_history_max < 1:
            raise HTTPException(status_code=400, detail="stats_history_max must be >= 1")
        current["stats_history_max"] = settings.stats_history_max
        cfg.STATS_HISTORY_MAX = settings.stats_history_max

    if settings.health_check_interval_s is not None:
        if settings.health_check_interval_s < 1:
            raise HTTPException(status_code=400, detail="health_check_interval_s must be >= 1")
        current["health_check_interval_s"] = settings.health_check_interval_s
        cfg.HEALTH_CHECK_INTERVAL_S = settings.health_check_interval_s

    if settings.health_failure_threshold is not None:
        if settings.health_failure_threshold < 1:
            raise HTTPException(status_code=400, detail="health_failure_threshold must be >= 1")
        current["health_failure_threshold"] = settings.health_failure_threshold
        cfg.HEALTH_FAILURE_THRESHOLD = settings.health_failure_threshold

    if settings.health_unhealthy_grace_period_s is not None:
        if settings.health_unhealthy_grace_period_s < 1:
            raise HTTPException(status_code=400, detail="health_unhealthy_grace_period_s must be >= 1")
        current["health_unhealthy_grace_period_s"] = settings.health_unhealthy_grace_period_s
        cfg.HEALTH_UNHEALTHY_GRACE_PERIOD_S = settings.health_unhealthy_grace_period_s

    if settings.health_replacement_cooldown_s is not None:
        if settings.health_replacement_cooldown_s < 1:
            raise HTTPException(status_code=400, detail="health_replacement_cooldown_s must be >= 1")
        current["health_replacement_cooldown_s"] = settings.health_replacement_cooldown_s
        cfg.HEALTH_REPLACEMENT_COOLDOWN_S = settings.health_replacement_cooldown_s

    if settings.circuit_breaker_failure_threshold is not None:
        if settings.circuit_breaker_failure_threshold < 1:
            raise HTTPException(status_code=400, detail="circuit_breaker_failure_threshold must be >= 1")
        current["circuit_breaker_failure_threshold"] = settings.circuit_breaker_failure_threshold
        cfg.CIRCUIT_BREAKER_FAILURE_THRESHOLD = settings.circuit_breaker_failure_threshold

    if settings.circuit_breaker_recovery_timeout_s is not None:
        if settings.circuit_breaker_recovery_timeout_s < 1:
            raise HTTPException(status_code=400, detail="circuit_breaker_recovery_timeout_s must be >= 1")
        current["circuit_breaker_recovery_timeout_s"] = settings.circuit_breaker_recovery_timeout_s
        cfg.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S = settings.circuit_breaker_recovery_timeout_s

    if settings.circuit_breaker_replacement_threshold is not None:
        if settings.circuit_breaker_replacement_threshold < 1:
            raise HTTPException(
                status_code=400, detail="circuit_breaker_replacement_threshold must be >= 1"
            )
        current["circuit_breaker_replacement_threshold"] = settings.circuit_breaker_replacement_threshold
        cfg.CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD = settings.circuit_breaker_replacement_threshold

    if settings.circuit_breaker_replacement_timeout_s is not None:
        if settings.circuit_breaker_replacement_timeout_s < 1:
            raise HTTPException(
                status_code=400, detail="circuit_breaker_replacement_timeout_s must be >= 1"
            )
        current["circuit_breaker_replacement_timeout_s"] = settings.circuit_breaker_replacement_timeout_s
        cfg.CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S = settings.circuit_breaker_replacement_timeout_s

    if settings.max_concurrent_provisions is not None:
        if settings.max_concurrent_provisions < 1:
            raise HTTPException(status_code=400, detail="max_concurrent_provisions must be >= 1")
        current["max_concurrent_provisions"] = settings.max_concurrent_provisions
        cfg.MAX_CONCURRENT_PROVISIONS = settings.max_concurrent_provisions

    if settings.min_provision_interval_s is not None:
        if settings.min_provision_interval_s < 0:
            raise HTTPException(status_code=400, detail="min_provision_interval_s must be >= 0")
        current["min_provision_interval_s"] = settings.min_provision_interval_s
        cfg.MIN_PROVISION_INTERVAL_S = settings.min_provision_interval_s

    if settings.port_range_host is not None:
        _validate_port_range(settings.port_range_host, "port_range_host")
        current["port_range_host"] = settings.port_range_host
        cfg.PORT_RANGE_HOST = settings.port_range_host

    if settings.ace_http_range is not None:
        _validate_port_range(settings.ace_http_range, "ace_http_range")
        current["ace_http_range"] = settings.ace_http_range
        cfg.ACE_HTTP_RANGE = settings.ace_http_range

    if settings.ace_https_range is not None:
        _validate_port_range(settings.ace_https_range, "ace_https_range")
        current["ace_https_range"] = settings.ace_https_range
        cfg.ACE_HTTPS_RANGE = settings.ace_https_range

    if settings.debug_mode is not None:
        current["debug_mode"] = settings.debug_mode
        cfg.DEBUG_MODE = settings.debug_mode

    if SettingsPersistence.save_orchestrator_config(current):
        logger.info("Orchestrator settings persisted")
    else:
        logger.warning("Failed to persist orchestrator settings")

    return {"message": "Orchestrator settings updated and persisted", **current}


@router.get("/settings/engine")
def get_engine_settings():
    """Get current engine configuration settings."""
    from ...infrastructure.engine_config import (
        EngineConfig,
        detect_platform,
        get_config as get_engine_config,
        resolve_engine_image,
    )
    from ...persistence.settings_persistence import SettingsPersistence

    persisted = SettingsPersistence.load_engine_settings() or {}
    current_platform = detect_platform()
    engine_config = get_engine_config() or EngineConfig()

    default_settings = {
        "min_replicas": cfg.MIN_REPLICAS,
        "max_replicas": cfg.MAX_REPLICAS,
        "auto_delete": cfg.AUTO_DELETE,
        "manual_mode": False,
        "manual_engines": [],
    }

    merged = {**default_settings, **persisted}
    merged.pop("engine_variant", None)
    merged.pop("use_custom_variant", None)
    merged.pop("platform", None)

    payload = {
        **merged,
        **engine_config.model_dump(mode="json"),
        "platform": current_platform,
        "image": resolve_engine_image(current_platform),
    }

    try:
        if not persisted and SettingsPersistence.save_engine_settings(default_settings):
            logger.info("Created default engine settings on first access")
    except Exception as e:
        logger.warning(f"Failed to save default engine settings: {e}")

    return payload


@router.post("/settings/engine", dependencies=[Depends(require_api_key)])
async def update_engine_settings(settings: EngineSettingsUpdate):
    """Update engine configuration settings."""
    from ...persistence.settings_persistence import SettingsPersistence
    from ...infrastructure.engine_config import (
        EngineConfig,
        RESTRICTED_FLAGS,
        detect_platform,
        get_config as get_engine_config,
        resolve_engine_image,
        save_config as save_engine_config,
    )
    from ..routers.provisioning import _trigger_engine_generation_rollout

    current_platform = detect_platform()
    current_settings = SettingsPersistence.load_engine_settings() or {
        "min_replicas": cfg.MIN_REPLICAS,
        "max_replicas": cfg.MAX_REPLICAS,
        "auto_delete": cfg.AUTO_DELETE,
        "manual_mode": False,
        "manual_engines": [],
    }
    current_settings.pop("engine_variant", None)
    current_settings.pop("use_custom_variant", None)
    current_settings.pop("platform", None)

    if settings.min_replicas is not None:
        if settings.min_replicas < 0 or settings.min_replicas > 50:
            raise HTTPException(status_code=400, detail="min_replicas must be between 0 and 50")
        current_settings["min_replicas"] = settings.min_replicas
        cfg.MIN_REPLICAS = settings.min_replicas

    if settings.max_replicas is not None:
        if settings.max_replicas < 1 or settings.max_replicas > 100:
            raise HTTPException(status_code=400, detail="max_replicas must be between 1 and 100")
        current_settings["max_replicas"] = settings.max_replicas
        cfg.MAX_REPLICAS = settings.max_replicas

    if current_settings["min_replicas"] > current_settings["max_replicas"]:
        raise HTTPException(status_code=400, detail="min_replicas must be <= max_replicas")

    if settings.auto_delete is not None:
        current_settings["auto_delete"] = settings.auto_delete
        cfg.AUTO_DELETE = settings.auto_delete

    if settings.manual_mode is not None:
        current_settings["manual_mode"] = settings.manual_mode

    if settings.manual_engines is not None:
        current_settings["manual_engines"] = settings.manual_engines

    if current_settings.get("manual_mode"):
        manual_keys = [k for k in state.engines.keys() if k.startswith("manual-")]
        for k in manual_keys:
            state.remove_engine(k)

        for engine in current_settings.get("manual_engines", []):
            host = engine.get("host")
            port = engine.get("port")
            if host and port:
                container_id = f"manual-{host}-{port}"

                existing = state.get_engine(container_id)
                if not existing:
                    state.engines[container_id] = EngineState(
                        container_id=container_id,
                        container_name=f"manual-{host}-{port}",
                        host=host,
                        port=port,
                        labels={"manual": "true"},
                        first_seen=state.now(),
                        last_seen=state.now(),
                        streams=[],
                        health_status="unknown",
                    )
                else:
                    existing.last_seen = state.now()

    existing_engine_config = get_engine_config() or EngineConfig()
    engine_payload = existing_engine_config.model_dump(mode="json")

    for field in (
        "total_max_download_rate",
        "total_max_upload_rate",
        "live_cache_type",
        "buffer_time",
        "max_peers",
        "memory_limit",
        "parameters",
        "torrent_folder_mount_enabled",
        "torrent_folder_host_path",
        "torrent_folder_container_path",
        "disk_cache_mount_enabled",
        "disk_cache_prune_enabled",
        "disk_cache_prune_interval",
    ):
        incoming = getattr(settings, field)
        if incoming is not None:
            engine_payload[field] = incoming

    try:
        updated_engine_config = EngineConfig(**engine_payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid engine configuration: {e}")

    for parameter in updated_engine_config.parameters:
        if parameter.enabled and parameter.name in RESTRICTED_FLAGS:
            raise HTTPException(
                status_code=400,
                detail=f"restricted parameter '{parameter.name}' is managed by the orchestrator",
            )

    if not save_engine_config(updated_engine_config):
        raise HTTPException(status_code=500, detail="failed to persist engine configuration")

    if SettingsPersistence.save_engine_settings(current_settings):
        logger.info(f"Engine settings persisted: {current_settings}")
    else:
        logger.warning("Failed to persist engine settings to JSON file")

    previous_desired = state.get_desired_replica_count()
    adjusted_desired = max(cfg.MIN_REPLICAS, min(previous_desired, cfg.MAX_REPLICAS))
    state.set_desired_replica_count(adjusted_desired)
    if adjusted_desired != previous_desired:
        logger.info(
            "Adjusted desired replicas after engine settings update: %s -> %s (min=%s max=%s)",
            previous_desired,
            adjusted_desired,
            cfg.MIN_REPLICAS,
            cfg.MAX_REPLICAS,
        )

    from ...infrastructure.engine_settings_applier import (
        LIVE_SETTABLE_FIELDS,
        RESTART_REQUIRED_FIELDS,
        apply_settings_to_all_engines,
    )

    # Determine which categories of fields actually changed.
    old_dump = existing_engine_config.model_dump(mode="json")
    new_dump = updated_engine_config.model_dump(mode="json")
    changed_fields = {k for k in new_dump if new_dump[k] != old_dump.get(k)}

    live_changed = bool(changed_fields & LIVE_SETTABLE_FIELDS)
    restart_required = bool(changed_fields & RESTART_REQUIRED_FIELDS)

    # Push live-settable settings immediately to all healthy engines.
    live_update_results: dict = {}
    if live_changed:
        live_update_results = apply_settings_to_all_engines(updated_engine_config)

    # Trigger a rolling reprovision only when restart-required settings changed.
    rollout: dict = {}
    if restart_required:
        engine_controller.request_reconcile(reason="engine_settings_update")
        rollout = _trigger_engine_generation_rollout(reason="engine_settings_update")

    return {
        "message": "Engine settings updated and persisted",
        **current_settings,
        **updated_engine_config.model_dump(mode="json"),
        "platform": current_platform,
        "image": resolve_engine_image(current_platform),
        "live_update": {
            "applied": live_changed,
            "engines": live_update_results,
        },
        "rolling_update": {
            "triggered": restart_required,
            "changed": bool(rollout.get("changed")),
            "target_generation": rollout.get("generation"),
            "target_hash": rollout.get("config_hash"),
        },
    }


@router.get("/settings")
def get_settings_bundle():
    """Return the consolidated DB-backed runtime settings payload."""
    from ...persistence.settings_persistence import SettingsPersistence

    return SettingsPersistence.load_all_settings()


@router.post("/settings", dependencies=[Depends(require_api_key)])
def update_settings_bundle(payload: SettingsBundleUpdate):
    """Patch one or more settings categories in a single call."""
    from ...persistence.settings_persistence import SettingsPersistence
    from ...proxy.config_helper import Config as ProxyConfig

    updates = payload.model_dump(exclude_none=True)
    applied: Dict[str, bool] = {}

    if "engine_config" in updates:
        applied["engine_config"] = bool(SettingsPersistence.save_engine_config(updates["engine_config"]))
    if "engine_settings" in updates:
        applied["engine_settings"] = bool(SettingsPersistence.save_engine_settings(updates["engine_settings"]))
    if "orchestrator_settings" in updates:
        applied["orchestrator_settings"] = bool(
            SettingsPersistence.save_orchestrator_config(updates["orchestrator_settings"])
        )
    if "proxy_settings" in updates:
        applied["proxy_settings"] = bool(SettingsPersistence.save_proxy_config(updates["proxy_settings"]))
    if "vpn_settings" in updates:
        applied["vpn_settings"] = bool(SettingsPersistence.save_vpn_config(updates["vpn_settings"]))

    if any(not ok for ok in applied.values()):
        raise HTTPException(
            status_code=500,
            detail={"message": "failed to persist one or more settings groups", "applied": applied},
        )

    if "engine_settings" in updates:
        engine_settings = SettingsPersistence.load_engine_settings() or {}
        if "min_replicas" in engine_settings:
            cfg.MIN_REPLICAS = int(engine_settings["min_replicas"])
        if "max_replicas" in engine_settings:
            cfg.MAX_REPLICAS = int(engine_settings["max_replicas"])
        if "auto_delete" in engine_settings:
            cfg.AUTO_DELETE = bool(engine_settings["auto_delete"])

    if "orchestrator_settings" in updates:
        orchestrator_settings = SettingsPersistence.load_orchestrator_config() or {}
        _orch_field_map = {
            "monitor_interval_s": "MONITOR_INTERVAL_S",
            "engine_grace_period_s": "ENGINE_GRACE_PERIOD_S",
            "autoscale_interval_s": "AUTOSCALE_INTERVAL_S",
            "startup_timeout_s": "STARTUP_TIMEOUT_S",
            "idle_ttl_s": "IDLE_TTL_S",
            "collect_interval_s": "COLLECT_INTERVAL_S",
            "stats_history_max": "STATS_HISTORY_MAX",
            "health_check_interval_s": "HEALTH_CHECK_INTERVAL_S",
            "health_failure_threshold": "HEALTH_FAILURE_THRESHOLD",
            "health_unhealthy_grace_period_s": "HEALTH_UNHEALTHY_GRACE_PERIOD_S",
            "health_replacement_cooldown_s": "HEALTH_REPLACEMENT_COOLDOWN_S",
            "circuit_breaker_failure_threshold": "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
            "circuit_breaker_recovery_timeout_s": "CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S",
            "circuit_breaker_replacement_threshold": "CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD",
            "circuit_breaker_replacement_timeout_s": "CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S",
            "max_concurrent_provisions": "MAX_CONCURRENT_PROVISIONS",
            "min_provision_interval_s": "MIN_PROVISION_INTERVAL_S",
            "port_range_host": "PORT_RANGE_HOST",
            "ace_http_range": "ACE_HTTP_RANGE",
            "ace_https_range": "ACE_HTTPS_RANGE",
            "debug_mode": "DEBUG_MODE",
        }
        for _json_key, _cfg_attr in _orch_field_map.items():
            if _json_key in orchestrator_settings:
                _value = orchestrator_settings[_json_key]
                if _json_key == "collect_interval_s":
                    _value = 1
                setattr(cfg, _cfg_attr, _value)

    if "proxy_settings" in updates:
        proxy_settings = SettingsPersistence.load_proxy_config() or {}
        if "initial_data_wait_timeout" in proxy_settings:
            cfg.PROXY_INITIAL_DATA_WAIT_TIMEOUT = proxy_settings["initial_data_wait_timeout"]
        if "stream_timeout" in proxy_settings:
            cfg.PROXY_STREAM_TIMEOUT = proxy_settings["stream_timeout"]
        if "proxy_prebuffer_seconds" in proxy_settings:
            cfg.PROXY_PREBUFFER_SECONDS = max(0, int(proxy_settings["proxy_prebuffer_seconds"]))
        if "stream_mode" in proxy_settings:
            cfg.STREAM_MODE = proxy_settings["stream_mode"]
        if "control_mode" in proxy_settings:
            from ...shared.proxy_modes import normalize_proxy_mode, PROXY_MODE_API
            cfg.PROXY_CONTROL_MODE = normalize_proxy_mode(proxy_settings["control_mode"], default=PROXY_MODE_API) or PROXY_MODE_API
        if "max_streams_per_engine" in proxy_settings:
            cfg.MAX_STREAMS_PER_ENGINE = int(proxy_settings["max_streams_per_engine"])
        if "ace_live_edge_delay" in proxy_settings:
            cfg.ACE_LIVE_EDGE_DELAY = max(0, int(proxy_settings["ace_live_edge_delay"]))
        
        # HLS settings
        if "hls_max_segments" in proxy_settings:
            cfg.HLS_MAX_SEGMENTS = int(proxy_settings["hls_max_segments"])
        if "hls_initial_segments" in proxy_settings:
            cfg.HLS_INITIAL_SEGMENTS = int(proxy_settings["hls_initial_segments"])
        if "hls_window_size" in proxy_settings:
            cfg.HLS_WINDOW_SIZE = int(proxy_settings["hls_window_size"])
        if "hls_buffer_ready_timeout" in proxy_settings:
            cfg.HLS_BUFFER_READY_TIMEOUT = int(proxy_settings["hls_buffer_ready_timeout"])
        if "hls_first_segment_timeout" in proxy_settings:
            cfg.HLS_FIRST_SEGMENT_TIMEOUT = int(proxy_settings["hls_first_segment_timeout"])
        if "hls_initial_buffer_seconds" in proxy_settings:
            cfg.HLS_INITIAL_BUFFER_SECONDS = int(proxy_settings["hls_initial_buffer_seconds"])
        if "hls_max_initial_segments" in proxy_settings:
            cfg.HLS_MAX_INITIAL_SEGMENTS = int(proxy_settings["hls_max_initial_segments"])
        if "hls_segment_fetch_interval" in proxy_settings:
            cfg.HLS_SEGMENT_FETCH_INTERVAL = float(proxy_settings["hls_segment_fetch_interval"])

    if "vpn_settings" in updates:
        vpn_settings = SettingsPersistence.load_vpn_config() or {}
        if "api_port" in vpn_settings:
            cfg.GLUETUN_API_PORT = int(vpn_settings["api_port"])
        if "health_check_interval_s" in vpn_settings:
            cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S = int(vpn_settings["health_check_interval_s"])
        if "port_cache_ttl_s" in vpn_settings:
            cfg.GLUETUN_PORT_CACHE_TTL_S = int(vpn_settings["port_cache_ttl_s"])
        if "restart_engines_on_reconnect" in vpn_settings:
            cfg.VPN_RESTART_ENGINES_ON_RECONNECT = bool(vpn_settings["restart_engines_on_reconnect"])
        if "unhealthy_restart_timeout_s" in vpn_settings:
            cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S = int(vpn_settings["unhealthy_restart_timeout_s"])
        if "preferred_engines_per_vpn" in vpn_settings:
            cfg.PREFERRED_ENGINES_PER_VPN = max(1, int(vpn_settings["preferred_engines_per_vpn"]))
        if "provider" in vpn_settings:
            cfg.VPN_PROVIDER = str(vpn_settings["provider"] or cfg.VPN_PROVIDER).strip().lower() or cfg.VPN_PROVIDER
        if "protocol" in vpn_settings:
            cfg.VPN_PROTOCOL = str(vpn_settings["protocol"] or cfg.VPN_PROTOCOL).strip().lower() or cfg.VPN_PROTOCOL
        cfg.DYNAMIC_VPN_MANAGEMENT = True

    return {
        "message": "Settings updated",
        "applied": applied,
        "settings": SettingsPersistence.load_all_settings(),
    }


@router.get("/settings/export")
async def export_settings(api_key_param: str = Depends(require_api_key)):
    """Export all settings as a ZIP file."""
    import io
    import zipfile
    from datetime import datetime

    try:
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            try:
                from ...infrastructure.engine_config import get_config as get_engine_config

                engine_config = get_engine_config()
                if engine_config:
                    config_json = json.dumps(engine_config.model_dump(mode="json"), indent=2)
                    zip_file.writestr("engine_config.json", config_json)
                    logger.info("Added global engine config to backup")
            except Exception as e:
                logger.warning(f"Failed to export global engine config: {e}")

            try:
                proxy_settings = {
                    "initial_data_wait_timeout": cfg.PROXY_INITIAL_DATA_WAIT_TIMEOUT,
                    "stream_timeout": cfg.PROXY_STREAM_TIMEOUT,
                    "proxy_prebuffer_seconds": cfg.PROXY_PREBUFFER_SECONDS,
                    "stream_mode": cfg.STREAM_MODE,
                    "hls_max_segments": cfg.HLS_MAX_SEGMENTS,
                    "hls_initial_segments": cfg.HLS_INITIAL_SEGMENTS,
                    "hls_window_size": cfg.HLS_WINDOW_SIZE,
                    "hls_buffer_ready_timeout": cfg.HLS_BUFFER_READY_TIMEOUT,
                    "hls_first_segment_timeout": cfg.HLS_FIRST_SEGMENT_TIMEOUT,
                    "hls_initial_buffer_seconds": cfg.HLS_INITIAL_BUFFER_SECONDS,
                    "hls_max_initial_segments": cfg.HLS_MAX_INITIAL_SEGMENTS,
                    "hls_segment_fetch_interval": cfg.HLS_SEGMENT_FETCH_INTERVAL,
                }
                proxy_json = json.dumps(proxy_settings, indent=2)
                zip_file.writestr("proxy_settings.json", proxy_json)
                logger.info("Added proxy settings to backup")
            except Exception as e:
                logger.warning(f"Failed to export proxy settings: {e}")



            try:
                from ...persistence.settings_persistence import SettingsPersistence

                engine_settings = SettingsPersistence.load_engine_settings()
                if engine_settings:
                    engine_json = json.dumps(engine_settings, indent=2)
                    zip_file.writestr("engine_settings.json", engine_json)
                    logger.info("Added engine settings to backup")
            except Exception as e:
                logger.warning(f"Failed to export engine settings: {e}")

            metadata = {
                "export_date": datetime.now().isoformat(),
                "version": BACKUP_FORMAT_VERSION,
                "description": "AceStream Orchestrator Settings Backup",
            }
            zip_file.writestr("metadata.json", json.dumps(metadata, indent=2))

        zip_buffer.seek(0)

        import io as _io

        return StreamingResponse(
            _io.BytesIO(zip_buffer.getvalue()),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=orchestrator_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            },
        )
    except Exception as e:
        logger.error(f"Failed to export settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to export settings: {str(e)}")


@router.post("/settings/import")
async def import_settings_data(
    request: Request,
    import_engine_config: bool = Query(True),
    import_proxy: bool = Query(True),
    import_engine: bool = Query(True),
    import_custom_variant: Optional[bool] = Query(None),
    import_templates: Optional[bool] = Query(None),
    api_key_param: str = Depends(require_api_key),
):
    """Import settings from uploaded ZIP file data."""
    import io
    import zipfile

    from ...infrastructure.engine_config import EngineConfig, reload_config as reload_engine_config, save_config as save_engine_config
    from ...persistence.settings_persistence import SettingsPersistence

    try:
        file_data = await request.body()

        if not file_data:
            raise HTTPException(status_code=400, detail="No file data provided")

        imported = {
            "engine_config": False,
            "proxy": False,
            "engine": False,
            "errors": [],
        }

        effective_import_engine_config = bool(import_engine_config)
        if import_custom_variant is not None:
            effective_import_engine_config = bool(import_custom_variant)

        zip_buffer = io.BytesIO(file_data)

        with zipfile.ZipFile(zip_buffer, "r") as zip_file:
            if effective_import_engine_config:
                config_filename = None
                if "engine_config.json" in zip_file.namelist():
                    config_filename = "engine_config.json"
                elif "custom_engine_variant.json" in zip_file.namelist():
                    config_filename = "custom_engine_variant.json"

                if config_filename:
                    try:
                        config_data = zip_file.read(config_filename).decode("utf-8")
                        config_dict = json.loads(config_data)
                        config = EngineConfig(**config_dict)
                        if save_engine_config(config):
                            reload_engine_config()
                            imported["engine_config"] = True
                            logger.info("Imported global engine config from %s", config_filename)
                    except Exception as e:
                        error_msg = f"Failed to import engine config: {e}"
                        logger.error(error_msg)
                        imported["errors"].append(error_msg)

            if import_proxy and "proxy_settings.json" in zip_file.namelist():
                try:
                    from ...proxy.config_helper import Config as ProxyConfig

                    proxy_data = zip_file.read("proxy_settings.json").decode("utf-8")
                    proxy_dict = json.loads(proxy_data)

                    ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT = proxy_dict.get(
                        "initial_data_wait_timeout", ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT
                    )
                    ProxyConfig.INITIAL_DATA_CHECK_INTERVAL = proxy_dict.get(
                        "initial_data_check_interval", ProxyConfig.INITIAL_DATA_CHECK_INTERVAL
                    )
                    ProxyConfig.NO_DATA_TIMEOUT_CHECKS = proxy_dict.get(
                        "no_data_timeout_checks", ProxyConfig.NO_DATA_TIMEOUT_CHECKS
                    )
                    ProxyConfig.NO_DATA_CHECK_INTERVAL = proxy_dict.get(
                        "no_data_check_interval", ProxyConfig.NO_DATA_CHECK_INTERVAL
                    )
                    ProxyConfig.CONNECTION_TIMEOUT = proxy_dict.get(
                        "connection_timeout", ProxyConfig.CONNECTION_TIMEOUT
                    )
                    ProxyConfig.STREAM_TIMEOUT = proxy_dict.get("stream_timeout", ProxyConfig.STREAM_TIMEOUT)
                    ProxyConfig.CHANNEL_SHUTDOWN_DELAY = proxy_dict.get(
                        "channel_shutdown_delay", ProxyConfig.CHANNEL_SHUTDOWN_DELAY
                    )

                    if "stream_mode" in proxy_dict:
                        ProxyConfig.STREAM_MODE = proxy_dict["stream_mode"]

                    if SettingsPersistence.save_proxy_config(proxy_dict):
                        imported["proxy"] = True
                        logger.info("Imported proxy settings")
                    else:
                        error_msg = "Failed to persist proxy settings to file"
                        logger.error(error_msg)
                        imported["errors"].append(error_msg)
                except Exception as e:
                    error_msg = f"Failed to import proxy settings: {e}"
                    logger.error(error_msg)
                    imported["errors"].append(error_msg)



            if import_engine and "engine_settings.json" in zip_file.namelist():
                try:
                    engine_data = zip_file.read("engine_settings.json").decode("utf-8")
                    engine_dict = json.loads(engine_data)

                    if "min_replicas" in engine_dict:
                        cfg.MIN_REPLICAS = engine_dict["min_replicas"]
                    if "max_replicas" in engine_dict:
                        cfg.MAX_REPLICAS = engine_dict["max_replicas"]
                    if "auto_delete" in engine_dict:
                        cfg.AUTO_DELETE = engine_dict["auto_delete"]

                    if SettingsPersistence.save_engine_settings(engine_dict):
                        imported["engine"] = True
                        logger.info("Imported engine settings")
                    else:
                        error_msg = "Failed to persist engine settings to file"
                        logger.error(error_msg)
                        imported["errors"].append(error_msg)
                except Exception as e:
                    error_msg = f"Failed to import engine settings: {e}"
                    logger.error(error_msg)
                    imported["errors"].append(error_msg)

        return {"message": "Settings imported successfully", "imported": imported}
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")
    except Exception as e:
        logger.error(f"Failed to import settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to import settings: {str(e)}")
