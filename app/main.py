from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI, HTTPException, Query, Depends, BackgroundTasks, Request, APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from fastapi.routing import APIRoute
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
import asyncio
import io
import os
import json
import logging
import hashlib
import re
import httpx
import threading
from uuid import uuid4
from types import SimpleNamespace
import time
from docker.errors import NotFound

from .utils.logging import setup
from .utils.wireguard_parser import parse_wireguard_conf
from .core.config import cfg
from .control_plane.autoscaler import ensure_minimum, scale_to, can_stop_engine, engine_controller
from .control_plane.provisioner import StartRequest, start_container, stop_container, AceProvisionRequest, AceProvisionResponse, start_acestream, HOST_LABEL_HTTP, compute_current_engine_config_hash
from .control_plane.health import sweep_idle
from .control_plane.health_monitor import health_monitor
from .control_plane.health_manager import health_manager
from .infrastructure.inspect import inspect_container, ContainerNotFound
from .services.state import state, load_state_from_db, cleanup_on_shutdown
from .models.schemas import (
    StreamStartedEvent,
    StreamEndedEvent,
    EngineState,
    StreamState,
    StreamStatSnapshot,
    EventLog,
    HealthStatusResponse,
    MetricsResponse,
    OrchestratorStatusResponse,
    GenericObjectResponse,
    GenericListResponse,
    VPNSettingsUpdate,
    VPNSettingsResponse,
)
from .observability.collector import collector
from .observability.event_logger import event_logger
from .data_plane.stream_cleanup import stream_cleanup
from .observability.metrics import (
    update_custom_metrics,
    observe_proxy_request,
    observe_proxy_ttfb,
    observe_proxy_egress_bytes,
)
from .infrastructure.engine_selection import select_best_engine as select_best_engine_shared
from .api.auth import require_api_key
from .persistence.db import engine
from .models.db_models import Base
from .persistence.reindex import reindex_existing
from .vpn.gluetun import gluetun_monitor
from .infrastructure.docker_stats import get_container_stats, get_multiple_container_stats, get_total_stats
from .infrastructure.docker_stats_collector import docker_stats_collector
from .persistence.cache import start_cleanup_task, stop_cleanup_task, invalidate_cache, get_cache
from .data_plane.stream_loop_detector import stream_loop_detector
from .data_plane.looping_streams import looping_streams_tracker
from .vpn.vpn_controller import vpn_controller
from .persistence.db_maintenance import db_maintenance_service
from .observability.cache_monitoring_service import start_cache_monitoring
from .data_plane.legacy_stream_monitoring import legacy_stream_monitoring_service
from .data_plane.hls_segmenter import hls_segmenter_service
from .infrastructure.docker_client import get_client, docker_event_watcher
from .vpn.vpn_credentials import credential_manager
from .vpn.proton_updater import ProtonServerUpdater, ProtonFilterConfig
from .vpn.vpn_servers_refresh import vpn_servers_refresh_service
from .shared.utils import get_client_ip, sanitize_stream_id
from .data_plane.client_tracker import client_tracking_service
from .shared.redis_client import get_redis_client

logger = logging.getLogger(__name__)

setup()


_CLIENT_TRACKER_PRUNE_LOCK = threading.Lock()
_CLIENT_TRACKER_LAST_PRUNE_MONOTONIC = 0.0


def _prune_client_tracker_if_due(*, ttl_s: float, min_interval_s: float = 3.0) -> None:
    """Prune stale tracker rows with throttling to keep hot endpoints responsive."""
    if ttl_s <= 0:
        return

    global _CLIENT_TRACKER_LAST_PRUNE_MONOTONIC
    now_monotonic = time.monotonic()
    if (now_monotonic - _CLIENT_TRACKER_LAST_PRUNE_MONOTONIC) < max(0.25, float(min_interval_s)):
        return

    with _CLIENT_TRACKER_PRUNE_LOCK:
        now_monotonic = time.monotonic()
        if (now_monotonic - _CLIENT_TRACKER_LAST_PRUNE_MONOTONIC) < max(0.25, float(min_interval_s)):
            return
        from .data_plane.client_tracker import client_tracking_service

        client_tracking_service.prune_stale_clients(float(ttl_s))
        _CLIENT_TRACKER_LAST_PRUNE_MONOTONIC = now_monotonic




def _format_sse_message(payload: Dict[str, Any], *, event_name: Optional[str] = None, event_id: Optional[str] = None) -> str:
    chunks: List[str] = []
    if event_name:
        chunks.append(f"event: {event_name}\n")
    if event_id:
        chunks.append(f"id: {event_id}\n")

    data = json.dumps(payload, separators=(",", ":"), default=str)
    for line in data.splitlines() or [data]:
        chunks.append(f"data: {line}\n")
    chunks.append("\n")
    return "".join(chunks)


def _build_sse_payload() -> Dict[str, Any]:
    engines = state.list_engines()
    streams = state.list_streams_with_stats(status="started")
    pending_failover_streams = state.list_streams_with_stats(status="pending_failover")
    seen_stream_ids = {str(getattr(stream, "id", "")) for stream in streams}
    streams = streams + [
        stream for stream in pending_failover_streams
        if str(getattr(stream, "id", "")) not in seen_stream_ids
    ]
    engine_docker_stats = docker_stats_collector.get_all_stats()

    total_peers = 0
    total_speed_down = 0
    total_speed_up = 0
    
    # Pre-fetch tracker for efficiency (but only if we have active streams)
    active_stream_keys = [s.key for s in streams if getattr(s, "key", None)]
    from .data_plane.client_tracker import client_tracking_service
    
    for stream in streams:
        # Populate clients for dashboard runway calculation
        if stream.status == "started" and getattr(stream, "key", None):
            with suppress(Exception):
                stream.clients = client_tracking_service.get_stream_clients(stream.key)
        
        try:
            total_peers += int(stream.peers or 0)
        except Exception:
            pass
        try:
            total_speed_down += int(stream.speed_down or 0)
        except Exception:
            pass
        try:
            total_speed_up += int(stream.speed_up or 0)
        except Exception:
            pass

    try:
        vpn_status = get_vpn_status()
    except Exception:
        vpn_status = {"enabled": False}

    try:
        orchestrator_status = get_orchestrator_status()
    except Exception:
        orchestrator_status = None


    return {
        "engines": jsonable_encoder(engines),
        "engine_docker_stats": jsonable_encoder(engine_docker_stats),
        "streams": jsonable_encoder(streams),
        "vpn_status": jsonable_encoder(vpn_status),
        "orchestrator_status": jsonable_encoder(orchestrator_status),
        "kpis": {
            "total_engines": len(engines),
            "active_streams": len(streams),
            "healthy_engines": sum(1 for e in engines if (e.health_status or "").lower() == "healthy"),
            "total_peers": total_peers,
            "total_speed_down": total_speed_down,
            "total_speed_up": total_speed_up,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _validate_sse_api_key(request: Request, api_key: Optional[str]):
    """Validate SSE clients where EventSource cannot send custom auth headers."""
    if not cfg.API_KEY:
        return

    token = (api_key or "").strip()
    if not token:
        authorization = str(request.headers.get("Authorization") or "")
        if authorization.startswith("Bearer "):
            token = authorization.split(" ", 1)[1].strip()

    if token != cfg.API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid SSE API key")


def _serialize_event_row(event_row) -> Dict[str, Any]:
    timestamp = event_row.timestamp
    if timestamp and getattr(timestamp, "tzinfo", None) is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    return {
        "id": event_row.id,
        "timestamp": timestamp,
        "event_type": event_row.event_type,
        "category": event_row.category,
        "message": event_row.message,
        "details": event_row.details or {},
        "container_id": event_row.container_id,
        "stream_id": event_row.stream_id,
    }


def _build_events_sse_payload(limit: int, event_type: Optional[str]) -> Dict[str, Any]:
    rows = event_logger.get_events(limit=limit, event_type=event_type)
    return {
        "events": jsonable_encoder([_serialize_event_row(row) for row in rows]),
        "stats": jsonable_encoder(event_logger.get_event_stats()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class WireguardParseRequest(BaseModel):
    file_content: str


class StreamMigrationRequest(BaseModel):
    stream_key: str
    old_container_id: Optional[str] = None
    new_container_id: Optional[str] = None


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


def _trigger_engine_generation_rollout(reason: str) -> Dict[str, Any]:
    """Update target engine config generation and request reconciliation."""
    target_hash = compute_current_engine_config_hash()
    result = state.set_target_engine_config(target_hash)
    if result.get("changed"):
        logger.info(
            f"Engine target config updated ({reason}): hash={result['config_hash']} generation={result['generation']}"
        )
        engine_controller.request_reconcile(reason=f"config_rollout:{reason}")
    return result


def _mark_engines_draining_for_reprovision(reason: str = "engine_settings_reprovision") -> int:
    """Mark managed engines as draining so they are replaced during reconcile."""
    marked = 0

    for engine_state in state.list_engines():
        labels = getattr(engine_state, "labels", None)
        if isinstance(labels, dict) and str(labels.get("manual") or "").strip().lower() == "true":
            continue

        container_id = str(getattr(engine_state, "container_id", "") or "").strip()
        if not container_id:
            continue

        if state.mark_engine_draining(container_id, reason=reason):
            marked += 1

    return marked



async def _refresh_vpn_servers_before_vpn_provision(vpn_controller_enabled: bool) -> None:
    """Refresh VPN server list before any VPN node provisioning begins.

    Startup tries the configured refresh source first. If it fails or is unavailable,
    it falls back to the official Gluetun servers source.
    """
    if not vpn_controller_enabled:
        return

    refresh_timeout_s = 180
    configured_source = "gluetun_official"

    try:
        from .persistence.settings_persistence import SettingsPersistence

        configured_source = str(
            SettingsPersistence.get_cached_setting(
                "vpn_settings",
                "vpn_servers_refresh_source",
                "gluetun_official",
            )
            or "gluetun_official"
        ).strip().lower()
    except Exception:
        configured_source = "gluetun_official"

    try:
        result = await asyncio.wait_for(
            vpn_servers_refresh_service.refresh_now(reason="startup-preprovision"),
            timeout=refresh_timeout_s,
        )
        if not bool(result.get("ok", False)):
            raise RuntimeError(str(result.get("detail") or "startup refresh returned not-ok status"))

        logger.info(
            "Startup VPN server refresh succeeded before provisioning: source=%s duration_s=%s",
            result.get("source"),
            result.get("duration_s"),
        )
        return
    except Exception as exc:
        logger.warning(
            "Startup VPN server refresh failed for source=%s; falling back to gluetun_official: %s",
            configured_source,
            exc,
        )

    try:
        fallback_result = await asyncio.wait_for(
            vpn_servers_refresh_service.refresh_now(
                reason="startup-fallback-gluetun",
                overrides={"vpn_servers_refresh_source": "gluetun_official"},
            ),
            timeout=refresh_timeout_s,
        )
        if not bool(fallback_result.get("ok", False)):
            raise RuntimeError(
                str(fallback_result.get("detail") or "startup fallback refresh returned not-ok status")
            )

        logger.info(
            "Startup VPN server refresh fallback succeeded: source=%s duration_s=%s",
            fallback_result.get("source"),
            fallback_result.get("duration_s"),
        )
    except Exception as fallback_exc:
        logger.error(
            "Startup VPN server refresh fallback failed; continuing with existing server catalog: %s",
            fallback_exc,
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - Ensure clean start (dry run)
    Base.metadata.create_all(bind=engine)
    from .persistence.db_maintenance import migrate_bitrate_column
    migrate_bitrate_column()

    from .persistence.config_migrator import migrate_legacy_json_configs
    from .persistence.settings_persistence import SettingsPersistence

    migration_result = migrate_legacy_json_configs()
    SettingsPersistence.initialize_cache(force_reload=True)
    if migration_result.get("renamed_files"):
        logger.info("Legacy settings migrated to DB and renamed: %s", migration_result.get("renamed_files"))
    elif migration_result.get("seeded_defaults"):
        logger.info("Runtime settings initialized with a new default row (no legacy JSON files found)")

    await cleanup_on_shutdown()  # Clean any existing state and containers after DB is ready
    
    # Load global engine customization early to ensure provisioning uses persisted values.
    from .infrastructure.engine_config import detect_platform, load_config as load_engine_config
    try:
        engine_config = load_engine_config()
        if engine_config:
            logger.info(
                "Loaded global engine config (platform=%s, cache=%s)",
                detect_platform(),
                engine_config.live_cache_type,
            )
        else:
            logger.warning("Global engine config is unavailable; provisioning will use runtime defaults")
    except Exception as e:
        logger.warning(f"Failed to load global engine config during startup: {e}")
    
    
    _loaded_live_edge_from_proxy = False

    # Load proxy settings
    try:
        proxy_settings = SettingsPersistence.load_proxy_config()
        if proxy_settings:
            logger.debug("Loading persisted proxy settings")
            if 'max_streams_per_engine' in proxy_settings:
                cfg.MAX_STREAMS_PER_ENGINE = proxy_settings['max_streams_per_engine']
            if 'ace_live_edge_delay' in proxy_settings:
                try:
                    cfg.ACE_LIVE_EDGE_DELAY = max(0, int(proxy_settings['ace_live_edge_delay']))
                    _loaded_live_edge_from_proxy = True
                except Exception:
                    logger.warning("Invalid ace_live_edge_delay in persisted proxy settings; keeping current value")
            logger.debug("Proxy settings loaded from persistent storage")
    except Exception as e:
        logger.warning(f"Failed to load persisted proxy settings: {e}")
    
    # Load loop detection settings
    try:
        loop_settings = SettingsPersistence.load_loop_detection_config()
        if loop_settings:
            logger.debug("Loading persisted loop detection settings")
            if 'enabled' in loop_settings:
                cfg.STREAM_LOOP_DETECTION_ENABLED = loop_settings['enabled']
            if 'threshold_seconds' in loop_settings:
                cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = loop_settings['threshold_seconds']
            if 'check_interval_seconds' in loop_settings:
                cfg.STREAM_LOOP_CHECK_INTERVAL_S = loop_settings['check_interval_seconds']
            if 'retention_minutes' in loop_settings:
                cfg.STREAM_LOOP_RETENTION_MINUTES = loop_settings['retention_minutes']
            logger.debug("Loop detection settings loaded from persistent storage")
    except Exception as e:
        logger.warning(f"Failed to load persisted loop detection settings: {e}")
    
    # Load engine settings
    try:
        engine_settings = SettingsPersistence.load_engine_settings()
        if engine_settings:
            logger.debug("Loading persisted engine settings")
            if 'min_replicas' in engine_settings:
                cfg.MIN_REPLICAS = engine_settings['min_replicas']
            if 'max_replicas' in engine_settings:
                cfg.MAX_REPLICAS = engine_settings['max_replicas']
            if 'auto_delete' in engine_settings:
                cfg.AUTO_DELETE = engine_settings['auto_delete']
            
            # Load manual engines into state if manual mode is enabled
            if engine_settings.get('manual_mode'):
                logger.info("Manual mode is enabled. Injecting manual engines into state on startup.")
                from .models.schemas import EngineState
                
                for man_eng in engine_settings.get("manual_engines", []):
                    host = man_eng.get("host")
                    port = man_eng.get("port")
                    if host and port:
                        container_id = f"manual-{host}-{port}"
                        state.engines[container_id] = EngineState(
                            container_id=container_id,
                            container_name=f"manual-{host}-{port}",
                            host=host,
                            port=port,
                            labels={"manual": "true"},
                            first_seen=state.now(),
                            last_seen=state.now(),
                            streams=[],
                            health_status="unknown"
                        )
                        
            logger.info(
                "Engine settings loaded from persistent storage: MIN_REPLICAS=%s, MAX_REPLICAS=%s, AUTO_DELETE=%s, MANUAL_MODE=%s",
                cfg.MIN_REPLICAS,
                cfg.MAX_REPLICAS,
                cfg.AUTO_DELETE,
                engine_settings.get('manual_mode', False),
            )
        else:
            # No persisted settings found - create default settings from current config
            logger.info("No persisted engine settings found, creating defaults")
            default_settings = {
                "min_replicas": cfg.MIN_REPLICAS,
                "max_replicas": cfg.MAX_REPLICAS,
                "auto_delete": cfg.AUTO_DELETE,
                "manual_mode": False,
                "manual_engines": [],
            }
            if SettingsPersistence.save_engine_settings(default_settings):
                logger.info(f"Default engine settings created and saved: MIN_REPLICAS={cfg.MIN_REPLICAS}, MAX_REPLICAS={cfg.MAX_REPLICAS}, AUTO_DELETE={cfg.AUTO_DELETE}")
            else:
                logger.warning("Failed to save default engine settings")
    except Exception as e:
        logger.warning(f"Failed to load persisted engine settings: {e}")
    

    # Load orchestrator settings
    try:
        orchestrator_settings = SettingsPersistence.load_orchestrator_config()
        if orchestrator_settings:
            logger.debug("Loading persisted orchestrator settings")
            _orch_field_map = {
                'monitor_interval_s': 'MONITOR_INTERVAL_S',
                'engine_grace_period_s': 'ENGINE_GRACE_PERIOD_S',
                'autoscale_interval_s': 'AUTOSCALE_INTERVAL_S',
                'startup_timeout_s': 'STARTUP_TIMEOUT_S',
                'idle_ttl_s': 'IDLE_TTL_S',
                'collect_interval_s': 'COLLECT_INTERVAL_S',
                'stats_history_max': 'STATS_HISTORY_MAX',
                'health_check_interval_s': 'HEALTH_CHECK_INTERVAL_S',
                'health_failure_threshold': 'HEALTH_FAILURE_THRESHOLD',
                'health_unhealthy_grace_period_s': 'HEALTH_UNHEALTHY_GRACE_PERIOD_S',
                'health_replacement_cooldown_s': 'HEALTH_REPLACEMENT_COOLDOWN_S',
                'circuit_breaker_failure_threshold': 'CIRCUIT_BREAKER_FAILURE_THRESHOLD',
                'circuit_breaker_recovery_timeout_s': 'CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S',
                'circuit_breaker_replacement_threshold': 'CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD',
                'circuit_breaker_replacement_timeout_s': 'CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S',
                'max_concurrent_provisions': 'MAX_CONCURRENT_PROVISIONS',
                'min_provision_interval_s': 'MIN_PROVISION_INTERVAL_S',
                'port_range_host': 'PORT_RANGE_HOST',
                'ace_http_range': 'ACE_HTTP_RANGE',
                'ace_https_range': 'ACE_HTTPS_RANGE',
                'debug_mode': 'DEBUG_MODE',
            }
            for _json_key, _cfg_attr in _orch_field_map.items():
                if _json_key in orchestrator_settings:
                    _value = orchestrator_settings[_json_key]
                    if _json_key == 'collect_interval_s':
                        # Keep collector cadence fast so frontend topology interpolation has fresh targets.
                        _value = 1
                    setattr(cfg, _cfg_attr, _value)

            # Backward compatibility: older releases persisted live edge under orchestrator settings.
            if not _loaded_live_edge_from_proxy and 'ace_live_edge_delay' in orchestrator_settings:
                try:
                    cfg.ACE_LIVE_EDGE_DELAY = max(0, int(orchestrator_settings['ace_live_edge_delay']))
                    logger.info(
                        "Loaded legacy ace_live_edge_delay from orchestrator settings and migrated runtime ownership to proxy"
                    )
                except Exception:
                    logger.warning("Invalid legacy ace_live_edge_delay in orchestrator settings; keeping current value")
            logger.debug("Orchestrator settings loaded from persistent storage")
    except Exception as e:
        logger.warning(f"Failed to load persisted orchestrator settings: {e}")

    vpn_settings: Dict[str, Any] = {}
    vpn_controller_enabled = False

    # Load VPN settings
    try:
        vpn_settings = SettingsPersistence.load_vpn_config() or {}
        if vpn_settings:
            logger.debug("Loading persisted VPN settings")
            vpn_enabled = bool(vpn_settings.get('enabled', False))
            if 'api_port' in vpn_settings:
                cfg.GLUETUN_API_PORT = vpn_settings['api_port']
            if 'health_check_interval_s' in vpn_settings:
                cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S = vpn_settings['health_check_interval_s']
            if 'port_cache_ttl_s' in vpn_settings:
                cfg.GLUETUN_PORT_CACHE_TTL_S = vpn_settings['port_cache_ttl_s']
            if 'restart_engines_on_reconnect' in vpn_settings:
                cfg.VPN_RESTART_ENGINES_ON_RECONNECT = vpn_settings['restart_engines_on_reconnect']
            if 'unhealthy_restart_timeout_s' in vpn_settings:
                cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S = vpn_settings['unhealthy_restart_timeout_s']
            cfg.DYNAMIC_VPN_MANAGEMENT = True
            if 'preferred_engines_per_vpn' in vpn_settings:
                try:
                    cfg.PREFERRED_ENGINES_PER_VPN = max(1, int(vpn_settings['preferred_engines_per_vpn']))
                except Exception:
                    logger.warning("Invalid preferred_engines_per_vpn in persisted settings; keeping current value")
            if 'provider' in vpn_settings:
                cfg.VPN_PROVIDER = str(vpn_settings['provider'] or cfg.VPN_PROVIDER).strip().lower() or cfg.VPN_PROVIDER
            if 'protocol' in vpn_settings:
                protocol = str(vpn_settings['protocol'] or cfg.VPN_PROTOCOL).strip().lower()
                cfg.VPN_PROTOCOL = protocol or cfg.VPN_PROTOCOL

            logger.info(
                "VPN settings loaded from persistent storage: enabled=%s dynamic=%s provider=%s protocol=%s preferred_engines_per_vpn=%s",
                vpn_enabled,
                cfg.DYNAMIC_VPN_MANAGEMENT,
                cfg.VPN_PROVIDER,
                cfg.VPN_PROTOCOL,
                cfg.PREFERRED_ENGINES_PER_VPN,
            )

        vpn_controller_enabled = bool(vpn_settings.get("enabled", False))

        provider_value = str(vpn_settings.get("provider") or cfg.VPN_PROVIDER).strip().lower()
        providers = [provider_value] if provider_value else []

        lease_summary = await credential_manager.configure(
            dynamic_vpn_management=True,
            providers=providers,
            protocol=vpn_settings.get("protocol") or cfg.VPN_PROTOCOL,
            regions=vpn_settings.get("regions", []),
            credentials=vpn_settings.get("credentials", []),
        )
        logger.info(
            "VPN credential manager initialized: dynamic=%s max_vpn_capacity=%s available=%s leased=%s",
            lease_summary.get("dynamic_vpn_management"),
            lease_summary.get("max_vpn_capacity"),
            lease_summary.get("available"),
            lease_summary.get("leased"),
        )
    except Exception as e:
        logger.warning(f"Failed to load persisted VPN settings: {e}")

    # Load state from database first
    load_state_from_db()
    
    # Initialize client tracker with Redis for cross-plane visibility
    client_tracking_service.set_redis_client(get_redis_client())
    
    # Initialize looping streams tracker with configured retention
    looping_streams_tracker.set_retention_minutes(cfg.STREAM_LOOP_RETENTION_MINUTES)
    
    state.set_desired_replica_count(cfg.MIN_REPLICAS)
    target_config_hash = compute_current_engine_config_hash()
    target_config = state.set_target_engine_config(target_config_hash)
    logger.info(
        f"Initialized desired replicas={cfg.MIN_REPLICAS}, config_hash={target_config['config_hash']}, generation={target_config['generation']}"
    )

    # Start VPN server refresh in background to avoid blocking API startup.
    # The VPN controller will use existing servers until the refresh finishes.
    asyncio.create_task(
        _refresh_vpn_servers_before_vpn_provision(vpn_controller_enabled),
        name="initial-vpn-refresh"
    )

    await docker_event_watcher.start()
    await engine_controller.start()

    if vpn_controller_enabled:
        await vpn_controller.start()
    else:
        logger.info("Dynamic VPN controller disabled in settings")
    
    # Start remaining monitoring services
    asyncio.create_task(collector.start())
    asyncio.create_task(stream_cleanup.start())  # Start stream cleanup service
    asyncio.create_task(health_monitor.start())  # Start health monitoring  
    asyncio.create_task(health_manager.start())  # Start proactive health management
    asyncio.create_task(docker_stats_collector.start())  # Start Docker stats collection
    asyncio.create_task(stream_loop_detector.start())  # Start stream loop detection
    asyncio.create_task(looping_streams_tracker.start())  # Start looping streams tracker
    asyncio.create_task(db_maintenance_service.start())  # Start daily DB pruning and vacuum
    
    # Start engine cache manager background tasks
    from .infrastructure.engine_cache_manager import engine_cache_manager
    if engine_cache_manager.is_enabled():
        asyncio.create_task(engine_cache_manager.start_pruner())
        asyncio.create_task(start_cache_monitoring())
        logger.info("Engine cache pruner and monitoring started")
    
    reindex_existing()  # Final reindex to ensure all containers are properly tracked
    
    # Start cache cleanup task
    await start_cleanup_task(interval=60)
    logger.info("Cache service started")

    await vpn_servers_refresh_service.start()
    
    yield
    
    # Shutdown
    await collector.stop()
    await stream_cleanup.stop()  # Stop stream cleanup service
    await health_monitor.stop()  # Stop health monitoring
    await health_manager.stop()  # Stop health management
    await docker_stats_collector.stop()  # Stop Docker stats collector
    if vpn_controller.is_running():
        await vpn_controller.stop()  # Stop VPN reconciliation controller
    await engine_controller.stop()  # Stop declarative engine controller
    await docker_event_watcher.stop()  # Stop Docker event watcher
    await stream_loop_detector.stop()  # Stop stream loop detector
    await looping_streams_tracker.stop()  # Stop looping streams tracker
    await db_maintenance_service.stop()  # Stop DB maintenance loop
    await vpn_servers_refresh_service.stop()  # Stop VPN servers refresh loop
    await legacy_stream_monitoring_service.stop_all()  # Stop legacy monitor sessions
    await stop_cleanup_task()  # Stop cache cleanup
    
    # Final cleanup: stop engines and clear state
    await cleanup_on_shutdown()
    logger.info("Orchestrator shutdown complete")
    
    # Give a small delay to ensure any pending operations complete
    await asyncio.sleep(0.1)
    
    await cleanup_on_shutdown()

__version__ = "1.7.3"

app = FastAPI(
    title="On-Demand Orchestrator",
    version=__version__,
    lifespan=lifespan
)


@app.get("/api/v1/openapi.json", include_in_schema=False)
def get_v1_openapi_spec():
    """Serve OpenAPI spec under the versioned API namespace."""
    return app.openapi()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
async def serve_root():
    """
    Serve index.html at root level to present the panel directly.
    Fall back to /panel redirect if the static panel directory isn't available.
    """
    panel_dir = "app/static/panel"
    index_path = os.path.join(panel_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return RedirectResponse(url="/panel")

# Mount static files with validation and SPA fallback
panel_dir = "app/static/panel"
if os.path.exists(panel_dir) and os.path.isdir(panel_dir):
    # Add catch-all route for SPA routing BEFORE mounting StaticFiles
    # This ensures direct navigation to subroutes like /panel/engines works
    @app.get("/panel/{full_path:path}")
    async def serve_spa(full_path: str):
        """
        Catch-all route to serve index.html for all /panel/* routes.
        This enables direct navigation to subroutes like /panel/engines.
        """
        # Sanitize the path to prevent directory traversal attacks
        # Resolve to absolute path and ensure it's within panel_dir
        panel_dir_abs = os.path.abspath(panel_dir)
        requested_path = os.path.abspath(os.path.join(panel_dir, full_path))
        
        # Security check: ensure the requested path is within panel_dir
        if not requested_path.startswith(panel_dir_abs + os.sep) and requested_path != panel_dir_abs:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Check if it's a request for an actual file (has extension)
        if os.path.isfile(requested_path):
            return FileResponse(requested_path)
        
        # Otherwise, serve index.html for React Router
        index_path = os.path.join(panel_dir_abs, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        else:
            raise HTTPException(status_code=404, detail="Panel not found")
    
    # Mount static files for assets (without html=True since we handle it above)
    app.mount("/panel", StaticFiles(directory=panel_dir), name="panel")
else:
    logger.warning(f"Panel directory {panel_dir} not found. /panel endpoint will not be available.")


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------
from .api.routers.debug import router as _debug_router
from .api.routers.engines import router as _engines_router
from .api.routers.streams import router as _streams_router
from .api.routers.proxy_routes import router as _proxy_router
from .api.routers.provisioning import router as _provisioning_router
from .api.routers.vpn import router as _vpn_router
from .api.routers.settings import router as _settings_router
from .api.routers.observability import router as _observability_router
from .api.routers.legacy_monitor import router as _legacy_monitor_router

app.include_router(_debug_router)
app.include_router(_engines_router)
app.include_router(_streams_router)
app.include_router(_proxy_router)
app.include_router(_provisioning_router)
app.include_router(_vpn_router)
app.include_router(_settings_router)
app.include_router(_observability_router)
app.include_router(_legacy_monitor_router)


def get_orchestrator_status():
    """Thin wrapper kept for backward-compat (sse_helpers dynamic import)."""
    from .api.routers.observability import get_orchestrator_status as _f
    return _f()

_MANAGEMENT_ROUTE_EXCLUDED_PREFIXES = (
    "/panel",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon",
    "/apple-touch-icon.png",
)
_MANAGEMENT_ROUTE_EXCLUDED_EXACT = {
    "/ace/getstream",
    "/ace/manifest.m3u8",
    "/ace/hls/{content_id}/segment/{segment_path:path}",
}

_TAG_BY_SEGMENT = {
    "engines": "Engines",
    "streams": "Streams",
    "settings": "Settings",
    "proxy": "Proxy",
    "ace": "Proxy",
    "health": "Health",
    "orchestrator": "Health",
    "vpn": "Health",
    "events": "Events",
    "metrics": "Metrics",
    "cache": "Cache",
    "engine-cache": "Cache",
    "engine": "Settings",
    "custom-variant": "Settings",
    "stream-loop-detection": "Streams",
    "looping-streams": "Streams",
    "containers": "Orchestrator",
    "provision": "Orchestrator",
    "scale": "Orchestrator",
    "gc": "Orchestrator",
    "modify_m3u": "Proxy",
    "by-label": "Orchestrator",
    "version": "System",
}


def _is_management_route(path: str) -> bool:
    if not path or not path.startswith("/"):
        return False
    if path.startswith("/api/v1"):
        return False
    if path in _MANAGEMENT_ROUTE_EXCLUDED_EXACT:
        return False
    for excluded_prefix in _MANAGEMENT_ROUTE_EXCLUDED_PREFIXES:
        if path == excluded_prefix or path.startswith(f"{excluded_prefix}/"):
            return False
    return True


def _infer_route_tag(path: str) -> str:
    segment = path.strip("/").split("/", 1)[0] if path.strip("/") else "root"
    return _TAG_BY_SEGMENT.get(segment, "Orchestrator")


def _infer_route_summary(route: APIRoute) -> str:
    if route.summary:
        return route.summary
    endpoint_name = (route.name or "route").replace("_", " ").strip()
    return endpoint_name[:1].upper() + endpoint_name[1:]


def _infer_route_description(route: APIRoute) -> str:
    if route.description:
        return route.description
    doc = (route.endpoint.__doc__ or "").strip()
    if doc:
        return doc
    method = sorted(route.methods or ["GET"])[0]
    return f"{method} {route.path}"


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalized_responses(route: APIRoute) -> Dict[int, Dict[str, str]]:
    responses: Dict[int, Dict[str, str]] = {}
    for key, value in (route.responses or {}).items():
        try:
            int_key = int(key)
        except Exception:
            continue
        if isinstance(value, dict):
            responses[int_key] = dict(value)

    responses.setdefault(200, {"description": "Successful response"})
    responses.setdefault(400, {"description": "Bad request"})
    responses.setdefault(404, {"description": "Not found"})
    responses.setdefault(500, {"description": "Internal server error"})
    return responses


def _infer_response_model(route: APIRoute):
    if route.response_model is not None:
        return route.response_model

    overrides = {
        "/health/status": HealthStatusResponse,
        "/metrics/dashboard": MetricsResponse,
        "/metrics/performance": MetricsResponse,
        "/orchestrator/status": OrchestratorStatusResponse,
        "/by-label": GenericListResponse,
        "/engines/with-metrics": GenericListResponse,
    }
    if route.path in overrides:
        return overrides[route.path]

    # Binary/plaintext response endpoints should not be constrained to JSON object models.
    if route.path in {"/metrics", "/settings/export", "/modify_m3u"}:
        return Any

    return GenericObjectResponse


def _register_v1_management_routes():
    v1_router = APIRouter(prefix="/api/v1")
    grouped_routers: Dict[str, APIRouter] = {}

    for route in list(app.routes):
        if not isinstance(route, APIRoute):
            continue

        if not _is_management_route(route.path):
            continue

        normalized_path = route.path.strip("/")
        segment = normalized_path.split("/", 1)[0] if normalized_path else "root"

        if segment == "root":
            segment_router = grouped_routers.get(segment)
            if not segment_router:
                segment_router = APIRouter(prefix="", tags=["Orchestrator"])
                grouped_routers[segment] = segment_router
            relative_path = route.path
        else:
            segment_router = grouped_routers.get(segment)
            if not segment_router:
                inferred_tag = _infer_route_tag(route.path)
                segment_router = APIRouter(prefix=f"/{segment}", tags=[inferred_tag])
                grouped_routers[segment] = segment_router

            segment_prefix = f"/{segment}"
            relative_path = route.path[len(segment_prefix):]
            if not relative_path:
                relative_path = ""

        route_tags = _dedupe_preserve_order(list(route.tags or []))

        response_model = _infer_response_model(route)
        route_name = f"v1_{route.name}_{segment}" if route.name else None

        segment_router.add_api_route(
            relative_path,
            endpoint=route.endpoint,
            methods=sorted(route.methods or ["GET"]),
            response_model=response_model,
            status_code=route.status_code,
            tags=route_tags,
            dependencies=route.dependencies,
            summary=_infer_route_summary(route),
            description=_infer_route_description(route),
            response_description=route.response_description,
            responses=_normalized_responses(route),
            deprecated=route.deprecated,
            include_in_schema=True,
            name=route_name,
            response_class=route.response_class,
            openapi_extra=route.openapi_extra,
        )

        # Keep legacy paths working for compatibility, but expose only /api/v1 in Swagger.
        route.include_in_schema = False

    for segment in sorted(grouped_routers.keys()):
        v1_router.include_router(grouped_routers[segment])

    app.include_router(v1_router)


_register_v1_management_routes()
