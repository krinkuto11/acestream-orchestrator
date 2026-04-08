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
from .core.config import cfg
from .services.autoscaler import ensure_minimum, scale_to, can_stop_engine, engine_controller
from .services.provisioner import StartRequest, start_container, stop_container, AceProvisionRequest, AceProvisionResponse, start_acestream, HOST_LABEL_HTTP, compute_current_engine_config_hash
from .services.health import sweep_idle
from .services.health_monitor import health_monitor
from .services.health_manager import health_manager
from .services.inspect import inspect_container, ContainerNotFound
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
from .services.collector import collector
from .services.event_logger import event_logger
from .services.stream_cleanup import stream_cleanup
from .services.metrics import (
    update_custom_metrics,
    observe_proxy_request,
    observe_proxy_ttfb,
    observe_proxy_egress_bytes,
)
from .services.engine_selection import select_best_engine as select_best_engine_shared
from .services.auth import require_api_key
from .services.db import engine
from .models.db_models import Base
from .services.reindex import reindex_existing
from .services.gluetun import gluetun_monitor
from .services.docker_stats import get_container_stats, get_multiple_container_stats, get_total_stats
from .services.docker_stats_collector import docker_stats_collector
from .services.cache import start_cleanup_task, stop_cleanup_task, invalidate_cache, get_cache
from .services.stream_loop_detector import stream_loop_detector
from .services.looping_streams import looping_streams_tracker
from .services.vpn_controller import vpn_controller
from .services.db_maintenance import db_maintenance_service
from .services.cache_monitoring_service import start_cache_monitoring
from .services.legacy_stream_monitoring import legacy_stream_monitoring_service
from .services.hls_segmenter import hls_segmenter_service
from .services.docker_client import get_client, docker_event_watcher
from .services.vpn_credentials import credential_manager
from .utils.wireguard_parser import parse_wireguard_conf
from .proxy.manager import ProxyManager
from .proxy.ace_api_client import AceLegacyApiClient, AceLegacyApiError
from .proxy.constants import PROXY_MODE_HTTP, PROXY_MODE_API, normalize_proxy_mode

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
        from .services.client_tracker import client_tracking_service

        client_tracking_service.prune_stale_clients(float(ttl_s))
        _CLIENT_TRACKER_LAST_PRUNE_MONOTONIC = now_monotonic


def _merge_clients_with_redis_runway(stream_key: str, tracker_clients: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Overlay tracker client rows with freshest runway telemetry from Redis.

    Stream failover tolerance uses Redis-backed runway data in StreamManager.
    During reconnect races the in-memory tracker can briefly miss rows, which
    makes UI runway appear stuck at 0.0. This reconciler keeps API output aligned
    with the source-of-truth runway telemetry.
    """
    normalized_key = str(stream_key or "").strip()
    if not normalized_key:
        return list(tracker_clients or [])

    try:
        from .proxy.manager import ProxyManager
        from .proxy.redis_keys import RedisKeys
        from .proxy.constants import ClientMetadataField

        proxy_server = ProxyManager.get_instance()
        redis_client = getattr(proxy_server, "redis_client", None)
        if not redis_client:
            return list(tracker_clients or [])

        def _to_str(value: Any) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="ignore")
            return str(value or "")

        def _to_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(_to_str(value))
            except Exception:
                return default

        merged_by_id: Dict[str, Dict[str, Any]] = {}
        passthrough_rows: List[Dict[str, Any]] = []

        for row in list(tracker_clients or []):
            item = dict(row or {})
            cid = str(item.get("client_id") or item.get("id") or "").strip()
            if cid:
                merged_by_id[cid] = item
            else:
                passthrough_rows.append(item)

        now = time.time()
        client_ids = redis_client.smembers(RedisKeys.clients(normalized_key)) or []
        for raw_client_id in client_ids:
            client_id = _to_str(raw_client_id).strip()
            if not client_id:
                continue

            client_key = RedisKeys.client_metadata(normalized_key, client_id)
            if hasattr(redis_client, "hmget"):
                values = redis_client.hmget(
                    client_key,
                    [
                        ClientMetadataField.CLIENT_RUNWAY_SECONDS,
                        ClientMetadataField.BUFFER_SECONDS_BEHIND,
                        ClientMetadataField.POSITION_SOURCE,
                        ClientMetadataField.POSITION_CONFIDENCE,
                        ClientMetadataField.POSITION_OBSERVED_AT,
                        ClientMetadataField.STATS_UPDATED_AT,
                        ClientMetadataField.LAST_ACTIVE,
                        ClientMetadataField.IP_ADDRESS,
                        ClientMetadataField.USER_AGENT,
                    ],
                )
            else:
                values = [
                    redis_client.hget(client_key, ClientMetadataField.CLIENT_RUNWAY_SECONDS),
                    redis_client.hget(client_key, ClientMetadataField.BUFFER_SECONDS_BEHIND),
                    redis_client.hget(client_key, ClientMetadataField.POSITION_SOURCE),
                    redis_client.hget(client_key, ClientMetadataField.POSITION_CONFIDENCE),
                    redis_client.hget(client_key, ClientMetadataField.POSITION_OBSERVED_AT),
                    redis_client.hget(client_key, ClientMetadataField.STATS_UPDATED_AT),
                    redis_client.hget(client_key, ClientMetadataField.LAST_ACTIVE),
                    redis_client.hget(client_key, ClientMetadataField.IP_ADDRESS),
                    redis_client.hget(client_key, ClientMetadataField.USER_AGENT),
                ]

            if not values:
                continue

            (
                runway_raw,
                legacy_runway_raw,
                source_raw,
                confidence_raw,
                observed_raw,
                stats_updated_raw,
                last_active_raw,
                ip_raw,
                ua_raw,
            ) = values

            runway = _to_float(runway_raw, default=float("nan"))
            if runway != runway:
                runway = _to_float(legacy_runway_raw, default=float("nan"))
            if runway != runway:
                continue
            runway = max(0.0, runway)

            observed_at = _to_float(observed_raw, default=0.0)
            if observed_at <= 0.0:
                observed_at = _to_float(stats_updated_raw, default=0.0)
            if observed_at <= 0.0:
                observed_at = now

            row = dict(merged_by_id.get(client_id) or {
                "id": client_id,
                "client_id": client_id,
                "stream_id": normalized_key,
                "protocol": "TS",
                "type": "TS",
                "ip_address": _to_str(ip_raw) or "unknown",
                "ip": _to_str(ip_raw) or "unknown",
                "user_agent": _to_str(ua_raw) or "unknown",
                "ua": _to_str(ua_raw) or "unknown",
                "connected_at": observed_at,
                "last_active": observed_at,
            })

            existing_observed = _to_float(row.get("position_observed_at"), default=0.0)
            existing_runway = _to_float(row.get("client_runway_seconds"), default=0.0)

            if observed_at >= existing_observed or existing_runway <= 0.0:
                row["client_runway_seconds"] = runway
                row["buffer_seconds_behind"] = runway
                row["position_source"] = _to_str(source_raw) or str(row.get("position_source") or "")
                row["position_confidence"] = max(0.0, min(1.0, _to_float(confidence_raw, default=0.0)))
                row["position_observed_at"] = observed_at

            last_active = _to_float(last_active_raw, default=0.0)
            if last_active > 0.0:
                row["last_active"] = max(last_active, _to_float(row.get("last_active"), default=last_active))

            if not row.get("ip_address"):
                row["ip_address"] = _to_str(ip_raw) or "unknown"
                row["ip"] = row["ip_address"]
            if not row.get("user_agent"):
                row["user_agent"] = _to_str(ua_raw) or "unknown"
                row["ua"] = row["user_agent"]

            merged_by_id[client_id] = row

        merged_rows = list(merged_by_id.values()) + passthrough_rows
        merged_rows.sort(key=lambda item: float(item.get("last_active") or 0.0), reverse=True)
        return merged_rows
    except Exception:
        return list(tracker_clients or [])


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
    engine_docker_stats = docker_stats_collector.get_all_stats()

    total_peers = 0
    total_speed_down = 0
    total_speed_up = 0
    for stream in streams:
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

def _init_proxy_server():
    """Initialize ProxyServer and HLSProxyServer in background thread during startup.
    
    This prevents blocking when /proxy/streams/{stream_key}/clients or /ace/getstream
    endpoints are called from the panel while streams are active. Lazy initialization
    of these singletons connects to Redis and sets up data structures, which can block
    HTTP responses in single-worker uvicorn mode.
    """
    try:
        from .proxy.server import ProxyServer
        ProxyServer.get_instance()
        logger.info("ProxyServer pre-initialized during startup")
    except Exception as e:
        logger.warning(f"Failed to pre-initialize ProxyServer: {e}")
    
    try:
        from .proxy.hls_proxy import HLSProxyServer
        HLSProxyServer.get_instance()
        logger.info("HLSProxyServer pre-initialized during startup")
    except Exception as e:
        logger.warning(f"Failed to pre-initialize HLSProxyServer: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - Ensure clean start (dry run)
    Base.metadata.create_all(bind=engine)

    from .services.config_migrator import migrate_legacy_json_configs
    from .services.settings_persistence import SettingsPersistence

    migration_result = migrate_legacy_json_configs()
    SettingsPersistence.initialize_cache(force_reload=True)
    if migration_result.get("renamed_files"):
        logger.info("Legacy settings migrated to DB and renamed: %s", migration_result.get("renamed_files"))
    elif migration_result.get("seeded_defaults"):
        logger.info("Runtime settings initialized with a new default row (no legacy JSON files found)")

    cleanup_on_shutdown()  # Clean any existing state and containers after DB is ready
    
    # Load global engine customization early to ensure provisioning uses persisted values.
    from .services.engine_config import detect_platform, load_config as load_engine_config
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
    
    # Load persisted settings (Proxy and Loop Detection)
    from .proxy.config_helper import Config as ProxyConfig
    
    _loaded_live_edge_from_proxy = False

    # Load proxy settings
    try:
        proxy_settings = SettingsPersistence.load_proxy_config()
        if proxy_settings:
            logger.debug("Loading persisted proxy settings")
            if 'initial_data_wait_timeout' in proxy_settings:
                ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT = proxy_settings['initial_data_wait_timeout']
            if 'initial_data_check_interval' in proxy_settings:
                ProxyConfig.INITIAL_DATA_CHECK_INTERVAL = proxy_settings['initial_data_check_interval']
            if 'no_data_timeout_checks' in proxy_settings:
                ProxyConfig.NO_DATA_TIMEOUT_CHECKS = proxy_settings['no_data_timeout_checks']
            if 'no_data_check_interval' in proxy_settings:
                ProxyConfig.NO_DATA_CHECK_INTERVAL = proxy_settings['no_data_check_interval']
            if 'connection_timeout' in proxy_settings:
                ProxyConfig.CONNECTION_TIMEOUT = proxy_settings['connection_timeout']
            if 'upstream_connect_timeout' in proxy_settings:
                ProxyConfig.UPSTREAM_CONNECT_TIMEOUT = proxy_settings['upstream_connect_timeout']
            if 'upstream_read_timeout' in proxy_settings:
                ProxyConfig.UPSTREAM_READ_TIMEOUT = proxy_settings['upstream_read_timeout']
            if 'stream_timeout' in proxy_settings:
                ProxyConfig.STREAM_TIMEOUT = proxy_settings['stream_timeout']
            if 'channel_shutdown_delay' in proxy_settings:
                ProxyConfig.CHANNEL_SHUTDOWN_DELAY = proxy_settings['channel_shutdown_delay']
            if 'proxy_prebuffer_seconds' in proxy_settings:
                ProxyConfig.PROXY_PREBUFFER_SECONDS = max(0, int(proxy_settings['proxy_prebuffer_seconds']))
            if 'max_streams_per_engine' in proxy_settings:
                cfg.MAX_STREAMS_PER_ENGINE = proxy_settings['max_streams_per_engine']
            if 'stream_mode' in proxy_settings:
                # Validate stream_mode before loading
                mode = proxy_settings['stream_mode']
                ProxyConfig.STREAM_MODE = mode
            if 'control_mode' in proxy_settings:
                ProxyConfig.CONTROL_MODE = _resolve_control_mode(proxy_settings['control_mode'])
            if 'legacy_api_preflight_tier' in proxy_settings:
                tier = str(proxy_settings['legacy_api_preflight_tier']).strip().lower()
                if tier in ['light', 'deep']:
                    ProxyConfig.LEGACY_API_PREFLIGHT_TIER = tier
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
    
    # Initialize looping streams tracker with configured retention
    looping_streams_tracker.set_retention_minutes(cfg.STREAM_LOOP_RETENTION_MINUTES)
    
    state.set_desired_replica_count(cfg.MIN_REPLICAS)
    target_config_hash = compute_current_engine_config_hash()
    target_config = state.set_target_engine_config(target_config_hash)
    logger.info(
        f"Initialized desired replicas={cfg.MIN_REPLICAS}, config_hash={target_config['config_hash']}, generation={target_config['generation']}"
    )

    await docker_event_watcher.start()
    await engine_controller.start()

    if vpn_controller_enabled:
        await vpn_controller.start()
    else:
        logger.info("Dynamic VPN controller disabled in settings")
    
    # Initialize ProxyServer in background to avoid blocking later API calls
    init_thread = threading.Thread(target=_init_proxy_server, daemon=True, name="ProxyServer-Init")
    init_thread.start()
    # Note: We don't wait for ProxyServer initialization to complete because:
    # 1. The app won't receive HTTP requests until lifespan completes
    # 2. By the time panel loads, initialization should be done (happens in parallel)
    # 3. ProxyServer.get_instance() is thread-safe (singleton pattern)
    
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
    from .services.engine_cache_manager import engine_cache_manager
    if engine_cache_manager.is_enabled():
        asyncio.create_task(engine_cache_manager.start_pruner())
        asyncio.create_task(start_cache_monitoring())
        logger.info("Engine cache pruner and monitoring started")
    
    reindex_existing()  # Final reindex to ensure all containers are properly tracked
    
    # Start cache cleanup task
    await start_cleanup_task(interval=60)
    logger.info("Cache service started")
    
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
    await legacy_stream_monitoring_service.stop_all()  # Stop legacy monitor sessions
    await stop_cleanup_task()  # Stop cache cleanup
    
    # Final cleanup: stop engines and clear state
    cleanup_on_shutdown()
    logger.info("Orchestrator shutdown complete")
    
    # Give a small delay to ensure any pending operations complete
    await asyncio.sleep(0.1)
    
    cleanup_on_shutdown()

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


@app.get("/api/v1/docs", include_in_schema=False)
def get_v1_swagger_docs():
    """Serve Swagger UI under the versioned API namespace."""
    return get_swagger_ui_html(
        openapi_url="/api/v1/openapi.json",
        title=f"{app.title} - Swagger UI (API v1)",
    )


@app.get("/api/v1/redoc", include_in_schema=False)
def get_v1_redoc_docs():
    """Serve ReDoc under the versioned API namespace."""
    return get_redoc_html(
        openapi_url="/api/v1/openapi.json",
        title=f"{app.title} - ReDoc (API v1)",
    )

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

# Favicon routes - serve favicon files at root level for browser default requests
# Try both built panel and source panel-react/public directories
def serve_favicon(filename: str):
    """Helper function to serve favicon files from panel directory or fallback to source."""
    # Try built panel directory first
    if os.path.exists(panel_dir) and os.path.isdir(panel_dir):
        favicon_path = os.path.join(panel_dir, filename)
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path)
    
    # Fallback to panel-react/public for development
    panel_react_public = "app/static/panel-react/public"
    if os.path.exists(panel_react_public):
        favicon_path = os.path.join(panel_react_public, filename)
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path)
    
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/favicon.ico")
async def get_favicon_ico():
    """Serve favicon.ico at root level."""
    return serve_favicon("favicon.ico")

@app.get("/favicon.svg")
async def get_favicon_svg():
    """Serve favicon.svg at root level."""
    return serve_favicon("favicon.svg")

@app.get("/favicon-96x96.png")
async def get_favicon_96():
    """Serve favicon-96x96.png at root level."""
    return serve_favicon("favicon-96x96.png")

@app.get("/favicon-96x96-dark.png")
async def get_favicon_96_dark():
    """Serve favicon-96x96-dark.png at root level."""
    return serve_favicon("favicon-96x96-dark.png")

@app.get("/apple-touch-icon.png")
async def get_apple_touch_icon():
    """Serve apple-touch-icon.png at root level."""
    return serve_favicon("apple-touch-icon.png")

# Version endpoint
@app.get("/version")
def get_version():
    """Get the current version of the orchestrator."""
    return {
        "version": __version__,
        "title": "AceStream Orchestrator"
    }

# Prometheus metrics endpoint with custom aggregated metrics
from starlette.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

@app.get("/metrics")
def get_metrics():
    """
    Prometheus metrics endpoint with custom aggregated metrics.
    Updates aggregated metrics before serving Prometheus format.
    """
    # Update custom metrics with current aggregated data
    update_custom_metrics()
    
    # Generate and return Prometheus metrics
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/metrics/dashboard")
def get_dashboard_metrics_snapshot(
    window_seconds: int = Query(cfg.DASHBOARD_DEFAULT_WINDOW_S, ge=60, le=604800),
    max_points: int = Query(360, ge=30, le=2000),
):
    """Get structured advanced metrics for the pane-based dashboard."""
    return update_custom_metrics(window_seconds=window_seconds, max_points=max_points)

@app.get("/metrics/performance")
def get_performance_metrics(
    operation: Optional[str] = Query(None, description="Filter by operation name"),
    window: Optional[int] = Query(None, description="Time window in seconds")
):
    """
    Get performance metrics for system operations.
    
    Shows timing statistics (avg, p50, p95, p99) for key operations:
    - hls_manifest_generation: HLS manifest creation time
    - hls_segment_fetch: HLS segment download time
    - docker_stats_collection: Docker stats batch collection time
    - stream_event_handling: Event handler processing time
    """
    from .services.performance_metrics import performance_metrics
    
    if operation:
        stats = {operation: performance_metrics.get_stats(operation, window)}
    else:
        stats = performance_metrics.get_all_stats(window)
    
    return {
        "window_seconds": window or "all",
        "operations": stats
    }

# Provisioning
@app.post("/provision", dependencies=[Depends(require_api_key)])
def provision(req: StartRequest):
    result = start_container(req)
    # Log engine provisioning
    event_logger.log_event(
        event_type="engine",
        category="created",
        message=f"Engine provisioned: {result.get('container_id', 'unknown')[:12]}",
        details={"image": req.image, "labels": req.labels or {}},
        container_id=result.get("container_id")
    )
    return result

@app.post("/provision/acestream", response_model=AceProvisionResponse, dependencies=[Depends(require_api_key)])
def provision_acestream(req: AceProvisionRequest):
    # Check provisioning status before attempting
    from .services.circuit_breaker import circuit_breaker_manager
    
    vpn_status_check = get_vpn_status()
    circuit_breaker_status = circuit_breaker_manager.get_status()
    
    # Build detailed error response if provisioning is blocked
    if vpn_status_check.get("enabled", False) and not vpn_status_check.get("connected", False):
        logger.error("Provisioning blocked: VPN not connected")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "provisioning_blocked",
                "code": "vpn_disconnected",
                "message": "VPN connection is required but currently disconnected",
                "recovery_eta_seconds": 60,
                "can_retry": True,
                "should_wait": True
            }
        )
    
    if circuit_breaker_status.get("general", {}).get("state") != "closed":
        cb_state = circuit_breaker_status.get("general", {}).get("state")
        recovery_timeout = circuit_breaker_status.get("general", {}).get("recovery_timeout", 300)
        last_failure = circuit_breaker_status.get("general", {}).get("last_failure_time")
        
        recovery_eta = recovery_timeout
        if last_failure:
            try:
                last_failure_dt = datetime.fromisoformat(last_failure.replace('Z', '+00:00'))
                elapsed = (datetime.now(timezone.utc) - last_failure_dt).total_seconds()
                recovery_eta = max(0, int(recovery_timeout - elapsed))
            except:
                pass
        
        logger.error(f"Provisioning blocked: Circuit breaker is {cb_state}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "provisioning_blocked",
                "code": "circuit_breaker",
                "message": f"Circuit breaker is {cb_state} due to repeated failures",
                "recovery_eta_seconds": recovery_eta,
                "can_retry": cb_state == "half_open",
                "should_wait": True
            }
        )
    
    try:
        response = start_acestream(req)
    except RuntimeError as e:
        error_msg = str(e)
        # Provide clear error messages for common failure scenarios
        if "Gluetun" in error_msg or "VPN" in error_msg:
            logger.error(f"Provisioning failed due to VPN issue: {error_msg}")
            raise HTTPException(
                status_code=503, 
                detail={
                    "error": "provisioning_failed",
                    "code": "vpn_error",
                    "message": f"VPN error during provisioning: {error_msg}",
                    "recovery_eta_seconds": 60,
                    "can_retry": True,
                    "should_wait": True
                }
            )
        elif "circuit breaker" in error_msg.lower():
            logger.error(f"Provisioning failed due to circuit breaker: {error_msg}")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "provisioning_blocked",
                    "code": "circuit_breaker",
                    "message": error_msg,
                    "recovery_eta_seconds": 300,
                    "can_retry": False,
                    "should_wait": True
                }
            )
        else:
            logger.error(f"Provisioning failed: {error_msg}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "provisioning_failed",
                    "code": "general_error",
                    "message": f"Failed to provision engine: {error_msg}",
                    "recovery_eta_seconds": None,
                    "can_retry": False,
                    "should_wait": False
                }
            )
    
    # Immediately add the new engine to state so it's available to proxies
    # This ensures the engine appears in /engines endpoint right after provisioning
    try:
        reindex_existing()
        logger.info(f"Reindexed after provisioning engine {response.container_id[:12]}")
    except Exception as e:
        logger.error(f"Failed to reindex after provisioning: {e}")
    
    # Invalidate cache after provisioning
    invalidate_cache("orchestrator:status")
    # Note: docker_stats_collector will automatically detect and collect stats for new engines
    
    # Log successful engine provisioning
    event_logger.log_event(
        event_type="engine",
        category="created",
        message=f"AceStream engine provisioned on port {response.host_http_port}",
        details={
            "image": req.image or "default",
            "host_http_port": response.host_http_port,
            "container_http_port": response.container_http_port,
            "labels": req.labels or {}
        },
        container_id=response.container_id
    )
    
    return response

@app.post("/scale/{demand}", dependencies=[Depends(require_api_key)])
def scale(demand: int):
    scale_to(demand)
    return {"scaled_to": demand}

@app.post("/gc", dependencies=[Depends(require_api_key)])
def garbage_collect():
    sweep_idle()
    return {"status": "ok"}

@app.delete("/containers/{container_id}", dependencies=[Depends(require_api_key)])
def delete(container_id: str):
    # Log engine deletion
    event_logger.log_event(
        event_type="engine",
        category="deleted",
        message=f"Engine deleted: {container_id[:12]}",
        container_id=container_id
    )
    stop_container(container_id)
    
    # Invalidate cache after stopping container
    invalidate_cache("orchestrator:status")
    # Note: docker_stats_collector will automatically detect removed engines and stop collecting their stats
    
    return {"deleted": container_id}

@app.get("/containers/{container_id}")
def get_container(container_id: str):
    try:
        return inspect_container(container_id)
    except ContainerNotFound:
        raise HTTPException(status_code=404, detail="container not found")


def _fetch_container_logs_payload(
    container_id: str,
    *,
    tail: int,
    since_seconds: Optional[int],
    timestamps: bool,
) -> Dict[str, Any]:
    client = get_client(timeout=20)
    container = client.containers.get(container_id)

    since = None
    if since_seconds is not None:
        since = int(time.time()) - since_seconds

    logs_raw = container.logs(
        stdout=True,
        stderr=True,
        tail=tail,
        since=since,
        timestamps=timestamps,
    )

    logs_text = logs_raw.decode("utf-8", errors="replace") if isinstance(logs_raw, (bytes, bytearray)) else str(logs_raw)

    return {
        "container_id": container_id,
        "tail": tail,
        "since_seconds": since_seconds,
        "timestamps": timestamps,
        "logs": logs_text,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/containers/{container_id}/logs", dependencies=[Depends(require_api_key)])
def get_container_logs(
    container_id: str,
    tail: int = Query(200, ge=1, le=2000, description="Maximum number of recent log lines"),
    since_seconds: Optional[int] = Query(
        None,
        ge=1,
        le=86400,
        description="Return logs newer than this many seconds",
    ),
    timestamps: bool = Query(False, description="Include Docker timestamps in each log line"),
):
    """Get recent Docker logs for a container.

    This endpoint is intended for live tailing in the panel by polling at short intervals.
    """
    try:
        return _fetch_container_logs_payload(
            container_id,
            tail=tail,
            since_seconds=since_seconds,
            timestamps=timestamps,
        )
    except NotFound:
        raise HTTPException(status_code=404, detail="container not found")
    except Exception as exc:
        logger.error(f"Failed to fetch logs for container {container_id[:12]}: {exc}")
        raise HTTPException(status_code=500, detail=f"failed_to_fetch_logs: {exc}")


@app.get("/api/v1/containers/{container_id}/logs/stream")
async def stream_container_logs(
    request: Request,
    container_id: str,
    tail: int = Query(300, ge=1, le=2000),
    since_seconds: Optional[int] = Query(1200, ge=1, le=86400),
    timestamps: bool = Query(False),
    interval_seconds: float = Query(2.5, ge=0.5, le=10.0),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for near-real-time container logs updates."""
    _validate_sse_api_key(request, api_key)

    async def _event_generator():
        last_logs_text: Optional[str] = None

        while True:
            if await request.is_disconnected():
                break

            try:
                payload = _fetch_container_logs_payload(
                    container_id,
                    tail=tail,
                    since_seconds=since_seconds,
                    timestamps=timestamps,
                )
            except NotFound:
                message = {
                    "type": "container_logs_error",
                    "payload": {
                        "container_id": container_id,
                        "status_code": 404,
                        "detail": "container not found",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="container_logs_error")
                break
            except Exception as exc:
                logger.debug(f"Logs SSE fetch failed for {container_id[:12]}: {exc}")
                message = {
                    "type": "container_logs_error",
                    "payload": {
                        "container_id": container_id,
                        "status_code": 500,
                        "detail": f"failed_to_fetch_logs: {exc}",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="container_logs_error")
                await asyncio.sleep(interval_seconds)
                continue

            logs_text = str(payload.get("logs") or "")
            if logs_text != last_logs_text:
                last_logs_text = logs_text
                message = {
                    "type": "container_logs_snapshot",
                    "payload": jsonable_encoder(payload),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="container_logs_snapshot")
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(interval_seconds)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)

# Events
@app.post("/events/stream_started", response_model=StreamState, dependencies=[Depends(require_api_key)])
def ev_stream_started(evt: StreamStartedEvent):
    result = state.on_stream_started(evt)
    # Log stream start event
    event_logger.log_event(
        event_type="stream",
        category="started",
        message=f"Stream started: {evt.stream.key_type}={evt.stream.key[:16]}...",
        details={
            "key_type": evt.stream.key_type,
            "key": evt.stream.key,
            "engine_port": evt.engine.port,
            "is_live": bool(evt.session.is_live)
        },
        container_id=evt.container_id,
        stream_id=result.id
    )
    return result

@app.post("/events/stream_ended", dependencies=[Depends(require_api_key)])
def ev_stream_ended(evt: StreamEndedEvent, bg: BackgroundTasks):
    st = state.on_stream_ended(evt)
    # Log stream end event
    if st:
        event_logger.log_event(
            event_type="stream",
            category="ended",
            message=f"Stream ended: {st.id[:16]}... (reason: {evt.reason or 'unknown'})",
            details={
                "reason": evt.reason,
                "key_type": st.key_type,
                "key": st.key
            },
            container_id=st.container_id,
            stream_id=st.id
        )
    
    if cfg.AUTO_DELETE and st:
        def _auto():
            cid = st.container_id
            
            # For testing or immediate shutdown scenarios (grace period <= 5s), bypass grace period
            bypass_grace = cfg.ENGINE_GRACE_PERIOD_S <= 5
            
            # Check if engine can be safely stopped (respects grace period unless bypassed)
            if can_stop_engine(cid, bypass_grace_period=bypass_grace):
                stopped_container_id = None
                for i in range(3):
                    try:
                        stop_container(cid)
                        stopped_container_id = cid
                        break
                    except Exception:
                        from .services.health import list_managed
                        try:
                            for c in list_managed():
                                if (c.labels or {}).get("stream_id") == st.id:
                                    if can_stop_engine(c.id, bypass_grace_period=bypass_grace):
                                        stop_container(c.id)
                                        stopped_container_id = c.id
                                        break
                                import urllib.parse
                                pu = urllib.parse.urlparse(st.stat_url)
                                host_port = pu.port
                                if (c.labels or {}).get(HOST_LABEL_HTTP) == str(host_port):
                                    if can_stop_engine(c.id, bypass_grace_period=bypass_grace):
                                        stop_container(c.id)
                                        stopped_container_id = c.id
                                        break
                        except Exception:
                            pass
                        import time; time.sleep(1 * (i+1))
                
                # If we successfully stopped a container, update state and ensure minimum replicas
                if stopped_container_id:
                    # Remove the engine from state
                    state.remove_engine(stopped_container_id)
                    # Ensure minimum number of replicas are maintained
                    from .services.autoscaler import ensure_minimum
                    ensure_minimum()
            else:
                # Engine cannot be stopped - it may be protected (MIN_REPLICAS/MIN_FREE_REPLICAS) 
                # or in grace period. Only log at debug level to avoid spam for protected engines.
                logger.debug(f"Engine {cid[:12]} cannot be stopped, deferring shutdown")
                
        bg.add_task(_auto)
    return {"updated": bool(st), "stream": st}


@app.get("/api/v1/events/stream")
async def stream_realtime_events(request: Request, api_key: Optional[str] = Query(None)):
    """Server-Sent Events endpoint for low-latency dashboard updates."""
    _validate_sse_api_key(request, api_key)

    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    loop = asyncio.get_running_loop()

    def _on_state_change(event: Dict[str, object]):
        def _enqueue():
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(dict(event))

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_enqueue)

    unsubscribe = state.subscribe_state_changes(_on_state_change)

    async def _event_generator():
        try:
            initial_payload = {
                "type": "full_sync",
                "payload": _build_sse_payload(),
                "meta": {
                    "reason": "initial_sync",
                    "seq": state.get_state_change_seq(),
                },
            }
            yield _format_sse_message(
                initial_payload,
                event_name="full_sync",
                event_id=str(state.get_state_change_seq()),
            )

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                message = {
                    "type": "full_sync",
                    "payload": _build_sse_payload(),
                    "meta": {
                        "reason": event.get("change_type"),
                        "seq": event.get("seq"),
                        "at": event.get("at"),
                    },
                }
                yield _format_sse_message(
                    message,
                    event_name="full_sync",
                    event_id=str(event.get("seq") or ""),
                )
        finally:
            unsubscribe()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@app.get("/api/v1/events/live")
async def stream_live_event_log(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    event_type: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for event log updates used by the Events page."""
    _validate_sse_api_key(request, api_key)

    queue: asyncio.Queue = asyncio.Queue(maxsize=16)
    loop = asyncio.get_running_loop()

    def _on_event(event: Dict[str, object]):
        if event_type and str(event.get("event_type") or "") != event_type:
            return

        def _enqueue():
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(dict(event))

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_enqueue)

    unsubscribe = event_logger.subscribe(_on_event)

    async def _event_generator():
        try:
            initial_payload = {
                "type": "events_snapshot",
                "payload": _build_events_sse_payload(limit=limit, event_type=event_type),
                "meta": {"reason": "initial_sync"},
            }
            yield _format_sse_message(initial_payload, event_name="events_snapshot")

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                message = {
                    "type": "events_snapshot",
                    "payload": _build_events_sse_payload(limit=limit, event_type=event_type),
                    "meta": {
                        "reason": "event_logged",
                        "seq": event.get("seq"),
                        "event_type": event.get("event_type"),
                    },
                }
                yield _format_sse_message(
                    message,
                    event_name="events_snapshot",
                    event_id=str(event.get("seq") or ""),
                )
        finally:
            unsubscribe()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@app.get("/api/v1/metrics/stream")
async def stream_live_metrics(
    request: Request,
    window_seconds: int = Query(cfg.DASHBOARD_DEFAULT_WINDOW_S, ge=60, le=604800),
    max_points: int = Query(360, ge=30, le=2000),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for dashboard metrics snapshots."""
    _validate_sse_api_key(request, api_key)

    async def _event_generator():
        while True:
            if await request.is_disconnected():
                break

            payload = update_custom_metrics(window_seconds=window_seconds, max_points=max_points)
            message = {
                "type": "metrics_snapshot",
                "payload": jsonable_encoder(payload),
                "meta": {
                    "window_seconds": window_seconds,
                    "max_points": max_points,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            yield _format_sse_message(message, event_name="metrics_snapshot")

            await asyncio.sleep(2.0)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@app.get("/api/v1/ace/monitor/legacy/stream")
async def stream_legacy_monitor_sessions(
    request: Request,
    include_recent_status: bool = Query(
        True,
        description="Include recent_status history in monitor payloads.",
    ),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for legacy monitor session updates."""
    _validate_sse_api_key(request, api_key)

    queue: asyncio.Queue = asyncio.Queue(maxsize=24)
    loop = asyncio.get_running_loop()

    def _shape_event_payload(event: Dict[str, object]) -> Dict[str, object]:
        payload = dict(event or {})
        monitor_payload = payload.get("monitor")
        if include_recent_status or not isinstance(monitor_payload, dict):
            return payload

        compact_monitor = dict(monitor_payload)
        compact_monitor.pop("recent_status", None)
        payload["monitor"] = compact_monitor
        return payload

    def _on_update(event: Dict[str, object]):
        shaped = _shape_event_payload(event)

        def _enqueue():
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(shaped)

        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_enqueue)

    unsubscribe = legacy_stream_monitoring_service.subscribe_updates(_on_update)

    async def _event_generator():
        try:
            initial_items = await legacy_stream_monitoring_service.list_monitors(
                include_recent_status=include_recent_status,
            )
            initial_payload = {
                "type": "legacy_monitor_snapshot",
                "payload": {
                    "items": jsonable_encoder(initial_items),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "meta": {"reason": "initial_sync"},
            }
            yield _format_sse_message(initial_payload, event_name="legacy_monitor_snapshot")

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                message = {
                    "type": "legacy_monitor_event",
                    "payload": jsonable_encoder(event),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(
                    message,
                    event_name="legacy_monitor_event",
                    event_id=str(event.get("seq") or ""),
                )
        finally:
            unsubscribe()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@app.get("/api/v1/custom-variant/reprovision/status/stream")
@app.get("/api/v1/settings/engine/reprovision/status/stream")
async def stream_reprovision_status(
    request: Request,
    interval_seconds: float = Query(1.0, ge=0.5, le=10.0),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint for engine reprovision status."""
    _validate_sse_api_key(request, api_key)

    async def _event_generator():
        last_digest: Optional[str] = None
        while True:
            if await request.is_disconnected():
                break

            payload = jsonable_encoder(get_reprovision_status())
            digest = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)

            if digest != last_digest:
                last_digest = digest
                message = {
                    "type": "reprovision_status",
                    "payload": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="reprovision_status")
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(interval_seconds)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)


@app.get("/api/v1/vpn/leases/stream")
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


@app.get("/api/v1/streams/{stream_id}/details/stream")
async def stream_stream_details(
    request: Request,
    stream_id: str,
    since_seconds: int = Query(3600, ge=60, le=86400),
    interval_seconds: float = Query(2.0, ge=0.5, le=10.0),
    api_key: Optional[str] = Query(None),
):
    """SSE endpoint that streams detail payloads for a single stream row."""
    _validate_sse_api_key(request, api_key)

    from .services.client_tracker import client_tracking_service
    from .proxy.config_helper import Config as ProxyConfig

    async def _event_generator():
        last_digest: Optional[str] = None
        cached_extended_stats: Optional[Dict[str, Any]] = None
        next_extended_refresh_monotonic = 0.0
        extended_refresh_task: Optional[asyncio.Task] = None

        while True:
            if await request.is_disconnected():
                break

            stream_state = state.get_stream(stream_id)
            if not stream_state:
                message = {
                    "type": "stream_details_error",
                    "payload": {
                        "stream_id": stream_id,
                        "status_code": 404,
                        "detail": "stream_not_found",
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(message, event_name="stream_details_error")
                break

            stat_url = str(getattr(stream_state, "stat_url", "") or "").strip()
            supports_extended_stats = bool(stat_url)

            now_monotonic = time.monotonic()
            if supports_extended_stats and now_monotonic >= next_extended_refresh_monotonic and (extended_refresh_task is None or extended_refresh_task.done()):
                extended_refresh_task = asyncio.create_task(get_stream_extended_stats(stream_id))
                next_extended_refresh_monotonic = now_monotonic + max(10.0, interval_seconds * 2.0)
            elif not supports_extended_stats:
                if cached_extended_stats is None:
                    cached_extended_stats = {
                        "available": False,
                        "reason": "extended_stats_disabled_in_api_mode",
                    }
                if extended_refresh_task is not None and not extended_refresh_task.done():
                    extended_refresh_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await extended_refresh_task
                    extended_refresh_task = None

            if extended_refresh_task is not None and extended_refresh_task.done():
                try:
                    cached_extended_stats = extended_refresh_task.result()
                except HTTPException as exc:
                    if exc.status_code != 404:
                        logger.debug(f"Extended stats refresh failed for stream {stream_id[:12]}: {exc.detail}")
                except Exception as exc:
                    logger.debug(f"Extended stats refresh failed for stream {stream_id[:12]}: {exc}")
                finally:
                    extended_refresh_task = None

            cutoff = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
            stream_stats = [snap for snap in state.get_stream_stats(stream_id) if snap.ts >= cutoff]

            clients: List[Dict[str, Any]] = []
            stream_key = str(getattr(stream_state, "key", "") or "")
            if stream_key:
                with suppress(Exception):
                    _prune_client_tracker_if_due(ttl_s=float(ProxyConfig.CLIENT_RECORD_TTL), min_interval_s=3.0)
                    clients = client_tracking_service.get_stream_clients(stream_key)
                    clients = _merge_clients_with_redis_runway(stream_key, clients)

            payload = jsonable_encoder(
                {
                    "stream_id": stream_id,
                    "status": getattr(stream_state, "status", None),
                    "stats": stream_stats,
                    "extended_stats": cached_extended_stats,
                    "clients": clients,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

            digest = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
            if digest != last_digest:
                last_digest = digest
                message = {
                    "type": "stream_details_snapshot",
                    "payload": payload,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                yield _format_sse_message(
                    message,
                    event_name="stream_details_snapshot",
                    event_id=str(int(time.time() * 1000)),
                )
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(interval_seconds)

        if extended_refresh_task is not None and not extended_refresh_task.done():
            extended_refresh_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await extended_refresh_task

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_event_generator(), media_type="text/event-stream", headers=headers)

# Read APIs
@app.get("/engines", response_model=List[EngineState])
def get_engines():
    """Get all engines with Docker verification and VPN health filtering."""
    engines = state.list_engines()

    def _to_int(value: Any) -> int:
        if value is None:
            return 0
        try:
            text = str(value).strip()
            if text == "":
                return 0
            return int(float(text))
        except Exception:
            return 0

    def _build_engine_runtime_metrics() -> Dict[str, Dict[str, int]]:
        from .services.state import ACTIVE_MONITOR_SESSION_STATUSES

        metrics: Dict[str, Dict[str, int]] = {}

        def _entry(container_id: str) -> Dict[str, int]:
            return metrics.setdefault(
                container_id,
                {
                    "active_stream_count": 0,
                    "monitor_stream_count": 0,
                    "stream_peers": 0,
                    "stream_speed_down": 0,
                    "stream_speed_up": 0,
                    "monitor_peers": 0,
                    "monitor_speed_down": 0,
                    "monitor_speed_up": 0,
                },
            )

        for stream in state.list_streams_with_stats(status="started"):
            cid = stream.container_id
            item = _entry(cid)
            item["active_stream_count"] += 1
            item["stream_peers"] += _to_int(stream.peers)
            item["stream_speed_down"] += _to_int(stream.speed_down)
            item["stream_speed_up"] += _to_int(stream.speed_up)

        for monitor in state.list_monitor_sessions():
            monitor_status = str(monitor.get("status") or "").strip().lower()
            if monitor_status not in ACTIVE_MONITOR_SESSION_STATUSES:
                continue

            engine_data = monitor.get("engine") or {}
            cid = str(engine_data.get("container_id") or "").strip()
            if not cid:
                continue

            latest_status = monitor.get("latest_status") or {}
            item = _entry(cid)
            item["monitor_stream_count"] += 1
            item["monitor_peers"] += _to_int(latest_status.get("peers") or latest_status.get("http_peers"))
            item["monitor_speed_down"] += _to_int(latest_status.get("speed_down") or latest_status.get("http_speed_down"))
            item["monitor_speed_up"] += _to_int(latest_status.get("speed_up"))

        aggregated: Dict[str, Dict[str, int]] = {}
        for cid, item in metrics.items():
            aggregated[cid] = {
                "total_peers": item["stream_peers"] + item["monitor_peers"],
                "total_speed_down": item["stream_speed_down"] + item["monitor_speed_down"],
                "total_speed_up": item["stream_speed_up"] + item["monitor_speed_up"],
                "stream_count": item["active_stream_count"] + item["monitor_stream_count"],
                "monitor_stream_count": item["monitor_stream_count"],
                "monitor_speed_down": item["monitor_speed_down"],
                "monitor_speed_up": item["monitor_speed_up"],
            }

        return aggregated

    runtime_metrics = _build_engine_runtime_metrics()
    
    # For better reliability, we can optionally verify against Docker
    # but we don't want to break existing functionality
    try:
        from .services.health import list_managed
        from .services.gluetun import gluetun_monitor, get_forwarded_port_sync
        from .services.engine_info import get_engine_version_info_sync
        
        managed_containers = list_managed()
        running_containers = [c for c in managed_containers if c.status == "running"]
        running_container_ids = {c.id for c in running_containers}

        # Container start timestamp changes on restart, which invalidates version cache.
        container_started_at = {}
        for c in running_containers:
            started_at = None
            try:
                started_at = (c.attrs or {}).get("State", {}).get("StartedAt")
            except Exception:
                started_at = None
            container_started_at[c.id] = str(started_at or "unknown")
        
        # In redundant VPN mode, filter out engines assigned to unhealthy VPNs
        # This hides engines from the proxy when their VPN is down
        vpn_health_cache = {}
        
        verified_engines = []
        for engine in engines:
            # First verify the engine container is running
            if engine.container_id not in running_container_ids:
                logger.debug(f"Engine {engine.container_id[:12]} not found in Docker, but keeping in response")
                # Still include for backwards compatibility - monitoring will handle cleanup
                verified_engines.append(engine)
                continue
            
            # Filter engines by assigned VPN health when a VPN assignment exists.
            if engine.vpn_container:
                # Check VPN health (use cache to avoid repeated checks)
                if engine.vpn_container not in vpn_health_cache:
                    vpn_health_cache[engine.vpn_container] = gluetun_monitor.is_healthy(engine.vpn_container)
                
                vpn_healthy = vpn_health_cache.get(engine.vpn_container)
                
                # Only include engine if its VPN is healthy
                if vpn_healthy:
                    verified_engines.append(engine)
                else:
                    logger.debug(f"Engine {engine.container_id[:12]} filtered out - VPN '{engine.vpn_container}' is unhealthy")
            else:
                # Engine has no VPN assignment.
                verified_engines.append(engine)
        
        # Enrich engines with version info and forwarded port
        for engine in verified_engines:
            # Only set engine_variant if not already set (from labels)
            # This ensures old engines keep their original label until reprovisioned.
            if not engine.engine_variant:
                from .services.engine_config import detect_platform
                engine.engine_variant = f"global-{detect_platform()}"
            
            # Get engine version info
            try:
                version_info = get_engine_version_info_sync(
                    engine.host,
                    engine.port,
                    cache_key=engine.container_id,
                    cache_revision=container_started_at.get(engine.container_id),
                )
                if version_info:
                    engine.platform = version_info.get("platform")
                    engine.version = version_info.get("version")
                else:
                    logger.debug(f"No version info returned for engine {engine.container_id[:12]} at {engine.host}:{engine.port}")
            except Exception as e:
                logger.debug(f"Could not get version info for engine {engine.container_id[:12]} at {engine.host}:{engine.port}: {e}")
            
            # Get forwarded port for forwarded engines
            if engine.forwarded and engine.vpn_container:
                try:
                    port = get_forwarded_port_sync(engine.vpn_container)
                    if port:
                        engine.forwarded_port = port
                    else:
                        logger.debug(f"No forwarded port available for VPN {engine.vpn_container} (engine {engine.container_id[:12]})")
                except Exception as e:
                    logger.warning(f"Could not get forwarded port for engine {engine.container_id[:12]} on VPN {engine.vpn_container}: {e}")
        
        # Enrich engine payloads with runtime stream/monitor metrics.
        enriched_engines = []
        for engine in verified_engines:
            metrics = runtime_metrics.get(
                engine.container_id,
                {
                    "total_peers": 0,
                    "total_speed_down": 0,
                    "total_speed_up": 0,
                    "stream_count": 0,
                    "monitor_stream_count": 0,
                    "monitor_speed_down": 0,
                    "monitor_speed_up": 0,
                },
            )
            enriched_engines.append(engine.model_copy(update=metrics))

        # Sort engines by port number for consistent ordering
        enriched_engines.sort(key=lambda e: e.port)
        return enriched_engines
    except Exception as e:
        # If verification fails, return state as is but still sorted
        logger.debug(f"Engine verification failed for /engines endpoint: {e}")
        enriched_engines = []
        for engine in engines:
            metrics = runtime_metrics.get(
                engine.container_id,
                {
                    "total_peers": 0,
                    "total_speed_down": 0,
                    "total_speed_up": 0,
                    "stream_count": 0,
                    "monitor_stream_count": 0,
                    "monitor_speed_down": 0,
                    "monitor_speed_up": 0,
                },
            )
            enriched_engines.append(engine.model_copy(update=metrics))

        enriched_engines.sort(key=lambda e: e.port)
        return enriched_engines

@app.get("/engines/with-metrics")
def get_engines_with_metrics():
    """Get all engines with aggregated stream metrics (peers, download/upload speeds)."""
    engines = get_engines()

    # Keep compatibility with dict-based consumers.
    result = []
    for engine in engines:
        engine_dict = engine.model_dump()
        result.append(engine_dict)

    return result

@app.get("/engines/{container_id}")
def get_engine(container_id: str):
    eng = state.get_engine(container_id)
    if not eng:
        return {"error": "not found"}
    streams = state.list_streams(status="started", container_id=container_id)
    return {"engine": eng, "streams": streams}

@app.get("/engines/stats/all")
def get_all_engine_stats():
    """Get Docker stats for all engines from background collector (instant response)."""
    # Return cached stats from background collector
    stats = docker_stats_collector.get_all_stats()
    return stats

@app.get("/engines/stats/total")
def get_total_engine_stats():
    """Get aggregated Docker stats across all engines from background collector (instant response)."""
    # Get stats from background collector - no cache needed as collector maintains fresh data
    total_stats = docker_stats_collector.get_total_stats()
    return total_stats

@app.get("/engines/{container_id}/stats")
def get_engine_stats(container_id: str):
    """Get Docker stats for a specific engine from background collector (instant response)."""
    # Get stats from background collector
    stats = docker_stats_collector.get_engine_stats(container_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Container not found or stats unavailable")
    return stats

@app.get("/streams", response_model=List[StreamState])
def get_streams(status: Optional[str] = Query(None, pattern="^(started|ended)$"), container_id: Optional[str] = None):
    """Get streams. By default, returns all streams. Use status=started or status=ended to filter."""
    return state.list_streams_with_stats(status=status, container_id=container_id)

@app.get("/streams/{stream_id}/stats", response_model=List[StreamStatSnapshot])
def get_stream_stats(stream_id: str, since: Optional[datetime] = None):
    snaps = state.get_stream_stats(stream_id)
    if since:
        snaps = [x for x in snaps if x.ts >= since]
    return snaps

@app.get("/streams/{stream_id}/extended-stats")
async def get_stream_extended_stats(stream_id: str):
    """
    Get extended statistics for a stream when stat_url is available (HTTP control mode).
    For API mode streams, extended stats gathering is intentionally disabled.
    """
    from .utils.acestream_api import get_stream_extended_stats
    from .services.cache import get_cache
    
    # Check cache first to avoid hammering the AceStream engine
    cache = get_cache()
    cache_key = f"stream_extended_stats:{stream_id}"
    cached_stats = cache.get(cache_key)
    if cached_stats is not None:
        return cached_stats
    
    # Get the stream from state
    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")

    # API mode streams don't provide stat_url; skip expensive metadata probes.
    if not str(getattr(stream, "stat_url", "") or "").strip():
        unavailable = {
            "available": False,
            "reason": "extended_stats_disabled_in_api_mode",
        }
        cache.set(cache_key, unavailable, ttl=300.0)
        return unavailable

    # Additional stable cache key to share results across stream session IDs
    # for the same content (e.g. infohash|legacy-<session>). This keeps the
    # panel fast during reconnects/failovers.
    content_cache_key = f"stream_extended_stats:content:{stream.key}"
    cached_content_stats = cache.get(content_cache_key)
    if cached_content_stats is not None:
        cache.set(cache_key, cached_content_stats, ttl=3600.0)
        return cached_content_stats
    
    extended_stats = await get_stream_extended_stats(stream.stat_url)

    # Avoid noisy panel failures for streams where metadata cannot be resolved.
    if extended_stats is None:
        unavailable = {"available": False}
        # Short negative cache to avoid repeated expensive lookups while keeping
        # quick recovery when metadata becomes available.
        cache.set(cache_key, unavailable, ttl=30.0)
        cache.set(content_cache_key, unavailable, ttl=30.0)
        return unavailable
    
    # Cache the result for 1 hour (3600 seconds) since stream title/infohash rarely change
    cache.set(cache_key, extended_stats, ttl=3600.0)
    cache.set(content_cache_key, extended_stats, ttl=3600.0)
    
    return extended_stats

@app.get("/streams/{stream_id}/livepos")
async def get_stream_livepos(stream_id: str):
    """
    Get live position data for a stream from stat URL or API-mode probe.
    This returns livepos details and derived live performance metrics.
    """
    import httpx

    def _to_int(value):
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _build_livepos_response(livepos, is_live, status_payload=None):
        if not livepos:
            return {
                "has_livepos": False,
                "is_live": bool(is_live),
            }

        normalized = {
            "pos": livepos.get("pos"),
            "live_first": livepos.get("live_first") or livepos.get("first_ts") or livepos.get("first"),
            "live_last": livepos.get("live_last") or livepos.get("last_ts") or livepos.get("last"),
            "first_ts": livepos.get("first_ts") or livepos.get("first"),
            "last_ts": livepos.get("last_ts") or livepos.get("last"),
            "buffer_pieces": livepos.get("buffer_pieces"),
        }

        pos_i = _to_int(normalized.get("pos"))
        first_i = _to_int(normalized.get("live_first"))
        last_i = _to_int(normalized.get("live_last"))

        live_delay_seconds = None
        dvr_window_seconds = None
        playback_offset_seconds = None
        if pos_i is not None and last_i is not None:
            live_delay_seconds = max(0, last_i - pos_i)
        if first_i is not None and last_i is not None:
            dvr_window_seconds = max(0, last_i - first_i)
        if pos_i is not None and first_i is not None:
            playback_offset_seconds = max(0, pos_i - first_i)

        performance = {
            "live_delay_seconds": live_delay_seconds,
            "dvr_window_seconds": dvr_window_seconds,
            "playback_offset_seconds": playback_offset_seconds,
        }

        if status_payload:
            performance.update({
                "status": status_payload.get("status_text") or status_payload.get("status"),
                "peers": status_payload.get("peers"),
                "http_peers": status_payload.get("http_peers"),
                "speed_down": status_payload.get("speed_down"),
                "http_speed_down": status_payload.get("http_speed_down"),
                "speed_up": status_payload.get("speed_up"),
                "downloaded": status_payload.get("downloaded"),
                "http_downloaded": status_payload.get("http_downloaded"),
                "uploaded": status_payload.get("uploaded"),
                "total_progress": status_payload.get("total_progress"),
                "immediate_progress": status_payload.get("immediate_progress"),
            })

        return {
            "has_livepos": True,
            "is_live": bool(is_live),
            "livepos": normalized,
            "performance": performance,
        }
    
    # Get the stream from state
    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    if stream.stat_url:
        # Fetch livepos data from stat URL (HTTP mode path)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(stream.stat_url)
                response.raise_for_status()
                data = response.json()

                payload = data.get("response")
                if not payload:
                    raise HTTPException(status_code=503, detail="No response data from stat URL")

                return _build_livepos_response(
                    payload.get("livepos"),
                    payload.get("is_live", 0) == 1,
                    status_payload=payload,
                )
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch livepos for stream {stream_id}: {e}")
            raise HTTPException(status_code=503, detail=f"Failed to fetch livepos data: {str(e)}")
        except Exception as e:
            logger.error(f"Error processing livepos for stream {stream_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error processing livepos data: {str(e)}")

    # API mode path (no stat_url): ask active proxy stream manager for a probe.
    try:
        from .proxy.server import ProxyServer
        from .services.hls_segmenter import hls_segmenter_service
        from .services.legacy_stream_monitoring import legacy_stream_monitoring_service

        proxy = ProxyServer.get_instance()
        manager = proxy.stream_managers.get(stream.key) if proxy else None
        probe = None

        if manager:
            probe = await asyncio.to_thread(
                manager.collect_legacy_stats_probe,
                1,
                1.0,
                True,
            )

        if not probe:
            probe = await asyncio.to_thread(
                hls_segmenter_service.collect_legacy_stats_probe,
                stream.key,
                1,
                1.0,
                True,
            )

        if not probe:
            reusable = await legacy_stream_monitoring_service.get_reusable_session_for_content(stream.key)
            if reusable:
                probe = reusable.get("latest_status") or None

        if not probe:
            raise HTTPException(status_code=503, detail="No legacy probe data available")

        livepos = probe.get("livepos") or {}
        return _build_livepos_response(
            livepos,
            is_live=(stream.is_live is True),
            status_payload=probe,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing legacy livepos for stream {stream_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing livepos data: {str(e)}")

@app.delete("/streams/{stream_id}", dependencies=[Depends(require_api_key)])
async def stop_stream(stream_id: str):
    """
    Stop a stream by calling its command URL with method=stop.
    Then marks the stream as ended in state.
    """
    # Get the stream from state
    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    if stream.status != "started":
        raise HTTPException(status_code=400, detail=f"Stream is not active (status: {stream.status})")
    
    if not stream.command_url:
        raise HTTPException(status_code=400, detail="Stream has no command URL")
    
    # Call command URL with method=stop to stop the stream on the engine
    stop_url = f"{stream.command_url}?method=stop"
    logger.info(f"Stopping stream {stream_id} via command URL: {stop_url}")
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(stop_url)
            if response.status_code >= 300:
                logger.warning(f"Stop command returned non-success status {response.status_code} for stream {stream_id}")
    except Exception as e:
        # Log but don't fail - we'll still mark the stream as ended
        logger.warning(f"Failed to send stop command for stream {stream_id}: {e}")
    
    # Mark the stream as ended in our state
    logger.info(f"Ending stream {stream_id} (reason: manual_stop_via_api)")
    state.on_stream_ended(StreamEndedEvent(
        container_id=stream.container_id,
        stream_id=stream_id,
        reason="manual_stop_via_api"
    ))
    
    return {"message": "Stream stopped successfully", "stream_id": stream_id}

@app.post("/streams/batch-stop", dependencies=[Depends(require_api_key)])
async def batch_stop_streams(command_urls: List[str]):
    """
    Batch stop multiple streams by calling their command URLs with method=stop.
    Then marks each stream as ended in state.
    
    Request body: List of command URLs
    Returns: List of results with success/failure status for each stream
    """
    results = []
    
    # Process each command URL
    for command_url in command_urls:
        result = {
            "command_url": command_url,
            "success": False,
            "message": "",
            "stream_id": None
        }
        
        try:
            # Find the stream by command URL
            stream = None
            for s in state.list_streams():
                if s.command_url == command_url:
                    stream = s
                    break
            
            if not stream:
                result["message"] = "Stream not found"
                results.append(result)
                continue
            
            result["stream_id"] = stream.id
            
            if stream.status != "started":
                result["message"] = f"Stream is not active (status: {stream.status})"
                results.append(result)
                continue
            
            # Call command URL with method=stop to stop the stream on the engine
            stop_url = f"{command_url}?method=stop"
            logger.info(f"Batch stopping stream {stream.id} via command URL: {stop_url}")
            
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(stop_url)
                    if response.status_code >= 300:
                        logger.warning(f"Stop command returned non-success status {response.status_code} for stream {stream.id}")
            except Exception as e:
                # Log but don't fail - we'll still mark the stream as ended
                logger.warning(f"Failed to send stop command for stream {stream.id}: {e}")
            
            # Mark the stream as ended in our state
            logger.info(f"Ending stream {stream.id} (reason: batch_stop_via_api)")
            state.on_stream_ended(StreamEndedEvent(
                container_id=stream.container_id,
                stream_id=stream.id,
                reason="batch_stop_via_api"
            ))
            
            result["success"] = True
            result["message"] = "Stream stopped successfully"
            
        except Exception as e:
            logger.error(f"Error stopping stream with command URL {command_url}: {e}")
            result["message"] = f"Error: {str(e)}"
        
        results.append(result)
    
    # Count successes
    success_count = sum(1 for r in results if r["success"])
    
    return {
        "total": len(command_urls),
        "success_count": success_count,
        "failure_count": len(command_urls) - success_count,
        "results": results
    }


@app.post("/proxy/migrate-stream", dependencies=[Depends(require_api_key)])
async def migrate_proxy_stream(req: StreamMigrationRequest):
    """Trigger an on-demand proxy stream migration for TS/HLS continuity workflows."""
    stream_key = str(req.stream_key or "").strip()
    if not stream_key:
        raise HTTPException(status_code=400, detail="stream_key is required")

    old_container_id = str(req.old_container_id or "").strip() or None
    new_container_id = str(req.new_container_id or "").strip() or None

    selected_engine = None
    if new_container_id:
        selected_engine = state.get_engine(new_container_id)
        if not selected_engine:
            raise HTTPException(status_code=404, detail=f"Target engine not found: {new_container_id}")

    try:
        result = await asyncio.to_thread(
            ProxyManager.migrate_stream,
            stream_key,
            selected_engine,
            old_container_id,
        )
    except Exception as e:
        logger.exception("On-demand stream migration failed for stream_key=%s", stream_key)
        raise HTTPException(status_code=500, detail=f"stream migration failed: {e}") from e

    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail=f"unexpected migration result payload: {result!r}")

    if bool(result.get("migrated")):
        logger.info(
            "On-demand stream migration succeeded: key=%s old=%s new=%s",
            stream_key,
            str(result.get("old_container_id") or old_container_id or "unknown"),
            str(result.get("new_container_id") or "unknown"),
        )
    else:
        logger.warning(
            "On-demand stream migration returned non-migrated result: key=%s reason=%s",
            stream_key,
            str(result.get("reason") or "unknown"),
        )

    return result

# by-label
from .services.inspect import inspect_container
from .services.health import list_managed
from .services.gluetun import get_vpn_status
@app.get("/by-label", dependencies=[Depends(require_api_key)])
def by_label(key: str, value: str):
    res = []
    for c in list_managed():
        if (c.labels or {}).get(key) == value:
            try:
                res.append(inspect_container(c.id))
            except Exception:
                continue
    return res

@app.get("/vpn/status")
async def get_vpn_status_endpoint():
    """
    Get VPN (Gluetun) status information with location data (cached for 0.5 seconds).
    
    Location data (provider, country, city, region) is now obtained directly from:
    - Provider: VPN_SERVICE_PROVIDER docker environment variable
    - Location: Gluetun's /v1/publicip/ip endpoint
    """
    # Use cache to avoid expensive VPN status checks on every poll
    cache = get_cache()
    cache_key = "vpn:status"
    
    # Try to get from cache
    cached_value = cache.get(cache_key)
    if cached_value is not None:
        return cached_value
    
    # Cache miss - fetch VPN status
    vpn_status = get_vpn_status()
    
    # Cache for 0.5 seconds
    cache.set(cache_key, vpn_status, ttl=0.5)
    
    return vpn_status


@app.post("/vpn/parse-wireguard")
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


@app.get("/vpn/leases")
async def get_vpn_credential_leases():
    """Return VPN credential pool lease status used by the settings dashboard."""
    return await credential_manager.summary()

@app.get("/vpn/publicip")
def get_vpn_publicip_endpoint():
    """Get effective public IP address (VPN egress when enabled, host egress otherwise)."""
    from .services.gluetun import get_effective_public_ip
    public_ip = get_effective_public_ip()
    if public_ip:
        return {"public_ip": public_ip}
    else:
        raise HTTPException(status_code=503, detail="Unable to retrieve public IP")

@app.get("/health/status")
def get_health_status_endpoint():
    """Get detailed health status and management information."""
    return health_manager.get_health_summary()


@app.post("/health/circuit-breaker/reset", dependencies=[Depends(require_api_key)])
def reset_circuit_breaker(operation_type: Optional[str] = None):
    """Reset circuit breakers (for manual intervention)."""
    from .services.circuit_breaker import circuit_breaker_manager
    circuit_breaker_manager.force_reset(operation_type)
    return {"message": f"Circuit breaker {'for ' + operation_type if operation_type else 'all'} reset successfully"}


@app.get("/orchestrator/status")
def get_orchestrator_status():
    """
    Get comprehensive orchestrator status for proxy integration (cached for 0.5 seconds).
    This endpoint provides all the information a proxy needs to understand
    the orchestrator's current state including VPN, provisioning, and health status.
    
    Enhanced to provide detailed provisioning status with recovery guidance.
    """
    # Use cache to avoid expensive status aggregation on every poll
    cache = get_cache()
    cache_key = "orchestrator:status"
    
    # Try to get from cache
    cached_value = cache.get(cache_key)
    if cached_value is not None:
        return cached_value
    
    # Cache miss - compute orchestrator status
    from .services.replica_validator import replica_validator
    from .services.circuit_breaker import circuit_breaker_manager
    from .services.metrics import get_dashboard_snapshot
    
    # Get engine and stream counts
    engines = state.list_engines()
    active_streams = state.list_streams(status="started")
    monitor_container_ids = state.get_active_monitor_container_ids()
    
    # Get Docker container status
    docker_status = replica_validator.get_docker_container_status()
    
    # Get VPN status
    vpn_status = get_vpn_status()
    
    # Get health summary
    health_summary = health_manager.get_health_summary()
    
    # Get circuit breaker status
    circuit_breaker_status = circuit_breaker_manager.get_status()
    
    # Calculate capacity
    # Count unique engines that have active streams (not total streams)
    # Multiple streams can run on the same engine
    total_capacity = len(engines)
    engines_with_streams = len(set(stream.container_id for stream in active_streams).union(monitor_container_ids))
    used_capacity = engines_with_streams
    available_capacity = max(0, total_capacity - used_capacity)
    
    # Determine overall system status
    vpn_enabled = vpn_status.get("enabled", False)
    vpn_connected = vpn_status.get("connected", False)
    circuit_breaker_state = circuit_breaker_status.get("general", {}).get("state")
    
    # Detailed provisioning status
    can_provision = True
    blocked_reason = None
    blocked_reason_details = None
    recovery_eta = None
    
    if vpn_enabled and not vpn_connected:
        can_provision = False
        blocked_reason = "VPN not connected"
        blocked_reason_details = {
            "code": "vpn_disconnected",
            "message": "VPN connection is required but currently disconnected. Engines cannot be provisioned without VPN.",
            "recovery_eta_seconds": 60,  # VPN typically reconnects within a minute
            "can_retry": True,
            "should_wait": True  # Proxy should wait for VPN to reconnect
        }
    elif circuit_breaker_state != "closed":
        can_provision = False
        cb_info = circuit_breaker_status.get("general", {})
        recovery_timeout = cb_info.get("recovery_timeout", 300)
        last_failure = cb_info.get("last_failure_time")
        
        if last_failure:
            try:
                last_failure_dt = datetime.fromisoformat(last_failure.replace('Z', '+00:00'))
                elapsed = (datetime.now(timezone.utc) - last_failure_dt).total_seconds()
                recovery_eta = max(0, int(recovery_timeout - elapsed))
            except:
                recovery_eta = recovery_timeout
        else:
            recovery_eta = recovery_timeout
            
        blocked_reason = f"Circuit breaker is {circuit_breaker_state}"
        blocked_reason_details = {
            "code": "circuit_breaker",
            "message": f"Provisioning circuit breaker is {circuit_breaker_state} due to repeated failures. System is waiting for conditions to improve.",
            "recovery_eta_seconds": recovery_eta,
            "can_retry": False if circuit_breaker_state == "open" else True,
            "should_wait": True  # Proxy should wait for circuit breaker to close
        }
    elif docker_status['total_running'] >= cfg.MAX_REPLICAS:
        can_provision = False
        blocked_reason = "Maximum capacity reached"
        blocked_reason_details = {
            "code": "max_capacity",
            "message": f"Maximum number of engines ({cfg.MAX_REPLICAS}) already running. Wait for streams to end or increase MAX_REPLICAS.",
            "recovery_eta_seconds": cfg.ENGINE_GRACE_PERIOD_S if cfg.AUTO_DELETE else None,
            "can_retry": False,
            "should_wait": True  # Proxy should wait for engines to free up
        }
    
    # Overall system status
    if docker_status['total_running'] == 0:
        overall_status = "unavailable"
    elif not can_provision and blocked_reason_details and blocked_reason_details["code"] in ["vpn_disconnected", "circuit_breaker"]:
        overall_status = "degraded"
    else:
        overall_status = "healthy"
    
    result = {
        "status": overall_status,
        "engines": {
            "total": len(engines),
            "running": docker_status['total_running'],
            "healthy": health_summary.get("healthy_engines", 0),
            "unhealthy": health_summary.get("unhealthy_engines", 0)
        },
        "streams": {
            "active": len(active_streams),
            "total": len(state.list_streams())
        },
        "capacity": {
            "total": total_capacity,
            "used": used_capacity,
            "available": available_capacity,
            "max_replicas": cfg.MAX_REPLICAS,
            "min_replicas": cfg.MIN_REPLICAS
        },
        "vpn": {
            "enabled": vpn_enabled,
            "connected": vpn_connected,
            "health": vpn_status.get("health", "unknown"),
            "container": vpn_status.get("container"),
            "forwarded_port": vpn_status.get("forwarded_port")
        },
        "provisioning": {
            "can_provision": can_provision,
            "circuit_breaker_state": circuit_breaker_state,
            "last_failure": circuit_breaker_status.get("general", {}).get("last_failure_time"),
            "blocked_reason": blocked_reason,
            "blocked_reason_details": blocked_reason_details
        },
        "config": {
            "auto_delete": cfg.AUTO_DELETE,
            "grace_period_s": cfg.ENGINE_GRACE_PERIOD_S,
            "engine_variant": f"global-{detect_platform()}",
            "debug_mode": cfg.DEBUG_MODE
        },
        "proxy": get_dashboard_snapshot().get("proxy", {}),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Cache for 0.5 seconds to keep topology updates responsive.
    cache.set(cache_key, result, ttl=0.5)
    
    return result


@app.get("/auth/status")
def get_auth_status():
    """Return whether API-key authentication is currently enforced by the server."""
    return {
        "required": bool(cfg.API_KEY),
        "mode": "bearer" if cfg.API_KEY else "none",
    }

# Global Engine Configuration endpoints
from .services.engine_config import (
    RESTRICTED_FLAGS,
    EngineConfig,
    detect_platform,
    get_config as get_engine_config,
    reload_config as reload_engine_config,
    resolve_engine_image,
    save_config as save_engine_config,
)


@app.get("/settings/engine/config")
def get_engine_config_endpoint():
    """Get the single global engine customization payload."""
    engine_config = get_engine_config()
    if not engine_config:
        raise HTTPException(status_code=500, detail="Failed to load engine configuration")

    platform_arch = detect_platform()
    return {
        **engine_config.model_dump(mode="json"),
        "platform": platform_arch,
        "image": resolve_engine_image(platform_arch),
    }


@app.post("/settings/engine/config", dependencies=[Depends(require_api_key)])
def update_engine_config_endpoint(config: EngineConfig):
    """Update the global engine customization payload."""
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


# Compatibility wrappers for legacy custom-variant routes.
@app.get("/custom-variant/platform")
def get_platform_info():
    return {
        "platform": detect_platform(),
        "supported_platforms": ["amd64", "arm32", "arm64"],
    }


@app.get("/custom-variant/config")
def get_custom_variant_config():
    return get_engine_config_endpoint()


@app.post("/custom-variant/config", dependencies=[Depends(require_api_key)])
def update_custom_variant_config(config: EngineConfig):
    return update_engine_config_endpoint(config)

@app.get("/custom-variant/reprovision/status")
def get_reprovision_status():
    """Compute declarative rollout status from desired-vs-actual engine hashes."""
    target = state.get_target_engine_config()
    target_hash = str(target.get("config_hash") or "")
    desired = max(0, int(state.get_desired_replica_count()))
    engines = state.list_engines()

    engines_with_target_hash = sum(
        1
        for engine in engines
        if str((engine.labels or {}).get("acestream.config_hash") or "") == target_hash
    )

    actual = len(engines)
    outdated_running = max(0, actual - engines_with_target_hash)
    in_progress = engines_with_target_hash < desired or actual > desired

    if in_progress and actual > desired:
        current_phase = "stopping"
    elif in_progress:
        current_phase = "provisioning"
    else:
        current_phase = "complete"

    return {
        "in_progress": in_progress,
        "status": "in_progress" if in_progress else "idle",
        "message": (
            "Rolling update in progress"
            if in_progress
            else "No rollout in progress"
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_engines": desired,
        "engines_stopped": outdated_running,
        "engines_provisioned": engines_with_target_hash,
        "current_engine_id": None,
        "current_phase": current_phase,
        "target_generation": target.get("generation"),
        "target_hash": target_hash,
    }

@app.post("/custom-variant/reprovision", dependencies=[Depends(require_api_key)])
async def reprovision_all_engines():
    """
    Trigger a declarative rolling update by bumping target engine config generation.
    """
    marked = _mark_engines_draining_for_reprovision(reason="engine_settings_reprovision")
    if marked > 0:
        engine_controller.request_reconcile(reason="engine_settings_reprovision")

    rollout = _trigger_engine_generation_rollout(reason="custom_variant_reprovision")
    changed = bool(rollout.get("changed"))

    return {
        "message": "Rolling update scheduled" if changed else "No config change detected; rollout not required",
        "reprovision_marked_engines": marked,
        "rolling_update": {
            "changed": changed,
            "target_generation": rollout.get("generation"),
            "target_hash": rollout.get("config_hash"),
        },
    }


@app.get("/settings/engine/reprovision/status")
def get_engine_reprovision_status():
    return get_reprovision_status()


@app.post("/settings/engine/reprovision", dependencies=[Depends(require_api_key)])
async def reprovision_all_engines_v2():
    return await reprovision_all_engines()

# Event Logging Endpoints
@app.get("/events", response_model=List[EventLog])
def get_events(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of events to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    event_type: Optional[str] = Query(None, description="Filter by event type (engine, stream, vpn, health, system)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    container_id: Optional[str] = Query(None, description="Filter by container ID"),
    stream_id: Optional[str] = Query(None, description="Filter by stream ID"),
    since: Optional[datetime] = Query(None, description="Only return events after this timestamp")
):
    """
    Retrieve application events with optional filtering.
    Events are returned in reverse chronological order (newest first).
    """
    events = event_logger.get_events(
        limit=limit,
        offset=offset,
        event_type=event_type,
        category=category,
        container_id=container_id,
        stream_id=stream_id,
        since=since
    )
    
    # Convert EventRow to EventLog schema
    return [EventLog(**_serialize_event_row(e)) for e in events]

@app.get("/events/stats")
def get_event_stats():
    """Get statistics about logged events."""
    return event_logger.get_event_stats()

@app.post("/events/cleanup", dependencies=[Depends(require_api_key)])
def cleanup_events(max_age_days: int = Query(30, ge=1, description="Delete events older than this many days")):
    """Manually trigger cleanup of old events."""
    deleted = event_logger.cleanup_old_events(max_age_days)
    return {"deleted": deleted, "message": f"Cleaned up {deleted} events older than {max_age_days} days"}

# Cache statistics endpoint
@app.get("/cache/stats")
def get_cache_stats():
    """Get cache statistics for monitoring and debugging."""
    cache = get_cache()
    return cache.get_stats()

@app.post("/cache/clear", dependencies=[Depends(require_api_key)])
def clear_cache():
    """Manually clear all cache entries."""
    cache = get_cache()
    cache.clear()
    return {"message": "Cache cleared successfully"}


# ============================================================================
# AceStream Proxy Endpoints
# ============================================================================

class LegacyStreamMonitorStartRequest(BaseModel):
    monitor_id: Optional[str] = None
    content_id: str
    stream_name: Optional[str] = None
    live_delay: Optional[int] = None
    interval_s: float = 1.0
    run_seconds: int = 0
    per_sample_timeout_s: float = 1.0
    engine_container_id: Optional[str] = None


class LegacyStreamMonitorM3UParseRequest(BaseModel):
    m3u_content: str


class StreamSeekRequest(BaseModel):
    target_timestamp: int


class StreamSaveRequest(BaseModel):
    path: str
    index: int = 0
    infohash: Optional[str] = None


@app.post("/ace/monitor/legacy/start", dependencies=[Depends(require_api_key)])
async def start_legacy_stream_monitor(req: LegacyStreamMonitorStartRequest):
    """Start a background legacy API monitor that collects STATUS every interval.

    The monitor uses LOADASYNC/START once, does not stream to clients, and gathers
    STATUS/livepos telemetry only for observability.
    """
    try:
        resolved_live_delay = _resolve_live_delay(None, req.live_delay)
        monitor = await legacy_stream_monitoring_service.start_monitor(
            content_id=req.content_id,
            stream_name=req.stream_name,
            live_delay=resolved_live_delay,
            interval_s=req.interval_s,
            run_seconds=req.run_seconds,
            per_sample_timeout_s=req.per_sample_timeout_s,
            engine_container_id=req.engine_container_id,
            monitor_id=req.monitor_id,
        )
        return monitor
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/ace/monitor/legacy/parse-m3u", dependencies=[Depends(require_api_key)])
async def parse_legacy_monitor_m3u(req: LegacyStreamMonitorM3UParseRequest):
    """Parse M3U content and extract acestream IDs with stream names."""
    from .services.m3u import parse_acestream_m3u_entries

    content = (req.m3u_content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="m3u_content is required")

    entries = parse_acestream_m3u_entries(content)
    return {
        "count": len(entries),
        "items": entries,
    }


@app.get("/ace/monitor/legacy", dependencies=[Depends(require_api_key)])
async def list_legacy_stream_monitors(
    include_recent_status: bool = Query(
        True,
        description="Include recent_status history in each monitor item. Set false to return latest_status-only summaries.",
    )
):
    """List all legacy monitoring sessions and their latest STATUS sample."""
    return {
        "items": await legacy_stream_monitoring_service.list_monitors(
            include_recent_status=include_recent_status,
        )
    }


@app.get("/ace/monitor/legacy/{monitor_id}", dependencies=[Depends(require_api_key)])
async def get_legacy_stream_monitor(
    monitor_id: str,
    include_recent_status: bool = Query(
        True,
        description="Include recent_status history. Set false to return latest_status-only summary for this monitor.",
    ),
):
    """Get a single legacy monitoring session including recent STATUS history."""
    monitor = await legacy_stream_monitoring_service.get_monitor(
        monitor_id,
        include_recent_status=include_recent_status,
    )
    if not monitor:
        raise HTTPException(status_code=404, detail="legacy monitor not found")
    return monitor


@app.delete("/ace/monitor/legacy/{monitor_id}", dependencies=[Depends(require_api_key)])
async def stop_legacy_stream_monitor(monitor_id: str):
    """Stop a legacy monitoring session and close its API connection."""
    stopped = await legacy_stream_monitoring_service.stop_monitor(monitor_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="legacy monitor not found")
    return {"stopped": True, "monitor_id": monitor_id}


@app.delete("/ace/monitor/legacy/{monitor_id}/entry", dependencies=[Depends(require_api_key)])
async def delete_legacy_stream_monitor(monitor_id: str):
    """Delete a legacy monitoring entry and ensure its API session is stopped."""
    deleted = await legacy_stream_monitoring_service.delete_monitor(monitor_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="legacy monitor not found")
    return {"deleted": True, "monitor_id": monitor_id}


def _select_stream_input(
    id: Optional[str],
    infohash: Optional[str],
    torrent_url: Optional[str],
    direct_url: Optional[str],
    raw_data: Optional[str],
) -> tuple[str, str]:
    choices = []
    for input_type, raw_value in [
        ("content_id", id),
        ("infohash", infohash),
        ("torrent_url", torrent_url),
        ("direct_url", direct_url),
        ("raw_data", raw_data),
    ]:
        text = (raw_value or "").strip()
        if text:
            choices.append((input_type, text))

    if not choices:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one input: id, infohash, torrent_url, direct_url, or raw_data",
        )

    if len(choices) > 1:
        raise HTTPException(
            status_code=400,
            detail="Input parameters are mutually exclusive. Provide only one of: id, infohash, torrent_url, direct_url, raw_data",
        )

    return choices[0]


def _normalize_file_indexes(file_indexes: Optional[str]) -> str:
    normalized = str(file_indexes if file_indexes is not None else "0").strip()
    if not normalized:
        return "0"

    # Keep query contract strict so stream identities are deterministic.
    if not re.fullmatch(r"\d+(,\d+)*", normalized):
        raise HTTPException(
            status_code=400,
            detail="file_indexes must be a comma-separated list of non-negative integers (for example: 0 or 0,2)",
        )
    return normalized


def _normalize_seekback(seekback: Optional[int]) -> int:
    if seekback is None:
        return 0
    try:
        normalized = int(float(seekback))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="live_delay (or seekback) must be a non-negative integer")
    if normalized < 0:
        raise HTTPException(status_code=400, detail="live_delay (or seekback) must be a non-negative integer")
    return normalized


def _resolve_live_delay(seekback: Optional[int], live_delay: Optional[int]) -> int:
    """Resolve effective startup delay using query override, legacy alias, then global default."""
    if live_delay is not None:
        return _normalize_seekback(live_delay)
    if seekback is not None:
        return _normalize_seekback(seekback)
    return _normalize_seekback(cfg.ACE_LIVE_EDGE_DELAY)


def _resolve_control_mode(mode: Optional[str]) -> str:
    """Normalize control mode to canonical values (http/api) with legacy aliases."""
    return normalize_proxy_mode(mode, default=PROXY_MODE_API) or PROXY_MODE_API


def _build_stream_key(input_type: str, input_value: str, file_indexes: str = "0", seekback: int = 0) -> str:
    if input_type in {"content_id", "infohash"} and file_indexes == "0" and seekback <= 0:
        return input_value

    # Preserve previous key format for non-id inputs when file index is default.
    if input_type not in {"content_id", "infohash"} and file_indexes == "0" and seekback <= 0:
        digest = hashlib.sha1(input_value.encode("utf-8")).hexdigest()
        return f"{input_type}:{digest}"

    keyed_payload = f"{input_type}:{input_value}|file_indexes={file_indexes}|seekback={seekback}"
    digest = hashlib.sha1(keyed_payload.encode("utf-8")).hexdigest()
    return f"{input_type}:{digest}"


def _build_engine_stream_params(
    input_type: str,
    input_value: str,
    pid: str,
    file_indexes: str = "0",
    seekback: int = 0,
) -> Dict[str, str]:
    params: Dict[str, str] = {
        "format": "json",
        "pid": pid,
        "file_indexes": file_indexes,
    }

    if seekback > 0:
        params["seekback"] = str(seekback)

    if input_type in {"content_id", "infohash"}:
        params["id"] = input_value
        if input_type == "infohash":
            params["infohash"] = input_value
    elif input_type == "torrent_url":
        params["torrent_url"] = input_value
    elif input_type == "direct_url":
        params["direct_url"] = input_value
        params["url"] = input_value
    elif input_type == "raw_data":
        params["raw_data"] = input_value
    else:
        params["id"] = input_value

    return params


def _build_stream_query_params(
    input_type: str,
    input_value: str,
    file_indexes: str = "0",
    seekback: int = 0,
) -> Dict[str, str]:
    if input_type == "content_id":
        params = {"id": input_value}
    else:
        params = {input_type: input_value}

    if file_indexes != "0":
        params["file_indexes"] = file_indexes

    if seekback > 0:
        params["live_delay"] = str(seekback)

    return params

@app.get(
    "/ace/preflight",
    tags=["Proxy"],
    summary="Preflight AceStream input",
    description="Runs availability checks and canonicalizes stream identifiers before playback.",
    responses={
        200: {"description": "Preflight result"},
        400: {"description": "Invalid request parameters"},
        503: {"description": "No available engine capacity"},
    },
)
def ace_preflight(
    id: Optional[str] = Query(None, description="AceStream content ID (PID/content_id)"),
    infohash: Optional[str] = Query(None, description="AceStream infohash"),
    torrent_url: Optional[str] = Query(None, description="Direct .torrent URL"),
    direct_url: Optional[str] = Query(None, description="Direct media/magnet URL"),
    raw_data: Optional[str] = Query(None, description="Raw torrent file data"),
    file_indexes: Optional[str] = Query("0", description="Comma-separated torrent file indexes (for example: 0 or 0,2)"),
    seekback: Optional[int] = Query(None, description="Deprecated alias for live_delay"),
    live_delay: Optional[int] = Query(None, description="Optional startup delay in seconds behind live edge"),
    tier: str = Query("light", description="Availability probe tier: light or deep"),
):
    """Run a short availability probe and canonicalize content IDs before playback."""
    from .proxy.config_helper import Config as ProxyConfig
    import requests

    input_type, input_value = _select_stream_input(id, infohash, torrent_url, direct_url, raw_data)
    normalized_file_indexes = _normalize_file_indexes(file_indexes)
    normalized_seekback = _resolve_live_delay(seekback, live_delay)
    stream_key = _build_stream_key(input_type, input_value, normalized_file_indexes, normalized_seekback)

    normalized_tier = (tier or "light").strip().lower()
    if normalized_tier not in {"light", "deep"}:
        raise HTTPException(status_code=400, detail="tier must be 'light' or 'deep'")

    control_mode = _resolve_control_mode(ProxyConfig.CONTROL_MODE)

    engines = state.list_engines()
    if not engines:
        raise HTTPException(status_code=503, detail="No engines available")

    active_streams = state.list_streams(status="started")
    monitor_loads = state.get_active_monitor_load_by_engine()
    engine_loads = {}
    for stream in active_streams:
        engine_loads[stream.container_id] = engine_loads.get(stream.container_id, 0) + 1

    for container_id, monitor_count in monitor_loads.items():
        engine_loads[container_id] = engine_loads.get(container_id, 0) + monitor_count

    max_streams = cfg.MAX_STREAMS_PER_ENGINE
    available_engines = [e for e in engines if engine_loads.get(e.container_id, 0) < max_streams]
    if not available_engines:
        raise HTTPException(
            status_code=503,
            detail=f"All engines at maximum capacity ({max_streams} streams per engine)",
        )

    selected_engine = sorted(
        available_engines,
        key=lambda e: (engine_loads.get(e.container_id, 0), not e.forwarded),
    )[0]

    if control_mode == PROXY_MODE_API:
        api_port = selected_engine.api_port or 62062
        client = AceLegacyApiClient(
            host=selected_engine.host,
            port=api_port,
            connect_timeout=8,
            response_timeout=8,
        )
        try:
            client.connect()
            client.authenticate()
            if input_type in {"content_id", "infohash"}:
                preflight_result = client.preflight(
                    input_value,
                    tier=normalized_tier,
                    file_indexes=normalized_file_indexes,
                )
            else:
                resolve_resp, resolved_mode = client.resolve_content(
                    input_value,
                    session_id="0",
                    mode=input_type,
                )
                status_code = resolve_resp.get("status")
                available = True if resolved_mode == "direct_url" else status_code in (1, 2)

                preflight_result = {
                    "tier": normalized_tier,
                    "available": available,
                    "status_code": 1 if resolved_mode == "direct_url" else status_code,
                    "mode": resolved_mode,
                    "infohash": resolve_resp.get("infohash"),
                    "loadresp": resolve_resp,
                    "can_retry": True,
                    "should_wait": bool(status_code == 2),
                }

                if not preflight_result["available"]:
                    preflight_result["message"] = resolve_resp.get("message", "content unavailable")

                if normalized_tier == "deep" and preflight_result["available"]:
                    start_info = client.start_stream(
                        input_value,
                        mode=resolved_mode,
                        file_indexes=normalized_file_indexes,
                        seekback=normalized_seekback,
                    )
                    status_probe = client.collect_status_samples(samples=4, interval_s=0.5, per_sample_timeout_s=2.0)
                    preflight_result["start"] = start_info
                    preflight_result["status_probe"] = status_probe
                    client.stop_stream()

            return {
                "control_mode": control_mode,
                "tier": normalized_tier,
                "input_type": input_type,
                "file_indexes": normalized_file_indexes,
                "seekback": normalized_seekback,
                "stream_key": stream_key,
                "engine": {
                    "container_id": selected_engine.container_id,
                    "host": selected_engine.host,
                    "port": selected_engine.port,
                    "api_port": api_port,
                    "forwarded": selected_engine.forwarded,
                },
                "result": preflight_result,
            }
        except AceLegacyApiError as e:
            raise HTTPException(status_code=503, detail=str(e))
        finally:
            try:
                client.shutdown()
            except Exception:
                pass

    # HTTP control fallback for compatibility in HTTP mode.
    url = f"http://{selected_engine.host}:{selected_engine.port}/ace/getstream"
    pid = f"preflight-{int(time.time())}"
    params = _build_engine_stream_params(
        input_type,
        input_value,
        pid=pid,
        file_indexes=normalized_file_indexes,
        seekback=normalized_seekback,
    )
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"HTTP preflight failed: {e}")

    if payload.get("error"):
        return {
            "control_mode": control_mode,
            "tier": normalized_tier,
            "input_type": input_type,
            "file_indexes": normalized_file_indexes,
            "seekback": normalized_seekback,
            "stream_key": stream_key,
            "engine": {
                "container_id": selected_engine.container_id,
                "host": selected_engine.host,
                "port": selected_engine.port,
                "api_port": selected_engine.api_port,
                "forwarded": selected_engine.forwarded,
            },
            "result": {
                "available": False,
                "message": payload.get("error"),
                "can_retry": True,
                "should_wait": False,
            },
        }

    response_data = payload.get("response") or {}
    result: Dict[str, Any] = {
        "available": bool(response_data.get("playback_url")),
        "infohash": response_data.get("infohash"),
        "playback_session_id": response_data.get("playback_session_id"),
        "playback_url": response_data.get("playback_url"),
        "stat_url": response_data.get("stat_url"),
        "command_url": response_data.get("command_url"),
        "is_live": response_data.get("is_live"),
        "can_retry": True,
        "should_wait": False,
    }

    if normalized_tier == "deep" and response_data.get("stat_url"):
        try:
            stat_response = requests.get(response_data.get("stat_url"), timeout=8)
            stat_response.raise_for_status()
            stat_payload = (stat_response.json() or {}).get("response") or {}
            result["status_probe"] = {
                "status_text": stat_payload.get("status_text") or stat_payload.get("status"),
                "status": stat_payload.get("status"),
                "progress": stat_payload.get("progress"),
                "peers": stat_payload.get("peers"),
                "http_peers": stat_payload.get("http_peers"),
                "speed_down": stat_payload.get("speed_down"),
                "speed_up": stat_payload.get("speed_up"),
                "downloaded": stat_payload.get("downloaded"),
                "uploaded": stat_payload.get("uploaded"),
                "livepos": stat_payload.get("livepos"),
            }
        except Exception as e:
            result["status_probe_error"] = str(e)

    return {
        "control_mode": control_mode,
        "tier": normalized_tier,
        "input_type": input_type,
        "file_indexes": normalized_file_indexes,
        "seekback": normalized_seekback,
        "stream_key": stream_key,
        "engine": {
            "container_id": selected_engine.container_id,
            "host": selected_engine.host,
            "port": selected_engine.port,
            "api_port": selected_engine.api_port,
            "forwarded": selected_engine.forwarded,
        },
        "result": result,
    }

@app.get(
    "/ace/getstream",
    tags=["Proxy"],
    summary="Start or join stream playback",
    description="Starts or joins multiplexed stream playback and returns MPEG-TS or HLS depending on proxy mode.",
    responses={
        200: {"description": "Streaming response"},
        400: {"description": "Invalid request or unsupported mode"},
        422: {"description": "Stream blacklisted or unprocessable"},
        500: {"description": "Internal streaming error"},
    },
)
async def ace_getstream(
    id: Optional[str] = Query(None, description="AceStream content ID (PID/content_id)"),
    infohash: Optional[str] = Query(None, description="AceStream infohash"),
    torrent_url: Optional[str] = Query(None, description="Direct .torrent URL"),
    direct_url: Optional[str] = Query(None, description="Direct media/magnet URL"),
    raw_data: Optional[str] = Query(None, description="Raw torrent file data"),
    file_indexes: Optional[str] = Query("0", description="Comma-separated torrent file indexes (for example: 0 or 0,2)"),
    seekback: Optional[int] = Query(None, description="Deprecated alias for live_delay"),
    live_delay: Optional[int] = Query(None, description="Optional startup delay in seconds behind live edge"),
    request: Request = None,
):
    """Proxy endpoint for AceStream video streams with multiplexing.
    
    This endpoint supports both MPEG-TS and HLS streaming modes based on proxy configuration.
    The mode can be configured in Proxy Settings (HLS only available for krinkuto11-amd64 variant).
    
    This endpoint:
    1. Checks if stream is blacklisted for looping
    2. Validates HLS mode is supported if configured
    3. Selects the best available engine (prioritizes forwarded, balances load)
    4. Multiplexes multiple clients to the same stream via battle-tested ts_proxy architecture
    5. Automatically manages stream lifecycle with heartbeat monitoring
    6. Sends events to orchestrator for panel visibility
    
    Args:
        id: AceStream content ID (PID/content_id)
        infohash: AceStream infohash
        torrent_url: Direct .torrent URL
        direct_url: Direct media/magnet URL
        raw_data: Raw torrent data payload
        file_indexes: Comma-separated torrent file indexes
        seekback: Deprecated alias for live_delay
        live_delay: Optional startup delay in seconds behind live edge
        request: FastAPI Request object for client info
        
    Returns:
        Streaming response with video data (TS or HLS based on configuration)
    """
    from fastapi.responses import StreamingResponse
    from uuid import uuid4
    from app.proxy.stream_generator import create_stream_generator
    from app.proxy.utils import get_client_ip
    from app.proxy.config_helper import Config as ProxyConfig
    from .services.looping_streams import looping_streams_tracker

    request_started_at = time.perf_counter()

    input_type, input_value = _select_stream_input(id, infohash, torrent_url, direct_url, raw_data)
    normalized_file_indexes = _normalize_file_indexes(file_indexes)
    normalized_seekback = _resolve_live_delay(seekback, live_delay)
    stream_key = _build_stream_key(input_type, input_value, normalized_file_indexes, normalized_seekback)
    
    # Get current stream mode
    stream_mode = ProxyConfig.STREAM_MODE
    control_mode = _resolve_control_mode(ProxyConfig.CONTROL_MODE)
    
    # Check if stream is on the looping blacklist
    if looping_streams_tracker.is_looping(stream_key):
        logger.warning(f"Stream request denied: {stream_key} is on looping blacklist")
        raise HTTPException(
            status_code=422,
            detail={
                "error": "stream_blacklisted",
                "code": "looping_stream",
                "message": "This stream has been detected as looping (no new data) and is temporarily blacklisted"
            }
        )
    
    # Generate unique client ID
    client_id = str(uuid4())
    
    # Get client info
    client_ip = get_client_ip(request) if request else "unknown"
    user_agent = request.headers.get('user-agent', 'unknown') if request else "unknown"
    client_identity = f"{client_ip}:{hashlib.sha1(user_agent.encode('utf-8', errors='ignore')).hexdigest()[:12]}"

    reusable_monitor_session = None
    if input_type in {"content_id", "infohash"} and normalized_file_indexes == "0" and normalized_seekback <= 0:
        reusable_monitor_session = await legacy_stream_monitoring_service.get_reusable_session_for_content(input_value)
    if reusable_monitor_session:
        monitor_engine = reusable_monitor_session.get("engine") or {}
        logger.info(
            "Reusing monitor session %s for stream %s on engine %s",
            reusable_monitor_session.get("monitor_id"),
            stream_key,
            str(monitor_engine.get("container_id") or "unknown")[:12],
        )
    
    reservation_engine_id = None
    
    def rollback_reservation(target_engine_id: Optional[str] = None):
        engine_id = target_engine_id or reservation_engine_id
        if engine_id:
            try:
                from app.proxy.manager import ProxyManager
                redis = ProxyManager.get_instance().redis_client
                if redis:
                    pending_key = f"ace_proxy:engine:{engine_id}:pending"
                    decr_script = """
                    local current = redis.call('GET', KEYS[1])
                    if current and tonumber(current) > 0 then
                        return redis.call('DECR', KEYS[1])
                    else
                        return 0
                    end
                    """
                    redis.eval(decr_script, 1, pending_key)
                    logger.debug(f"Rolled back pending reservation for engine {engine_id[:12]}")
            except Exception as e:
                logger.warning(f"Failed to rollback reservation for engine {engine_id[:12]}: {e}")
    
    def select_best_engine(additional_load_by_engine: Optional[Dict[str, int]] = None):
        """Select the best available engine using layer-based load balancing.
        
        Returns tuple of (selected_engine, current_load)
        Raises HTTPException if no engines available or all at capacity.
        """
        return select_best_engine_shared(
            reserve_pending=True,
            additional_load_by_engine=additional_load_by_engine,
        )

    def _find_active_api_hls_stream_id() -> Optional[str]:
        for active_stream in state.list_streams(status="started"):
            if active_stream.key != stream_key:
                continue
            if normalize_proxy_mode(active_stream.control_mode, default=PROXY_MODE_HTTP) == PROXY_MODE_API:
                return active_stream.id
        return None

    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    async def _register_api_hls_stream_if_missing(
        *,
        container_id: str,
        engine_host: str,
        engine_port: int,
        engine_api_port: int,
        playback_session_id: str,
        stat_url: str,
        command_url: str,
        is_live: int,
    ) -> Optional[str]:
        existing_stream_id = _find_active_api_hls_stream_id()
        if existing_stream_id:
            return existing_stream_id

        if not container_id or not engine_host or not engine_port or not playback_session_id:
            return None

        try:
            from .services.internal_events import handle_stream_started

            event = StreamStartedEvent(
                container_id=container_id,
                engine={"host": engine_host, "port": int(engine_port)},
                stream={
                    "key_type": input_type,
                    "key": stream_key,
                    "file_indexes": normalized_file_indexes,
                    "seekback": normalized_seekback,
                    "live_delay": normalized_seekback,
                    "control_mode": PROXY_MODE_API,
                },
                session={
                    "playback_session_id": playback_session_id,
                    "stat_url": stat_url,
                    "command_url": command_url,
                    "is_live": int(is_live or 1),
                },
                labels={
                    "source": "api_hls_segmenter",
                    "stream_mode": "HLS",
                    "proxy.control_mode": PROXY_MODE_API,
                    "stream.input_type": input_type,
                    "stream.file_indexes": normalized_file_indexes,
                    "stream.seekback": str(normalized_seekback),
                    "stream.live_delay": str(normalized_seekback),
                    "host.api_port": str(engine_api_port or ""),
                    "client.id": client_identity,
                    "client.ip": client_ip,
                    "client.user_agent": user_agent[:200],
                },
            )

            result = await asyncio.to_thread(handle_stream_started, event)
            return result.id if result else None
        except Exception as e:
            logger.warning("Failed to register API-mode HLS stream in state for %s: %s", stream_key, e)
            return None
    
    try:
        # Handle HLS mode differently from TS mode
        if stream_mode == 'HLS':
            import requests
            if control_mode == PROXY_MODE_HTTP:
                # HTTP mode uses the existing FastAPI-based HLS proxy.
                from app.proxy.hls_proxy import HLSProxyServer
                from uuid import uuid4

                hls_proxy = HLSProxyServer.get_instance()

                if hls_proxy.has_channel(stream_key):
                    logger.debug(f"HLS channel {stream_key} already exists, serving manifest to client {client_id} from {client_ip}")

                    try:
                        manifest_content = await hls_proxy.get_manifest_async(stream_key)
                        manifest_bytes = manifest_content.encode('utf-8')
                        manifest_seconds_behind = hls_proxy.get_manifest_buffer_seconds_behind(stream_key)
                        hls_proxy.record_client_activity(
                            stream_key,
                            client_ip,
                            client_id=client_identity,
                            user_agent=user_agent,
                            request_kind="manifest",
                            bytes_sent=len(manifest_bytes),
                            stream_buffer_window_seconds=manifest_seconds_behind,
                            position_source="hls_manifest_window",
                            position_confidence=0.35,
                        )

                        elapsed = time.perf_counter() - request_started_at
                        observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                        observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                        return StreamingResponse(
                            iter([manifest_bytes]),
                            media_type="application/vnd.apple.mpegurl",
                            headers={
                                "Cache-Control": "no-cache, no-store, must-revalidate",
                                "Connection": "keep-alive",
                            }
                        )
                    except TimeoutError as e:
                        logger.error(f"Timeout getting HLS manifest: {e}")
                        raise HTTPException(status_code=503, detail=f"Timeout waiting for stream buffer: {str(e)}")

                if reusable_monitor_session:
                    monitor_engine = reusable_monitor_session.get("engine") or {}
                    monitor_session = reusable_monitor_session.get("session") or {}
                    selected_engine = SimpleNamespace(
                        container_id=monitor_engine.get("container_id"),
                        host=monitor_engine.get("host"),
                        port=monitor_engine.get("port"),
                        api_port=monitor_engine.get("api_port") or 62062,
                        forwarded=bool(monitor_engine.get("forwarded")),
                    )
                    monitor_loads = state.get_active_monitor_load_by_engine()
                    current_load = len(state.list_streams(status="started", container_id=selected_engine.container_id)) + monitor_loads.get(selected_engine.container_id, 0)
                    playback_url = monitor_session.get("playback_url")
                    if not playback_url:
                        raise HTTPException(status_code=500, detail="Monitor session has no playback URL")
                else:
                    selected_engine, current_load = select_best_engine()
                    reservation_engine_id = selected_engine.container_id

                logger.info(
                    f"Selected engine {selected_engine.container_id[:12]} for new {stream_mode} stream {stream_key} "
                    f"(forwarded={selected_engine.forwarded}, current_load={current_load})"
                )
                logger.info(f"Client {client_id} initializing new {stream_mode} stream {stream_key} from {client_ip}")

                try:
                    if reusable_monitor_session:
                        logger.info("Using playback URL from monitoring session for HLS stream")
                        api_key = os.getenv('API_KEY')
                        monitor_session = reusable_monitor_session.get("session") or {}
                        monitor_playback_session_id = monitor_session.get('playback_session_id')
                        if not monitor_playback_session_id:
                            monitor_playback_session_id = f"hls-reuse-{stream_key[:16]}-{int(time.time())}"
                        session_info = {
                            'playback_session_id': monitor_playback_session_id,
                            'stat_url': monitor_session.get('stat_url') or '',
                            'command_url': monitor_session.get('command_url') or '',
                            'is_live': 1,
                            'owns_engine_session': False,
                        }
                    else:
                        hls_url = f"http://{selected_engine.host}:{selected_engine.port}/ace/manifest.m3u8"
                        pid = str(uuid4())
                        params = _build_engine_stream_params(
                            input_type,
                            input_value,
                            pid=pid,
                            file_indexes=normalized_file_indexes,
                            seekback=normalized_seekback,
                        )

                        logger.info(f"Requesting HLS stream from engine: {hls_url}")
                        response = requests.get(hls_url, params=params, timeout=10)
                        response.raise_for_status()
                        data = response.json()

                        if data.get("error"):
                            error_msg = data['error']
                            logger.error(f"AceStream engine returned error: {error_msg}")
                            raise HTTPException(status_code=500, detail=f"AceStream engine error: {error_msg}")

                        resp_data = data.get("response", {})
                        playback_url = resp_data.get("playback_url")
                        if not playback_url:
                            logger.error("No playback_url in AceStream response")
                            raise HTTPException(status_code=500, detail="No playback URL in engine response")

                        logger.info(f"HLS playback URL: {playback_url}")

                        api_key = os.getenv('API_KEY')
                        session_info = {
                            'playback_session_id': resp_data.get('playback_session_id'),
                            'stat_url': resp_data.get('stat_url'),
                            'command_url': resp_data.get('command_url'),
                            'is_live': resp_data.get('is_live', 1),
                            'owns_engine_session': True,
                        }

                    hls_proxy.initialize_channel(
                        channel_id=stream_key,
                        playback_url=playback_url,
                        engine_host=selected_engine.host,
                        engine_port=selected_engine.port,
                        engine_container_id=selected_engine.container_id,
                        session_info=session_info,
                        engine_api_port=selected_engine.api_port,
                        api_key=api_key,
                        stream_key_type=input_type,
                        file_indexes=normalized_file_indexes,
                        seekback=normalized_seekback,
                    )

                    manifest_content = await hls_proxy.get_manifest_async(stream_key)
                    manifest_bytes = manifest_content.encode('utf-8')
                    manifest_seconds_behind = hls_proxy.get_manifest_buffer_seconds_behind(stream_key)
                    hls_proxy.record_client_activity(
                        stream_key,
                        client_ip,
                        client_id=client_identity,
                        user_agent=user_agent,
                        request_kind="manifest",
                        bytes_sent=len(manifest_bytes),
                        stream_buffer_window_seconds=manifest_seconds_behind,
                        position_source="hls_manifest_window",
                        position_confidence=0.35,
                    )

                    elapsed = time.perf_counter() - request_started_at
                    observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                    observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                    return StreamingResponse(
                        iter([manifest_bytes]),
                        media_type="application/vnd.apple.mpegurl",
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Connection": "keep-alive",
                        }
                    )

                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to request HLS stream from engine: {e}")
                    raise HTTPException(status_code=503, detail=f"Engine communication error: {str(e)}")
                except TimeoutError as e:
                    logger.error(f"Timeout getting HLS manifest: {e}")
                    raise HTTPException(status_code=503, detail=f"Timeout waiting for stream buffer: {str(e)}")
            else:
                # API mode: expose HLS by segmenting MPEG-TS playback with local FFmpeg.
                existing_manifest = await hls_segmenter_service.get_or_wait_manifest(stream_key, timeout_s=15.0)
                if existing_manifest:
                    session_meta = hls_segmenter_service.get_session_metadata(stream_key) or {}
                    existing_stream_id = str(session_meta.get("stream_id") or "").strip()
                    if not existing_stream_id:
                        stream_id = await _register_api_hls_stream_if_missing(
                            container_id=str(session_meta.get("container_id") or ""),
                            engine_host=str(session_meta.get("engine_host") or ""),
                            engine_port=_safe_int(session_meta.get("engine_port"), default=0),
                            engine_api_port=_safe_int(session_meta.get("engine_api_port"), default=0),
                            playback_session_id=str(session_meta.get("playback_session_id") or ""),
                            stat_url=str(session_meta.get("stat_url") or ""),
                            command_url=str(session_meta.get("command_url") or ""),
                            is_live=_safe_int(session_meta.get("is_live"), default=1),
                        )
                        if stream_id:
                            hls_segmenter_service.set_session_metadata(stream_key, {"stream_id": stream_id})

                    logger.debug("Reusing external HLS segmenter for stream %s", stream_key)
                    hls_segmenter_service.record_activity(stream_key)
                    manifest_content = await hls_segmenter_service.read_manifest(stream_key, rewrite=True)
                    manifest_bytes = manifest_content.encode('utf-8')
                    hls_segmenter_service.record_client_activity(
                        stream_key,
                        client_identity,
                        client_ip,
                        user_agent,
                        request_kind="manifest",
                        bytes_sent=len(manifest_bytes),
                        stream_buffer_window_seconds=hls_segmenter_service.estimate_manifest_buffer_seconds_behind(stream_key),
                        position_source="hls_manifest_window",
                        position_confidence=0.35,
                    )

                    elapsed = time.perf_counter() - request_started_at
                    observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                    observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                    return StreamingResponse(
                        iter([manifest_bytes]),
                        media_type="application/vnd.apple.mpegurl",
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Connection": "keep-alive",
                        }
                    )

                if reusable_monitor_session:
                    monitor_engine = reusable_monitor_session.get("engine") or {}
                    monitor_session = reusable_monitor_session.get("session") or {}
                    selected_engine = SimpleNamespace(
                        container_id=monitor_engine.get("container_id"),
                        host=monitor_engine.get("host"),
                        port=monitor_engine.get("port"),
                        api_port=monitor_engine.get("api_port") or 62062,
                        forwarded=bool(monitor_engine.get("forwarded")),
                    )
                    playback_url = str(monitor_session.get("playback_url") or "").strip()
                    if not playback_url:
                        raise HTTPException(status_code=500, detail="Monitor session has no playback URL")
                    start_info = {
                        "playback_session_id": str(monitor_session.get("playback_session_id") or f"api-hls-reuse-{int(time.time())}"),
                        "stat_url": str(monitor_session.get("stat_url") or ""),
                        "command_url": str(monitor_session.get("command_url") or ""),
                        "is_live": int(monitor_session.get("is_live") or 1),
                    }
                    legacy_api_client = None
                else:
                    engines_count = max(1, len(state.list_engines()))
                    max_engine_attempts = min(2, engines_count)
                    excluded_engine_penalties: Dict[str, int] = {}
                    last_start_error: Optional[HTTPException] = None
                    selected_engine = None
                    legacy_api_client = None
                    start_info = {}
                    playback_url = ""

                    for attempt_idx in range(max_engine_attempts):
                        selected_engine, current_load = select_best_engine(
                            additional_load_by_engine=excluded_engine_penalties,
                        )
                        reservation_engine_id = selected_engine.container_id

                        logger.info(
                            "Starting API-mode HLS session on engine %s for stream %s (attempt %s/%s)",
                            selected_engine.container_id[:12],
                            stream_key,
                            attempt_idx + 1,
                            max_engine_attempts,
                        )

                        client = AceLegacyApiClient(
                            host=selected_engine.host,
                            port=selected_engine.api_port or 62062,
                            connect_timeout=10,
                            response_timeout=10,
                        )

                        try:
                            await asyncio.to_thread(client.connect)
                            await asyncio.to_thread(client.authenticate)

                            start_mode = input_type
                            start_payload = input_value

                            # Manual preflight logic removed as per user request to allow only UI-triggered checks.
                            # The engine START command will now use the raw input values.

                            loadresp, resolved_mode = await asyncio.to_thread(
                                client.resolve_content,
                                input_value,
                                "0",
                                input_type,
                            )
                            if resolved_mode != "direct_url":
                                status_code = loadresp.get("status")
                                if status_code not in (1, 2):
                                    message = loadresp.get("message") or "content unavailable"
                                    raise HTTPException(status_code=503, detail=f"LOADASYNC status={status_code}: {message}")
                            start_mode = resolved_mode

                            start_info = await asyncio.to_thread(
                                client.start_stream,
                                start_payload,
                                start_mode,
                                "output_format=http",
                                normalized_file_indexes,
                                normalized_seekback,
                            )
                            playback_url = str(start_info.get("url") or "").strip()
                            if not playback_url:
                                raise HTTPException(status_code=500, detail="No playback URL returned by API START")

                            legacy_api_client = client
                            break
                        except HTTPException as e:
                            last_start_error = e
                            excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                            rollback_reservation(selected_engine.container_id)
                            try:
                                await asyncio.to_thread(client.shutdown)
                            except Exception:
                                pass
                            if attempt_idx + 1 >= max_engine_attempts:
                                raise
                            logger.warning(
                                "API-mode HLS startup failed on engine %s (attempt %s/%s): %s. Retrying with another engine.",
                                selected_engine.container_id[:12],
                                attempt_idx + 1,
                                max_engine_attempts,
                                e.detail,
                            )
                        except AceLegacyApiError as e:
                            last_start_error = HTTPException(status_code=503, detail=str(e))
                            excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                            rollback_reservation(selected_engine.container_id)
                            try:
                                await asyncio.to_thread(client.shutdown)
                            except Exception:
                                pass
                            if attempt_idx + 1 >= max_engine_attempts:
                                raise last_start_error
                            logger.warning(
                                "API-mode HLS legacy API error on engine %s (attempt %s/%s): %s. Retrying with another engine.",
                                selected_engine.container_id[:12],
                                attempt_idx + 1,
                                max_engine_attempts,
                                e,
                            )

                    if not playback_url:
                        if last_start_error:
                            raise last_start_error
                        raise HTTPException(status_code=503, detail="Unable to start API-mode HLS stream")

                logger.info("Starting external HLS segmenter for stream %s", stream_key)
                segmenter_metadata = {
                    "playback_session_id": str(start_info.get("playback_session_id") or f"api-hls-{int(time.time())}"),
                    "stat_url": str(start_info.get("stat_url") or ""),
                    "command_url": str(start_info.get("command_url") or ""),
                    "is_live": int(start_info.get("is_live") or 1),
                    "container_id": str(selected_engine.container_id or ""),
                    "engine_host": str(selected_engine.host or ""),
                    "engine_port": int(selected_engine.port or 0),
                    "engine_api_port": int(selected_engine.api_port or 0),
                    "stream_key_type": input_type,
                    "file_indexes": normalized_file_indexes,
                    "seekback": normalized_seekback,
                    "control_client": legacy_api_client,
                }
                try:
                    await hls_segmenter_service.start_segmenter(stream_key, playback_url, metadata=segmenter_metadata)
                except (FileNotFoundError, RuntimeError, TimeoutError) as e:
                    if legacy_api_client is not None:
                        try:
                            await asyncio.to_thread(legacy_api_client.shutdown)
                        except Exception:
                            pass
                    logger.error("Failed to initialize external HLS segmenter for stream %s: %s", stream_key, e)
                    raise HTTPException(status_code=503, detail=f"Failed to initialize HLS segmenter: {e}")

                stream_id = await _register_api_hls_stream_if_missing(
                    container_id=str(selected_engine.container_id or ""),
                    engine_host=str(selected_engine.host or ""),
                    engine_port=int(selected_engine.port or 0),
                    engine_api_port=int(selected_engine.api_port or 0),
                    playback_session_id=str(segmenter_metadata.get("playback_session_id") or ""),
                    stat_url=str(segmenter_metadata.get("stat_url") or ""),
                    command_url=str(segmenter_metadata.get("command_url") or ""),
                    is_live=int(segmenter_metadata.get("is_live") or 1),
                )
                if stream_id:
                    hls_segmenter_service.set_session_metadata(stream_key, {"stream_id": stream_id})

                hls_segmenter_service.record_activity(stream_key)
                manifest_content = await hls_segmenter_service.read_manifest(stream_key, rewrite=True)
                manifest_bytes = manifest_content.encode('utf-8')
                hls_segmenter_service.record_client_activity(
                    stream_key,
                    client_identity,
                    client_ip,
                    user_agent,
                    request_kind="manifest",
                    bytes_sent=len(manifest_bytes),
                    stream_buffer_window_seconds=hls_segmenter_service.estimate_manifest_buffer_seconds_behind(stream_key),
                    position_source="hls_manifest_window",
                    position_confidence=0.35,
                )

                elapsed = time.perf_counter() - request_started_at
                observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                return StreamingResponse(
                    iter([manifest_bytes]),
                    media_type="application/vnd.apple.mpegurl",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Connection": "keep-alive",
                    }
                )
        else:
            # TS mode - use existing ts_proxy architecture
            if reusable_monitor_session:
                monitor_engine = reusable_monitor_session.get("engine") or {}
                monitor_session = reusable_monitor_session.get("session") or {}
                selected_engine = SimpleNamespace(
                    container_id=monitor_engine.get("container_id"),
                    host=monitor_engine.get("host"),
                    port=monitor_engine.get("port"),
                    api_port=monitor_engine.get("api_port") or 62062,
                    forwarded=bool(monitor_engine.get("forwarded")),
                )
                start_info = {
                    "playback_session_id": str(monitor_session.get("playback_session_id") or f"api-ts-reuse-{int(time.time())}"),
                    "stat_url": str(monitor_session.get("stat_url") or ""),
                    "command_url": str(monitor_session.get("command_url") or ""),
                    "is_live": int(monitor_session.get("is_live") or 1),
                }
                playback_url = str(monitor_session.get("playback_url") or "").strip()
                legacy_api_client = None
            elif control_mode == PROXY_MODE_API:
                engines_count = max(1, len(state.list_engines()))
                max_engine_attempts = min(2, engines_count)
                excluded_engine_penalties: Dict[str, int] = {}
                last_start_error: Optional[HTTPException] = None
                selected_engine = None
                legacy_api_client = None
                start_info = {}
                playback_url = ""

                for attempt_idx in range(max_engine_attempts):
                    selected_engine, current_load = select_best_engine(
                        additional_load_by_engine=excluded_engine_penalties,
                    )
                    reservation_engine_id = selected_engine.container_id

                    logger.info(
                        "Starting API-mode %s session on engine %s for stream %s (attempt %s/%s)",
                        stream_mode,
                        selected_engine.container_id[:12],
                        stream_key,
                        attempt_idx + 1,
                        max_engine_attempts,
                    )

                    client = AceLegacyApiClient(
                        host=selected_engine.host,
                        port=selected_engine.api_port or 62062,
                        connect_timeout=10,
                        response_timeout=10,
                    )

                    try:
                        await asyncio.to_thread(client.connect)
                        await asyncio.to_thread(client.authenticate)

                        loadresp, resolved_mode = await asyncio.to_thread(
                            client.resolve_content,
                            input_value,
                            "0",
                            input_type,
                        )
                        if resolved_mode != "direct_url":
                            status_code = loadresp.get("status")
                            if status_code not in (1, 2):
                                message = loadresp.get("message") or "content unavailable"
                                raise HTTPException(status_code=503, detail=f"LOADASYNC status={status_code}: {message}")
                        
                        start_mode = resolved_mode
                        start_payload = input_value
                        if loadresp.get("infohash"):
                            start_mode = "infohash"
                            start_payload = loadresp.get("infohash")

                        start_info = await asyncio.to_thread(
                            client.start_stream,
                            start_payload,
                            start_mode,
                            "output_format=http",
                            normalized_file_indexes,
                            normalized_seekback,
                        )
                        playback_url = str(start_info.get("url") or "").strip()
                        if not playback_url:
                            raise HTTPException(status_code=500, detail="No playback URL returned by API START")

                        legacy_api_client = client
                        break
                    except HTTPException as e:
                        last_start_error = e
                        excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                        rollback_reservation(selected_engine.container_id)
                        try:
                            await asyncio.to_thread(client.shutdown)
                        except Exception:
                            pass
                        if attempt_idx + 1 >= max_engine_attempts:
                            raise
                        logger.warning(
                            "API-mode %s startup failed on engine %s (attempt %s/%s): %s. Retrying.",
                            stream_mode,
                            selected_engine.container_id[:12],
                            attempt_idx + 1,
                            max_engine_attempts,
                            e.detail,
                        )
                    except AceLegacyApiError as e:
                        last_start_error = HTTPException(status_code=503, detail=str(e))
                        excluded_engine_penalties[selected_engine.container_id] = cfg.MAX_STREAMS_PER_ENGINE
                        rollback_reservation(selected_engine.container_id)
                        try:
                            await asyncio.to_thread(client.shutdown)
                        except Exception:
                            pass
                        if attempt_idx + 1 >= max_engine_attempts:
                            raise last_start_error
                        logger.warning(
                            "API-mode %s legacy API error on engine %s (attempt %s/%s): %s. Retrying.",
                            stream_mode,
                            selected_engine.container_id[:12],
                            attempt_idx + 1,
                            max_engine_attempts,
                            e,
                        )

                if not playback_url:
                    if last_start_error:
                        raise last_start_error
                    raise HTTPException(status_code=503, detail=f"Unable to start API-mode {stream_mode} stream")
            else:
                # HTTP mode - simple engine selection
                selected_engine, current_load = select_best_engine()
                reservation_engine_id = selected_engine.container_id
                start_info = {}
                playback_url = None
                legacy_api_client = None

            logger.info(
                f"Client {client_id} connecting to {stream_mode} stream {stream_key} from {client_ip} "
                f"on engine {selected_engine.container_id[:12]}"
            )
            
            # Get proxy instance
            proxy = ProxyManager.get_instance()
            
            # Start stream if not exists (idempotent)
            success = proxy.start_stream(
                content_id=stream_key,
                engine_host=selected_engine.host,
                engine_port=selected_engine.port,
                engine_api_port=selected_engine.api_port,
                engine_container_id=selected_engine.container_id,
                existing_session=reusable_monitor_session,
                source_input=input_value,
                source_input_type=input_type,
                file_indexes=normalized_file_indexes,
                seekback=normalized_seekback,
                # New adoption parameters
                playback_url=playback_url,
                playback_session_id=start_info.get("playback_session_id"),
                stat_url=start_info.get("stat_url"),
                command_url=start_info.get("command_url"),
                is_live=start_info.get("is_live"),
                ace_api_client=legacy_api_client,
            )
            
            if not success:
                if legacy_api_client:
                    try:
                        await asyncio.to_thread(legacy_api_client.shutdown)
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=500,
                    detail="Failed to start stream session"
                )
            
            # Create stream generator
            generator = create_stream_generator(
                content_id=stream_key,
                client_id=client_id,
                client_ip=client_ip,
                client_user_agent=user_agent,
                stream_initializing=(control_mode == PROXY_MODE_API)
            )
            
            # Return streaming response with TS data
            elapsed = time.perf_counter() - request_started_at
            observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
            observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

            return StreamingResponse(
                generator.generate(),
                media_type="video/mp2t",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Connection": "keep-alive",
                }
            )
        
    except HTTPException as exc:
        rollback_reservation()
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=False, status_code=exc.status_code)
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        rollback_reservation()
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=False, status_code=500)
        logger.error(f"Unexpected error in ace_getstream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")



@app.get(
    "/ace/hls/{content_id}/segment/{segment_path:path}",
    tags=["Proxy"],
    summary="Fetch HLS segment",
    description="Returns a buffered HLS segment for an active AceStream channel.",
    responses={
        200: {"description": "Segment data"},
        404: {"description": "Segment not found"},
        500: {"description": "Segment retrieval error"},
    },
)
async def ace_hls_segment(
    content_id: str,
    segment_path: str,
    request: Request,
):
    """Proxy endpoint for HLS segments.
    
    This endpoint serves individual HLS segments from the buffered stream.
    It's used when the stream mode is set to HLS.
    
    Args:
        content_id: AceStream content ID (infohash or content_id)
        segment_path: Segment filename from the M3U8 manifest (e.g., "123.ts")
        request: FastAPI Request object for client info
        
    Returns:
        Streaming response with segment data
    """
    from fastapi.responses import Response
    from app.proxy.hls_proxy import HLSProxyServer
    from app.proxy.utils import get_client_ip
    
    logger.debug(f"HLS segment request: content_id={content_id}, segment={segment_path}")
    request_started_at = time.perf_counter()
    
    try:
        hls_proxy = HLSProxyServer.get_instance()

        client_ip = get_client_ip(request)
        user_agent = request.headers.get('user-agent', 'unknown')
        client_identity = f"{client_ip}:{hashlib.sha1(user_agent.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
        
        # Parse sequence number from segment path (e.g. "123.ts" or "segment_123.ts")
        sequence = None
        try:
            # Extract digits from the filename
            seq_match = re.search(r'(\d+)', segment_path)
            if seq_match:
                sequence = int(seq_match.group(1))
        except Exception:
            pass

        segment_data = hls_proxy.get_segment(content_id, segment_path)
        hls_proxy.record_client_activity(
            content_id,
            client_ip,
            client_id=client_identity,
            user_agent=user_agent,
            request_kind="segment",
            bytes_sent=len(segment_data),
            chunks_sent=1,
            sequence=sequence,
            stream_buffer_window_seconds=hls_proxy.get_manifest_buffer_seconds_behind(content_id),
            client_runway_seconds=hls_proxy.get_segment_buffer_seconds_behind(content_id, sequence),
            position_source="hls_segment_delta",
            position_confidence=0.85,
        )
        observe_proxy_egress_bytes("HLS", len(segment_data))
        
        # Return segment data directly
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/ace/hls/segment", elapsed, success=True, status_code=200)
        observe_proxy_ttfb("HLS", "/ace/hls/segment", elapsed)

        return Response(
            content=segment_data,
            media_type="video/MP2T",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Connection": "keep-alive",
            }
        )
    except ValueError as e:
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/ace/hls/segment", elapsed, success=False, status_code=404)
        logger.warning(f"Segment not found: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/ace/hls/segment", elapsed, success=False, status_code=500)
        logger.error(f"Error serving HLS segment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Segment error: {str(e)}")



@app.get(
    "/api/v1/hls/{monitor_id}/{segment_filename}",
    tags=["Proxy"],
    summary="Serve FFmpeg-generated HLS segment",
    description="Serves local HLS .ts segments produced by the API-mode external segmenter.",
    responses={
        200: {"description": "Segment data"},
        404: {"description": "Segment not found"},
    },
)
async def api_hls_segment_file(monitor_id: str, segment_filename: str, request: Request):
    from app.proxy.utils import get_client_ip

    request_started_at = time.perf_counter()

    try:
        path = hls_segmenter_service.get_segment_file_path(monitor_id, segment_filename)
        if not path or not path.exists() or not path.is_file():
            elapsed = time.perf_counter() - request_started_at
            observe_proxy_request("HLS", "/api/v1/hls/segment", elapsed, success=False, status_code=404)
            raise HTTPException(status_code=404, detail="HLS segment not found")

        hls_segmenter_service.record_activity(monitor_id)
        # Track clients for API-mode HLS streams so /proxy/streams/{key}/clients can report them.
        client_ip = get_client_ip(request)
        user_agent = request.headers.get('user-agent', 'unknown')
        try:
            segment_size = int(path.stat().st_size)
        except OSError:
            segment_size = 0
        client_identity = f"{client_ip}:{hashlib.sha1(user_agent.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
        
        # Parse sequence number from segment filename (e.g. "index123.ts" or "123.ts")
        sequence = None
        try:
            seq_match = re.search(r'(\d+)', segment_filename)
            if seq_match:
                sequence = int(seq_match.group(1))
        except Exception:
            pass

        hls_segmenter_service.record_client_activity(
            monitor_id,
            client_identity,
            client_ip,
            user_agent,
            request_kind="segment",
            bytes_sent=segment_size,
            chunks_sent=1,
            sequence=sequence,
            stream_buffer_window_seconds=hls_segmenter_service.estimate_manifest_buffer_seconds_behind(monitor_id),
            client_runway_seconds=hls_segmenter_service.estimate_segment_buffer_seconds_behind(monitor_id, sequence),
            position_source="hls_segment_delta",
            position_confidence=0.85,
        )
        observe_proxy_egress_bytes("HLS", segment_size)

        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/api/v1/hls/segment", elapsed, success=True, status_code=200)
        observe_proxy_ttfb("HLS", "/api/v1/hls/segment", elapsed)

        return FileResponse(path=str(path), media_type="video/MP2T")
    except HTTPException:
        raise
    except Exception as e:
        elapsed = time.perf_counter() - request_started_at
        observe_proxy_request("HLS", "/api/v1/hls/segment", elapsed, success=False, status_code=500)
        logger.error(f"Error serving API-mode HLS segment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"HLS segment error: {str(e)}")




@app.get(
    "/ace/manifest.m3u8",
    tags=["Proxy"],
    summary="HLS manifest entrypoint",
    description="Serves HLS manifests for both HTTP and API control modes.",
    responses={
        200: {"description": "HLS manifest"},
        400: {"description": "Invalid request parameters"},
    },
)
async def ace_manifest(
    id: Optional[str] = Query(None, description="AceStream content ID (PID/content_id)"),
    infohash: Optional[str] = Query(None, description="AceStream infohash"),
    torrent_url: Optional[str] = Query(None, description="Direct .torrent URL"),
    direct_url: Optional[str] = Query(None, description="Direct media/magnet URL"),
    raw_data: Optional[str] = Query(None, description="Raw torrent file data"),
    file_indexes: Optional[str] = Query("0", description="Comma-separated torrent file indexes (for example: 0 or 0,2)"),
    seekback: Optional[int] = Query(None, description="Deprecated alias for live_delay"),
    live_delay: Optional[int] = Query(None, description="Optional startup delay in seconds behind live edge"),
    request: Request = None,
):
    """Proxy endpoint for AceStream HLS streams (M3U8).

    Args:
        id: AceStream content ID (PID/content_id)
        infohash: AceStream infohash
        torrent_url: Direct .torrent URL
        direct_url: Direct media/magnet URL
        raw_data: Raw torrent data payload
        file_indexes: Comma-separated torrent file indexes
        seekback: Deprecated alias for live_delay
        live_delay: Optional startup delay in seconds behind live edge

    Returns:
        HLS manifest content
    """
    return await ace_getstream(
        id=id,
        infohash=infohash,
        torrent_url=torrent_url,
        direct_url=direct_url,
        raw_data=raw_data,
        file_indexes=file_indexes,
        seekback=seekback,
        live_delay=live_delay,
        request=request,
    )


@app.post("/api/v1/streams/{monitor_id}/seek", dependencies=[Depends(require_api_key)])
async def seek_stream_live(monitor_id: str, req: StreamSeekRequest):
    """Seek an active API-mode stream to the given live timestamp via LIVESEEK."""
    target_timestamp = int(req.target_timestamp)
    if target_timestamp < 0:
        raise HTTPException(status_code=400, detail="target_timestamp must be non-negative")

    manager, _, _ = await _resolve_proxy_stream_manager(monitor_id)

    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for seek request")

    try:
        await asyncio.to_thread(manager.seek_stream, target_timestamp)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to seek stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Seek failed: {str(e)}")

    return {"status": "seek_issued"}


async def _resolve_proxy_stream_manager(monitor_id: str):
    """Resolve active StreamManager by stream id or monitor_id fallback."""
    proxy = ProxyManager.get_instance()

    stream = state.get_stream(monitor_id)
    stream_key = stream.key if stream else monitor_id
    manager = proxy.stream_managers.get(stream_key)

    # Compatibility fallback when monitor_id points to a legacy monitor session.
    if not manager:
        monitor = await legacy_stream_monitoring_service.get_monitor(monitor_id, include_recent_status=False)
        monitor_content_id = (monitor or {}).get("content_id") if monitor else None
        normalized_monitor_content_id = (monitor_content_id or "").strip().lower()
        if normalized_monitor_content_id:
            manager = proxy.stream_managers.get(normalized_monitor_content_id)
            stream_key = normalized_monitor_content_id

    return manager, stream, stream_key


def _set_stream_paused_runtime_state(monitor_id: str, stream: Optional[StreamState], paused: bool):
    if stream:
        state.set_stream_paused(stream.id, paused)

    monitor_session = state.get_monitor_session(monitor_id)
    if monitor_session:
        monitor_session["paused"] = bool(paused)
        monitor_session["status"] = "paused" if paused else "running"
        state.upsert_monitor_session(monitor_id, monitor_session)


@app.post("/api/v1/streams/{monitor_id}/pause", dependencies=[Depends(require_api_key)])
async def pause_stream_live(monitor_id: str):
    """Pause an active API-mode stream via PAUSE command."""
    manager, stream, _ = await _resolve_proxy_stream_manager(monitor_id)
    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for pause request")

    try:
        await asyncio.to_thread(manager.pause_stream)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AceLegacyApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to pause stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Pause failed: {str(e)}")

    _set_stream_paused_runtime_state(monitor_id, stream, True)
    return {"status": "paused"}


@app.post("/api/v1/streams/{monitor_id}/resume", dependencies=[Depends(require_api_key)])
async def resume_stream_live(monitor_id: str):
    """Resume an active API-mode stream via RESUME command."""
    manager, stream, _ = await _resolve_proxy_stream_manager(monitor_id)
    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for resume request")

    try:
        await asyncio.to_thread(manager.resume_stream)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AceLegacyApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to resume stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(e)}")

    _set_stream_paused_runtime_state(monitor_id, stream, False)
    return {"status": "resumed"}


@app.post("/api/v1/streams/{monitor_id}/save", dependencies=[Depends(require_api_key)])
async def save_stream_live(monitor_id: str, req: StreamSaveRequest):
    """Request SAVE for an active API-mode stream session."""
    save_path = str(req.path or "").strip()
    if not save_path:
        raise HTTPException(status_code=400, detail="path is required")

    file_index = int(req.index)
    if file_index < 0:
        raise HTTPException(status_code=400, detail="index must be non-negative")

    manager, _, _ = await _resolve_proxy_stream_manager(monitor_id)
    if not manager:
        raise HTTPException(status_code=404, detail="Active proxy stream not found for save request")

    try:
        result = await asyncio.to_thread(
            manager.save_stream,
            infohash=req.infohash,
            index=file_index,
            path=save_path,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AceLegacyApiError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to save stream {monitor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")

    return result


@app.get("/proxy/status")
async def proxy_status():
    """Get proxy status and active sessions.
    
    Returns:
        Proxy status including active sessions and client counts
    """
    proxy_manager = ProxyManager.get_instance()
    status = await proxy_manager.get_status()
    return status


@app.get("/proxy/sessions")
async def proxy_sessions():
    """Get list of active proxy sessions.
    
    Returns:
        List of active sessions with details
    """
    proxy_manager = ProxyManager.get_instance()
    status = await proxy_manager.get_status()
    return {"sessions": status.get("sessions", [])}


@app.get("/proxy/sessions/{ace_id}")
async def proxy_session_info(ace_id: str):
    """Get detailed info for a specific proxy session.
    
    Args:
        ace_id: AceStream content ID
        
    Returns:
        Session details or 404 if not found
    """
    proxy_manager = ProxyManager.get_instance()
    session_info = await proxy_manager.get_session_info(ace_id)
    
    if not session_info:
        raise HTTPException(status_code=404, detail=f"Session {ace_id} not found")
    
    return session_info


@app.get("/proxy/streams/{stream_key}/clients")
async def get_stream_clients(stream_key: str):
    """Get list of clients connected to a specific stream.
    
    Args:
        stream_key: AceStream content ID (infohash)
        
    Returns:
        List of client details or empty list if no clients
    """
    from .services.client_tracker import client_tracking_service
    from .proxy.config_helper import Config as ProxyConfig
    
    try:
        _prune_client_tracker_if_due(ttl_s=float(ProxyConfig.CLIENT_RECORD_TTL), min_interval_s=3.0)
        clients = client_tracking_service.get_stream_clients(stream_key)
        return {"clients": clients}
        
    except Exception as e:
        logger.error(f"Error getting clients for stream {stream_key}: {e}")
        return {"clients": []}


@app.get("/debug/sync-check")
async def sync_check():
    """
    Debug endpoint to check synchronization between state and proxy.
    
    Returns information about:
    - Streams in state.streams
    - Active TS proxy sessions
    - Active HLS proxy sessions
    - Orphaned proxy sessions (in proxy but not in state)
    - Missing proxy sessions (in state but not in proxy)
    """
    from .proxy.server import ProxyServer
    from .proxy.hls_proxy import HLSProxyServer
    
    # Get state streams
    state_streams = state.list_streams_with_stats(status="started")
    state_keys = {s.key for s in state_streams}
    
    # Get TS proxy sessions
    ts_proxy = ProxyServer.get_instance()
    ts_sessions = set(ts_proxy.stream_managers.keys())
    
    # Get HLS proxy sessions
    hls_proxy = HLSProxyServer.get_instance()
    hls_sessions = set(hls_proxy.stream_managers.keys())
    
    # Find discrepancies
    orphaned_ts = ts_sessions - state_keys
    orphaned_hls = hls_sessions - state_keys
    missing_ts = state_keys - ts_sessions
    missing_hls = state_keys - hls_sessions
    
    return {
        "state": {
            "stream_count": len(state_streams),
            "stream_keys": list(state_keys)
        },
        "ts_proxy": {
            "session_count": len(ts_sessions),
            "session_keys": list(ts_sessions)
        },
        "hls_proxy": {
            "session_count": len(hls_sessions),
            "session_keys": list(hls_sessions)
        },
        "discrepancies": {
            "orphaned_ts_sessions": list(orphaned_ts),  # In TS proxy but not in state
            "orphaned_hls_sessions": list(orphaned_hls),  # In HLS proxy but not in state
            "missing_ts_sessions": list(missing_ts),  # In state but not in TS proxy
            "missing_hls_sessions": list(missing_hls),  # In state but not in HLS proxy
            "has_issues": any([orphaned_ts, orphaned_hls, missing_ts, missing_hls])
        }
    }


# WebSocket endpoint removed - using simple polling approach

@app.get("/stream-loop-detection/config")
def get_stream_loop_detection_config():
    """Get current stream loop detection configuration."""
    return {
        "enabled": cfg.STREAM_LOOP_DETECTION_ENABLED,
        "threshold_seconds": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S,
        "threshold_minutes": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S / 60,
        "threshold_hours": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S / 3600,
        "check_interval_seconds": cfg.STREAM_LOOP_CHECK_INTERVAL_S,
        "retention_minutes": cfg.STREAM_LOOP_RETENTION_MINUTES,
    }

@app.post("/stream-loop-detection/config", dependencies=[Depends(require_api_key)])
async def update_stream_loop_detection_config(
    enabled: bool, 
    threshold_seconds: int,
    check_interval_seconds: Optional[int] = None,
    retention_minutes: Optional[int] = None
):
    """
    Update stream loop detection configuration.
    
    Args:
        enabled: Whether to enable stream loop detection
        threshold_seconds: Threshold in seconds for detecting stale streams
        check_interval_seconds: How often to check streams (in seconds)
        retention_minutes: How long to keep looping stream IDs (0 = indefinite)
    
    Note: This updates the runtime configuration but does not persist to .env file.
    """
    if threshold_seconds < 60:
        raise HTTPException(status_code=400, detail="Threshold must be at least 60 seconds")
    
    if check_interval_seconds is not None and check_interval_seconds < 5:
        raise HTTPException(status_code=400, detail="Check interval must be at least 5 seconds")
    
    if retention_minutes is not None and retention_minutes < 0:
        raise HTTPException(status_code=400, detail="Retention minutes must be 0 or greater")
    
    # Update config
    cfg.STREAM_LOOP_DETECTION_ENABLED = enabled
    cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = threshold_seconds
    
    if check_interval_seconds is not None:
        cfg.STREAM_LOOP_CHECK_INTERVAL_S = check_interval_seconds
    
    if retention_minutes is not None:
        cfg.STREAM_LOOP_RETENTION_MINUTES = retention_minutes
        looping_streams_tracker.set_retention_minutes(retention_minutes)
    
    # Restart the loop detector if enabled
    if enabled:
        await stream_loop_detector.stop()
        await stream_loop_detector.start()
        logger.info(f"Stream loop detection restarted with threshold {threshold_seconds}s, check_interval {cfg.STREAM_LOOP_CHECK_INTERVAL_S}s")
    else:
        await stream_loop_detector.stop()
        logger.info("Stream loop detection disabled")
    
    # Persist settings to JSON file
    from .services.settings_persistence import SettingsPersistence
    config_to_save = {
        "enabled": enabled,
        "threshold_seconds": threshold_seconds,
        "check_interval_seconds": cfg.STREAM_LOOP_CHECK_INTERVAL_S,
        "retention_minutes": cfg.STREAM_LOOP_RETENTION_MINUTES,
    }
    if SettingsPersistence.save_loop_detection_config(config_to_save):
        logger.info("Loop detection configuration persisted to JSON file")
    
    return {
        "message": "Stream loop detection configuration updated and persisted",
        "enabled": enabled,
        "threshold_seconds": threshold_seconds,
        "threshold_minutes": threshold_seconds / 60,
        "threshold_hours": threshold_seconds / 3600,
        "check_interval_seconds": cfg.STREAM_LOOP_CHECK_INTERVAL_S,
        "retention_minutes": cfg.STREAM_LOOP_RETENTION_MINUTES,
    }

@app.get("/looping-streams")
def get_looping_streams():
    """
    Get list of AceStream IDs that have been detected as looping.
    
    This endpoint is used by the stream proxy to check if a stream is looping
    before selecting an engine. If a stream ID is in this list, the proxy
    should return an error response to prevent playback attempts.
    
    Returns:
        Dict with:
        - stream_ids: List of looping stream IDs
        - streams: Dict mapping stream_id to detection time
        - retention_minutes: Current retention setting (0 = indefinite)
    """
    return {
        "stream_ids": list(looping_streams_tracker.get_looping_stream_ids()),
        "streams": looping_streams_tracker.get_looping_streams(),
        "retention_minutes": looping_streams_tracker.get_retention_minutes() or 0,
    }

@app.delete("/looping-streams/{stream_id}", dependencies=[Depends(require_api_key)])
def remove_looping_stream(stream_id: str):
    """
    Manually remove a stream ID from the looping streams list.
    
    Args:
        stream_id: The AceStream content ID to remove
        
    Returns:
        Success message if removed, error if not found
    """
    if looping_streams_tracker.remove_looping_stream(stream_id):
        return {"message": f"Stream {stream_id} removed from looping list"}
    else:
        raise HTTPException(status_code=404, detail=f"Stream {stream_id} not found in looping list")

@app.post("/looping-streams/clear", dependencies=[Depends(require_api_key)])
def clear_all_looping_streams():
    """
    Clear all looping streams from the tracker.
    
    Returns:
        Success message
    """
    looping_streams_tracker.clear_all()
    return {"message": "All looping streams cleared"}

@app.get("/proxy/config")
def get_proxy_config():
    """
    Get current proxy configuration settings.
    
    Returns proxy buffer and streaming settings that can be adjusted at runtime.
    """
    from .proxy import constants as proxy_constants
    from .proxy.config_helper import Config as ProxyConfig, ConfigHelper
    
    return {
        "vlc_user_agent": proxy_constants.VLC_USER_AGENT,
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "upstream_connect_timeout": ProxyConfig.UPSTREAM_CONNECT_TIMEOUT,
        "upstream_read_timeout": ProxyConfig.UPSTREAM_READ_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "chunk_size": ProxyConfig.CHUNK_SIZE,
        "buffer_chunk_size": ProxyConfig.BUFFER_CHUNK_SIZE,
        "redis_chunk_ttl": ProxyConfig.REDIS_CHUNK_TTL,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "proxy_prebuffer_seconds": ConfigHelper.proxy_prebuffer_seconds(),
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": _resolve_control_mode(ProxyConfig.CONTROL_MODE),
        "legacy_api_preflight_tier": ConfigHelper.legacy_api_preflight_tier(),
        "ace_live_edge_delay": cfg.ACE_LIVE_EDGE_DELAY,
        "engine_variant": f"global-{detect_platform()}",
        # HLS-specific settings
        "hls_max_segments": ProxyConfig.HLS_MAX_SEGMENTS,
        "hls_initial_segments": ProxyConfig.HLS_INITIAL_SEGMENTS,
        "hls_window_size": ProxyConfig.HLS_WINDOW_SIZE,
        "hls_buffer_ready_timeout": ProxyConfig.HLS_BUFFER_READY_TIMEOUT,
        "hls_first_segment_timeout": ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT,
        "hls_initial_buffer_seconds": ProxyConfig.HLS_INITIAL_BUFFER_SECONDS,
        "hls_max_initial_segments": ProxyConfig.HLS_MAX_INITIAL_SEGMENTS,
        "hls_segment_fetch_interval": ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL,
    }

@app.post("/proxy/config", dependencies=[Depends(require_api_key)])
def update_proxy_config(
    initial_data_wait_timeout: Optional[int] = None,
    initial_data_check_interval: Optional[float] = None,
    no_data_timeout_checks: Optional[int] = None,
    no_data_check_interval: Optional[float] = None,
    connection_timeout: Optional[int] = None,
    upstream_connect_timeout: Optional[int] = None,
    upstream_read_timeout: Optional[int] = None,
    stream_timeout: Optional[int] = None,
    channel_shutdown_delay: Optional[int] = None,
    proxy_prebuffer_seconds: Optional[int] = None,
    max_streams_per_engine: Optional[int] = None,
    stream_mode: Optional[str] = None,
    control_mode: Optional[str] = None,
    legacy_api_preflight_tier: Optional[str] = None,
    ace_live_edge_delay: Optional[int] = None,
    # HLS-specific parameters
    hls_max_segments: Optional[int] = None,
    hls_initial_segments: Optional[int] = None,
    hls_window_size: Optional[int] = None,
    hls_buffer_ready_timeout: Optional[int] = None,
    hls_first_segment_timeout: Optional[int] = None,
    hls_initial_buffer_seconds: Optional[int] = None,
    hls_max_initial_segments: Optional[int] = None,
    hls_segment_fetch_interval: Optional[float] = None,
):
    """
    Update proxy configuration settings at runtime.
    
    Args:
        initial_data_wait_timeout: Maximum seconds to wait for initial data in buffer (min: 1, max: 60)
        initial_data_check_interval: Seconds between buffer checks (min: 0.1, max: 2.0)
        no_data_timeout_checks: Number of consecutive empty checks before declaring stream ended (min: 5, max: 600)
        no_data_check_interval: Seconds between checks when no data is available (min: 0.01, max: 1.0)
        connection_timeout: Stream health timeout in seconds (min: 5, max: 60)
        upstream_connect_timeout: Upstream connect timeout in seconds (min: 1, max: 60)
        upstream_read_timeout: Upstream read timeout in seconds (min: 1, max: 120)
        stream_timeout: Stream timeout in seconds (min: 10, max: 300)
        channel_shutdown_delay: Delay before shutting down idle streams in seconds (min: 1, max: 60)
        proxy_prebuffer_seconds: Unified TS/HLS prebuffer holdback in seconds (min: 0, max: 300, 0 disables)
        max_streams_per_engine: Maximum streams per engine before provisioning new engine (min: 1, max: 20)
        stream_mode: Stream mode - 'TS' for MPEG-TS or 'HLS' for HLS streaming
        control_mode: Engine control mode - 'api' (default) or 'http'
        legacy_api_preflight_tier: Legacy API preflight tier - 'light' or 'deep'
        ace_live_edge_delay: Live edge delay in seconds (min: 0)
        hls_max_segments: Maximum HLS segments to buffer (min: 5, max: 100)
        hls_initial_segments: Minimum HLS segments before playback (min: 1, max: 10)
        hls_window_size: Number of segments in HLS manifest window (min: 3, max: 20)
        hls_buffer_ready_timeout: Timeout for HLS initial buffer (min: 5, max: 120)
        hls_first_segment_timeout: Timeout for first HLS segment (min: 5, max: 120)
        hls_initial_buffer_seconds: Target duration for HLS initial buffer (min: 5, max: 60)
        hls_max_initial_segments: Maximum segments to fetch during HLS initial buffering (min: 1, max: 20)
        hls_segment_fetch_interval: Multiplier for HLS manifest fetch interval (min: 0.1, max: 2.0)
    
    Note: This updates the runtime configuration but does not persist to .env file.
    Changes take effect for new streams only.
    """
    from .proxy import constants as proxy_constants
    from .proxy.config_helper import Config as ProxyConfig, ConfigHelper
    
    # Validation and updates
    if initial_data_wait_timeout is not None:
        if initial_data_wait_timeout < 1 or initial_data_wait_timeout > 60:
            raise HTTPException(status_code=400, detail="initial_data_wait_timeout must be between 1 and 60 seconds")
        ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT = initial_data_wait_timeout
    
    if initial_data_check_interval is not None:
        if initial_data_check_interval < 0.1 or initial_data_check_interval > 2.0:
            raise HTTPException(status_code=400, detail="initial_data_check_interval must be between 0.1 and 2.0 seconds")
        ProxyConfig.INITIAL_DATA_CHECK_INTERVAL = initial_data_check_interval
    
    if no_data_timeout_checks is not None:
        if no_data_timeout_checks < 5 or no_data_timeout_checks > 600:
            raise HTTPException(status_code=400, detail="no_data_timeout_checks must be between 5 and 600")
        ProxyConfig.NO_DATA_TIMEOUT_CHECKS = no_data_timeout_checks
    
    if no_data_check_interval is not None:
        if no_data_check_interval < 0.01 or no_data_check_interval > 1.0:
            raise HTTPException(status_code=400, detail="no_data_check_interval must be between 0.01 and 1.0 seconds")
        ProxyConfig.NO_DATA_CHECK_INTERVAL = no_data_check_interval
    
    if connection_timeout is not None:
        if connection_timeout < 5 or connection_timeout > 60:
            raise HTTPException(status_code=400, detail="connection_timeout must be between 5 and 60 seconds")
        ProxyConfig.CONNECTION_TIMEOUT = connection_timeout

    if upstream_connect_timeout is not None:
        if upstream_connect_timeout < 1 or upstream_connect_timeout > 60:
            raise HTTPException(status_code=400, detail="upstream_connect_timeout must be between 1 and 60 seconds")
        ProxyConfig.UPSTREAM_CONNECT_TIMEOUT = upstream_connect_timeout

    if upstream_read_timeout is not None:
        if upstream_read_timeout < 1 or upstream_read_timeout > 120:
            raise HTTPException(status_code=400, detail="upstream_read_timeout must be between 1 and 120 seconds")
        ProxyConfig.UPSTREAM_READ_TIMEOUT = upstream_read_timeout
    
    if stream_timeout is not None:
        if stream_timeout < 10 or stream_timeout > 300:
            raise HTTPException(status_code=400, detail="stream_timeout must be between 10 and 300 seconds")
        ProxyConfig.STREAM_TIMEOUT = stream_timeout
    
    if channel_shutdown_delay is not None:
        if channel_shutdown_delay < 1 or channel_shutdown_delay > 60:
            raise HTTPException(status_code=400, detail="channel_shutdown_delay must be between 1 and 60 seconds")
        ProxyConfig.CHANNEL_SHUTDOWN_DELAY = channel_shutdown_delay

    if proxy_prebuffer_seconds is not None:
        if proxy_prebuffer_seconds < 0 or proxy_prebuffer_seconds > 300:
            raise HTTPException(status_code=400, detail="proxy_prebuffer_seconds must be between 0 and 300 seconds")
        ProxyConfig.PROXY_PREBUFFER_SECONDS = int(proxy_prebuffer_seconds)
    
    if max_streams_per_engine is not None:
        if max_streams_per_engine < 1 or max_streams_per_engine > 20:
            raise HTTPException(status_code=400, detail="max_streams_per_engine must be between 1 and 20")
        cfg.MAX_STREAMS_PER_ENGINE = max_streams_per_engine
    
    if stream_mode is not None:
        if stream_mode not in ['TS', 'HLS']:
            raise HTTPException(status_code=400, detail="stream_mode must be either 'TS' or 'HLS'")
        
        ProxyConfig.STREAM_MODE = stream_mode

    if control_mode is not None:
        normalized_control_mode = normalize_proxy_mode(control_mode, default=None)
        if normalized_control_mode not in [PROXY_MODE_HTTP, PROXY_MODE_API]:
            raise HTTPException(status_code=400, detail="control_mode must be either 'http' or 'api'")

        ProxyConfig.CONTROL_MODE = normalized_control_mode

    if legacy_api_preflight_tier is not None:
        normalized_tier = str(legacy_api_preflight_tier).strip().lower()
        if normalized_tier not in ['light', 'deep']:
            raise HTTPException(status_code=400, detail="legacy_api_preflight_tier must be either 'light' or 'deep'")
        ProxyConfig.LEGACY_API_PREFLIGHT_TIER = normalized_tier

    if ace_live_edge_delay is not None:
        if ace_live_edge_delay < 0:
            raise HTTPException(status_code=400, detail="ace_live_edge_delay must be >= 0")
        cfg.ACE_LIVE_EDGE_DELAY = ace_live_edge_delay

    # HLS-specific settings validation and updates
    if hls_max_segments is not None:
        if hls_max_segments < 5 or hls_max_segments > 100:
            raise HTTPException(status_code=400, detail="hls_max_segments must be between 5 and 100")
        ProxyConfig.HLS_MAX_SEGMENTS = hls_max_segments
    
    if hls_initial_segments is not None:
        if hls_initial_segments < 1 or hls_initial_segments > 10:
            raise HTTPException(status_code=400, detail="hls_initial_segments must be between 1 and 10")
        ProxyConfig.HLS_INITIAL_SEGMENTS = hls_initial_segments
    
    if hls_window_size is not None:
        if hls_window_size < 3 or hls_window_size > 20:
            raise HTTPException(status_code=400, detail="hls_window_size must be between 3 and 20")
        ProxyConfig.HLS_WINDOW_SIZE = hls_window_size
    
    if hls_buffer_ready_timeout is not None:
        if hls_buffer_ready_timeout < 5 or hls_buffer_ready_timeout > 120:
            raise HTTPException(status_code=400, detail="hls_buffer_ready_timeout must be between 5 and 120 seconds")
        ProxyConfig.HLS_BUFFER_READY_TIMEOUT = hls_buffer_ready_timeout
    
    if hls_first_segment_timeout is not None:
        if hls_first_segment_timeout < 5 or hls_first_segment_timeout > 120:
            raise HTTPException(status_code=400, detail="hls_first_segment_timeout must be between 5 and 120 seconds")
        ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT = hls_first_segment_timeout
    
    if hls_initial_buffer_seconds is not None:
        if hls_initial_buffer_seconds < 5 or hls_initial_buffer_seconds > 60:
            raise HTTPException(status_code=400, detail="hls_initial_buffer_seconds must be between 5 and 60 seconds")
        ProxyConfig.HLS_INITIAL_BUFFER_SECONDS = hls_initial_buffer_seconds
    
    if hls_max_initial_segments is not None:
        if hls_max_initial_segments < 1 or hls_max_initial_segments > 20:
            raise HTTPException(status_code=400, detail="hls_max_initial_segments must be between 1 and 20")
        ProxyConfig.HLS_MAX_INITIAL_SEGMENTS = hls_max_initial_segments
    
    if hls_segment_fetch_interval is not None:
        if hls_segment_fetch_interval < 0.1 or hls_segment_fetch_interval > 2.0:
            raise HTTPException(status_code=400, detail="hls_segment_fetch_interval must be between 0.1 and 2.0")
        ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL = hls_segment_fetch_interval
    
    logger.info(
        f"Proxy configuration updated: "
        f"initial_data_wait_timeout={ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT}, "
        f"initial_data_check_interval={ProxyConfig.INITIAL_DATA_CHECK_INTERVAL}, "
        f"no_data_timeout_checks={ProxyConfig.NO_DATA_TIMEOUT_CHECKS}, "
        f"no_data_check_interval={ProxyConfig.NO_DATA_CHECK_INTERVAL}, "
        f"connection_timeout={ProxyConfig.CONNECTION_TIMEOUT}, "
        f"upstream_connect_timeout={ProxyConfig.UPSTREAM_CONNECT_TIMEOUT}, "
        f"upstream_read_timeout={ProxyConfig.UPSTREAM_READ_TIMEOUT}, "
        f"stream_timeout={ProxyConfig.STREAM_TIMEOUT}, "
        f"channel_shutdown_delay={ProxyConfig.CHANNEL_SHUTDOWN_DELAY}, "
        f"proxy_prebuffer_seconds={int(ProxyConfig.PROXY_PREBUFFER_SECONDS)}, "
        f"max_streams_per_engine={cfg.MAX_STREAMS_PER_ENGINE}, "
        f"stream_mode={ProxyConfig.STREAM_MODE}, "
        f"control_mode={_resolve_control_mode(ProxyConfig.CONTROL_MODE)}, "
        f"legacy_api_preflight_tier={str(ProxyConfig.LEGACY_API_PREFLIGHT_TIER).strip().lower()}, "
        f"ace_live_edge_delay={cfg.ACE_LIVE_EDGE_DELAY}"
    )
    
    # Persist settings to JSON file
    from .services.settings_persistence import SettingsPersistence
    config_to_save = {
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "upstream_connect_timeout": ProxyConfig.UPSTREAM_CONNECT_TIMEOUT,
        "upstream_read_timeout": ProxyConfig.UPSTREAM_READ_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "proxy_prebuffer_seconds": int(ProxyConfig.PROXY_PREBUFFER_SECONDS),
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": _resolve_control_mode(ProxyConfig.CONTROL_MODE),
        "legacy_api_preflight_tier": str(ProxyConfig.LEGACY_API_PREFLIGHT_TIER).strip().lower(),
        "ace_live_edge_delay": cfg.ACE_LIVE_EDGE_DELAY,
        # HLS-specific settings
        "hls_max_segments": ProxyConfig.HLS_MAX_SEGMENTS,
        "hls_initial_segments": ProxyConfig.HLS_INITIAL_SEGMENTS,
        "hls_window_size": ProxyConfig.HLS_WINDOW_SIZE,
        "hls_buffer_ready_timeout": ProxyConfig.HLS_BUFFER_READY_TIMEOUT,
        "hls_first_segment_timeout": ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT,
        "hls_initial_buffer_seconds": ProxyConfig.HLS_INITIAL_BUFFER_SECONDS,
        "hls_max_initial_segments": ProxyConfig.HLS_MAX_INITIAL_SEGMENTS,
        "hls_segment_fetch_interval": ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL,
    }
    if SettingsPersistence.save_proxy_config(config_to_save):
        logger.info("Proxy configuration persisted to RuntimeSettings DB")
    
    return {
        "message": "Proxy configuration updated and persisted",
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "upstream_connect_timeout": ProxyConfig.UPSTREAM_CONNECT_TIMEOUT,
        "upstream_read_timeout": ProxyConfig.UPSTREAM_READ_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "proxy_prebuffer_seconds": int(ProxyConfig.PROXY_PREBUFFER_SECONDS),
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": _resolve_control_mode(ProxyConfig.CONTROL_MODE),
        "legacy_api_preflight_tier": str(ProxyConfig.LEGACY_API_PREFLIGHT_TIER).strip().lower(),
        "ace_live_edge_delay": cfg.ACE_LIVE_EDGE_DELAY,
        # HLS-specific settings
        "hls_max_segments": ProxyConfig.HLS_MAX_SEGMENTS,
        "hls_initial_segments": ProxyConfig.HLS_INITIAL_SEGMENTS,
        "hls_window_size": ProxyConfig.HLS_WINDOW_SIZE,
        "hls_buffer_ready_timeout": ProxyConfig.HLS_BUFFER_READY_TIMEOUT,
        "hls_first_segment_timeout": ProxyConfig.HLS_FIRST_SEGMENT_TIMEOUT,
        "hls_initial_buffer_seconds": ProxyConfig.HLS_INITIAL_BUFFER_SECONDS,
        "hls_max_initial_segments": ProxyConfig.HLS_MAX_INITIAL_SEGMENTS,
        "hls_segment_fetch_interval": ProxyConfig.HLS_SEGMENT_FETCH_INTERVAL,
    }



# ============================================================================
# Orchestrator & VPN Settings Endpoints
# ============================================================================

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


@app.get("/settings/orchestrator")
def get_orchestrator_settings():
    """Get current orchestrator core configuration settings."""
    from .services.settings_persistence import SettingsPersistence

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

    # Return defaults from runtime cfg
    # Persist defaults so they appear on next load
    try:
        SettingsPersistence.save_orchestrator_config(defaults)
    except Exception:
        pass
    return defaults


@app.post("/settings/orchestrator", dependencies=[Depends(require_api_key)])
async def update_orchestrator_settings(settings: OrchestratorSettingsUpdate):
    """Update orchestrator core configuration settings at runtime and persist."""
    from .services.settings_persistence import SettingsPersistence

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
            start, end = v.split('-')
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
            raise HTTPException(status_code=400, detail="circuit_breaker_replacement_threshold must be >= 1")
        current["circuit_breaker_replacement_threshold"] = settings.circuit_breaker_replacement_threshold
        cfg.CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD = settings.circuit_breaker_replacement_threshold

    if settings.circuit_breaker_replacement_timeout_s is not None:
        if settings.circuit_breaker_replacement_timeout_s < 1:
            raise HTTPException(status_code=400, detail="circuit_breaker_replacement_timeout_s must be >= 1")
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
        logger.info(f"Orchestrator settings persisted")
    else:
        logger.warning("Failed to persist orchestrator settings")

    return {"message": "Orchestrator settings updated and persisted", **current}


@app.get("/settings/vpn", response_model=VPNSettingsResponse)
def get_vpn_settings():
    """Get current dynamic VPN configuration settings."""
    from .services.settings_persistence import SettingsPersistence

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
        return merged

    try:
        SettingsPersistence.save_vpn_config(defaults)
    except Exception:
        pass
    return defaults


@app.post("/settings/vpn", dependencies=[Depends(require_api_key)])
async def update_vpn_settings(settings: VPNSettingsUpdate):
    """Update dynamic VPN configuration settings at runtime and persist."""
    from .services.settings_persistence import SettingsPersistence

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
    }

    previously_enabled = bool(current.get("enabled", False))

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

    # Dynamic controller mode is mandatory for VPN mode.
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
    addresses: Optional[str] = None
    wireguard_addresses: Optional[str] = None
    endpoint: Optional[str] = None
    endpoints: Optional[str] = None
    wireguard_endpoints: Optional[str] = None
    source: Optional[str] = None
    port_forwarding: Optional[bool] = True


class SettingsBundleUpdate(BaseModel):
    engine_config: Optional[Dict[str, Any]] = None
    engine_settings: Optional[Dict[str, Any]] = None
    orchestrator_settings: Optional[Dict[str, Any]] = None
    proxy_settings: Optional[Dict[str, Any]] = None
    vpn_settings: Optional[Dict[str, Any]] = None


@app.get("/settings")
def get_settings_bundle():
    """Return the consolidated DB-backed runtime settings payload."""
    from .services.settings_persistence import SettingsPersistence

    return SettingsPersistence.load_all_settings()


@app.post("/settings", dependencies=[Depends(require_api_key)])
def update_settings_bundle(payload: SettingsBundleUpdate):
    """Patch one or more settings categories in a single call."""
    from .services.settings_persistence import SettingsPersistence
    from .proxy.config_helper import Config as ProxyConfig

    updates = payload.model_dump(exclude_none=True)
    applied: Dict[str, bool] = {}

    if "engine_config" in updates:
        applied["engine_config"] = bool(SettingsPersistence.save_engine_config(updates["engine_config"]))
    if "engine_settings" in updates:
        applied["engine_settings"] = bool(SettingsPersistence.save_engine_settings(updates["engine_settings"]))
    if "orchestrator_settings" in updates:
        applied["orchestrator_settings"] = bool(SettingsPersistence.save_orchestrator_config(updates["orchestrator_settings"]))
    if "proxy_settings" in updates:
        applied["proxy_settings"] = bool(SettingsPersistence.save_proxy_config(updates["proxy_settings"]))
    if "vpn_settings" in updates:
        applied["vpn_settings"] = bool(SettingsPersistence.save_vpn_config(updates["vpn_settings"]))

    if any(not ok for ok in applied.values()):
        raise HTTPException(status_code=500, detail={"message": "failed to persist one or more settings groups", "applied": applied})

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
                    _value = 1
                setattr(cfg, _cfg_attr, _value)

    if "proxy_settings" in updates:
        proxy_settings = SettingsPersistence.load_proxy_config() or {}
        if 'initial_data_wait_timeout' in proxy_settings:
            ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT = proxy_settings['initial_data_wait_timeout']
        if 'initial_data_check_interval' in proxy_settings:
            ProxyConfig.INITIAL_DATA_CHECK_INTERVAL = proxy_settings['initial_data_check_interval']
        if 'no_data_timeout_checks' in proxy_settings:
            ProxyConfig.NO_DATA_TIMEOUT_CHECKS = proxy_settings['no_data_timeout_checks']
        if 'no_data_check_interval' in proxy_settings:
            ProxyConfig.NO_DATA_CHECK_INTERVAL = proxy_settings['no_data_check_interval']
        if 'connection_timeout' in proxy_settings:
            ProxyConfig.CONNECTION_TIMEOUT = proxy_settings['connection_timeout']
        if 'stream_timeout' in proxy_settings:
            ProxyConfig.STREAM_TIMEOUT = proxy_settings['stream_timeout']
        if 'channel_shutdown_delay' in proxy_settings:
            ProxyConfig.CHANNEL_SHUTDOWN_DELAY = proxy_settings['channel_shutdown_delay']
        if 'proxy_prebuffer_seconds' in proxy_settings:
            ProxyConfig.PROXY_PREBUFFER_SECONDS = max(0, int(proxy_settings['proxy_prebuffer_seconds']))
        if 'stream_mode' in proxy_settings:
            ProxyConfig.STREAM_MODE = proxy_settings['stream_mode']
        if 'control_mode' in proxy_settings:
            ProxyConfig.CONTROL_MODE = _resolve_control_mode(proxy_settings['control_mode'])
        if 'legacy_api_preflight_tier' in proxy_settings:
            tier = str(proxy_settings['legacy_api_preflight_tier']).strip().lower()
            if tier in ['light', 'deep']:
                ProxyConfig.LEGACY_API_PREFLIGHT_TIER = tier
        if 'max_streams_per_engine' in proxy_settings:
            cfg.MAX_STREAMS_PER_ENGINE = int(proxy_settings['max_streams_per_engine'])
        if 'ace_live_edge_delay' in proxy_settings:
            cfg.ACE_LIVE_EDGE_DELAY = max(0, int(proxy_settings['ace_live_edge_delay']))

    if "vpn_settings" in updates:
        vpn_settings = SettingsPersistence.load_vpn_config() or {}
        if 'api_port' in vpn_settings:
            cfg.GLUETUN_API_PORT = int(vpn_settings['api_port'])
        if 'health_check_interval_s' in vpn_settings:
            cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S = int(vpn_settings['health_check_interval_s'])
        if 'port_cache_ttl_s' in vpn_settings:
            cfg.GLUETUN_PORT_CACHE_TTL_S = int(vpn_settings['port_cache_ttl_s'])
        if 'restart_engines_on_reconnect' in vpn_settings:
            cfg.VPN_RESTART_ENGINES_ON_RECONNECT = bool(vpn_settings['restart_engines_on_reconnect'])
        if 'unhealthy_restart_timeout_s' in vpn_settings:
            cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S = int(vpn_settings['unhealthy_restart_timeout_s'])
        if 'preferred_engines_per_vpn' in vpn_settings:
            cfg.PREFERRED_ENGINES_PER_VPN = max(1, int(vpn_settings['preferred_engines_per_vpn']))
        if 'provider' in vpn_settings:
            cfg.VPN_PROVIDER = str(vpn_settings['provider'] or cfg.VPN_PROVIDER).strip().lower() or cfg.VPN_PROVIDER
        if 'protocol' in vpn_settings:
            cfg.VPN_PROTOCOL = str(vpn_settings['protocol'] or cfg.VPN_PROTOCOL).strip().lower() or cfg.VPN_PROTOCOL
        cfg.DYNAMIC_VPN_MANAGEMENT = True

    return {
        "message": "Settings updated",
        "applied": applied,
        "settings": SettingsPersistence.load_all_settings(),
    }


@app.get("/vpn/config", response_model=VPNSettingsResponse)
def get_vpn_config_legacy_alias():
    """Legacy alias for VPN settings endpoint."""
    return get_vpn_settings()


@app.post("/vpn/config", dependencies=[Depends(require_api_key)])
async def update_vpn_config_legacy_alias(settings: VPNSettingsUpdate):
    """Legacy alias for VPN settings endpoint."""
    return await update_vpn_settings(settings)


@app.get("/engine/config")
def get_engine_config_legacy_alias():
    """Legacy alias for engine config endpoint."""
    return get_engine_config_endpoint()


@app.post("/engine/config", dependencies=[Depends(require_api_key)])
def update_engine_config_legacy_alias(config: EngineConfig):
    """Legacy alias for engine config endpoint."""
    return update_engine_config_endpoint(config)


def _load_vpn_settings_for_credential_ops() -> Dict[str, Any]:
    from .services.settings_persistence import SettingsPersistence

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
    }


@app.post("/settings/vpn/credentials", dependencies=[Depends(require_api_key)])
async def add_vpn_credential(credential: VPNCredentialUpsert):
    """Add a single VPN credential and persist immediately."""
    from .services.settings_persistence import SettingsPersistence

    current = _load_vpn_settings_for_credential_ops()
    credentials = list(current.get("credentials") or [])

    payload = credential.model_dump(exclude_none=True)
    payload["id"] = str(payload.get("id") or uuid4())

    provider = str(payload.get("provider") or current.get("provider") or cfg.VPN_PROVIDER or "").strip().lower()
    if provider:
        payload["provider"] = provider

    protocol = str(payload.get("protocol") or current.get("protocol") or cfg.VPN_PROTOCOL or "wireguard").strip().lower()
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


@app.delete("/settings/vpn/credentials/{credential_id}", dependencies=[Depends(require_api_key)])
async def delete_vpn_credential(credential_id: str):
    """Delete a VPN credential by ID and persist immediately."""
    from .services.settings_persistence import SettingsPersistence

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


# ============================================================================
# Engine Settings Endpoints
# ============================================================================

class EngineSettingsUpdate(BaseModel):
    """Model for updating engine settings."""
    min_replicas: Optional[int] = None
    max_replicas: Optional[int] = None
    auto_delete: Optional[bool] = None
    manual_mode: Optional[bool] = None
    manual_engines: Optional[List[Dict[str, Any]]] = None  # list of {"host": "ip", "port": port}

    # Global engine customization fields
    download_limit: Optional[int] = None
    upload_limit: Optional[int] = None
    live_cache_type: Optional[str] = None
    buffer_time: Optional[int] = None
    memory_limit: Optional[str] = None
    parameters: Optional[List[Dict[str, Any]]] = None
    torrent_folder_mount_enabled: Optional[bool] = None
    torrent_folder_host_path: Optional[str] = None
    torrent_folder_container_path: Optional[str] = None
    disk_cache_mount_enabled: Optional[bool] = None
    disk_cache_prune_enabled: Optional[bool] = None
    disk_cache_prune_interval: Optional[int] = None

@app.get("/settings/engine")
def get_engine_settings():
    """Get current engine configuration settings."""
    from .services.engine_config import (
        EngineConfig,
        detect_platform,
        get_config as get_engine_config,
        resolve_engine_image,
    )
    
    from .services.settings_persistence import SettingsPersistence

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

@app.post("/settings/engine", dependencies=[Depends(require_api_key)])
async def update_engine_settings(settings: EngineSettingsUpdate):
    """
    Update engine configuration settings.
    
    Args:
        settings: Engine settings to update
    
    Note: Changes are persisted to JSON and will be applied on next restart.
    Some changes may require reprovisioning engines.
    """
    from .services.settings_persistence import SettingsPersistence
    from .services.engine_config import (
        EngineConfig,
        RESTRICTED_FLAGS,
        detect_platform,
        get_config as get_engine_config,
        resolve_engine_image,
        save_config as save_engine_config,
    )

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
    
    # Validation and updates
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
    
    # Validate min_replicas <= max_replicas
    if current_settings["min_replicas"] > current_settings["max_replicas"]:
        raise HTTPException(status_code=400, detail="min_replicas must be <= max_replicas")
    
    if settings.auto_delete is not None:
        current_settings["auto_delete"] = settings.auto_delete
        cfg.AUTO_DELETE = settings.auto_delete
            
    if settings.manual_mode is not None:
        current_settings["manual_mode"] = settings.manual_mode
        
    if settings.manual_engines is not None:
        current_settings["manual_engines"] = settings.manual_engines
        
    # Inject manual engines into state if manual mode is enabled
    if current_settings.get("manual_mode"):
        # Clear existing manual engines first
        manual_keys = [k for k in state.engines.keys() if k.startswith("manual-")]
        for k in manual_keys:
            state.remove_engine(k)
            
        for engine in current_settings.get("manual_engines", []):
            host = engine.get("host")
            port = engine.get("port")
            if host and port:
                container_id = f"manual-{host}-{port}"
                
                # Check if it already exists to preserve some state like last_seen
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
                        health_status="unknown"
                    )
                else:
                    existing.last_seen = state.now()

    # Apply global engine customization payload.
    existing_engine_config = get_engine_config() or EngineConfig()
    engine_payload = existing_engine_config.model_dump(mode="json")

    for field in (
        "download_limit",
        "upload_limit",
        "live_cache_type",
        "buffer_time",
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
    
    # Persist settings to JSON file
    if SettingsPersistence.save_engine_settings(current_settings):
        logger.info(f"Engine settings persisted: {current_settings}")
    else:
        logger.warning("Failed to persist engine settings to JSON file")

    # Keep desired replicas within the updated [MIN_REPLICAS, MAX_REPLICAS] window
    # so settings changes take effect immediately without waiting for future demand recompute.
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

    engine_controller.request_reconcile(reason="engine_settings_update")
    rollout = _trigger_engine_generation_rollout(reason="engine_settings_update")
    
    return {
        "message": "Engine settings updated and persisted",
        **current_settings,
        **updated_engine_config.model_dump(mode="json"),
        "platform": current_platform,
        "image": resolve_engine_image(current_platform),
        "rolling_update": {
            "changed": bool(rollout.get("changed")),
            "target_generation": rollout.get("generation"),
            "target_hash": rollout.get("config_hash"),
        },
    }


# ============================================================================
# Settings Backup/Restore Endpoints
# ============================================================================

# Backup format version - increment when backup structure changes
BACKUP_FORMAT_VERSION = "2.0"

@app.get("/settings/export")
async def export_settings(api_key_param: str = Depends(require_api_key)):
    """
    Export all settings (engine config, engine settings, proxy, loop detection) as a ZIP file.
    
    Returns:
        ZIP file containing all settings as JSON files
    """
    import zipfile
    import io
    
    try:
        # Create an in-memory ZIP file
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Export global engine config
            try:
                from .services.engine_config import get_config as get_engine_config
                engine_config = get_engine_config()
                if engine_config:
                    config_json = json.dumps(engine_config.model_dump(mode="json"), indent=2)
                    zip_file.writestr("engine_config.json", config_json)
                    logger.info("Added global engine config to backup")
            except Exception as e:
                logger.warning(f"Failed to export global engine config: {e}")
            
            # Export proxy settings
            try:
                from .proxy.config_helper import Config as ProxyConfig, ConfigHelper
                proxy_settings = {
                    "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
                    "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
                    "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
                    "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
                    "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
                    "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
                    "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
                    "proxy_prebuffer_seconds": ConfigHelper.proxy_prebuffer_seconds(),
                    "stream_mode": ProxyConfig.STREAM_MODE,
                }
                proxy_json = json.dumps(proxy_settings, indent=2)
                zip_file.writestr("proxy_settings.json", proxy_json)
                logger.info("Added proxy settings to backup")
            except Exception as e:
                logger.warning(f"Failed to export proxy settings: {e}")
            
            # Export loop detection settings
            try:
                loop_settings = {
                    "enabled": cfg.STREAM_LOOP_DETECTION_ENABLED,
                    "threshold_seconds": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S,
                    "check_interval_seconds": cfg.STREAM_LOOP_CHECK_INTERVAL_S,
                    "retention_minutes": cfg.STREAM_LOOP_RETENTION_MINUTES,
                }
                loop_json = json.dumps(loop_settings, indent=2)
                zip_file.writestr("loop_detection_settings.json", loop_json)
                logger.info("Added loop detection settings to backup")
            except Exception as e:
                logger.warning(f"Failed to export loop detection settings: {e}")
            
            # Export engine settings
            try:
                from .services.settings_persistence import SettingsPersistence
                engine_settings = SettingsPersistence.load_engine_settings()
                if engine_settings:
                    engine_json = json.dumps(engine_settings, indent=2)
                    zip_file.writestr("engine_settings.json", engine_json)
                    logger.info("Added engine settings to backup")
            except Exception as e:
                logger.warning(f"Failed to export engine settings: {e}")
            
            # Add metadata
            metadata = {
                "export_date": datetime.now(timezone.utc).isoformat(),
                "version": BACKUP_FORMAT_VERSION,
                "description": "AceStream Orchestrator Settings Backup"
            }
            zip_file.writestr("metadata.json", json.dumps(metadata, indent=2))
        
        # Prepare the ZIP for download
        zip_buffer.seek(0)
        
        from starlette.responses import StreamingResponse
        return StreamingResponse(
            io.BytesIO(zip_buffer.getvalue()),
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename=orchestrator_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            }
        )
    except Exception as e:
        logger.error(f"Failed to export settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to export settings: {str(e)}")


@app.post("/settings/import")
async def import_settings_data(
    request: Request,
    import_engine_config: bool = Query(True),
    import_proxy: bool = Query(True),
    import_loop_detection: bool = Query(True),
    import_engine: bool = Query(True),
    import_custom_variant: Optional[bool] = Query(None),
    import_templates: Optional[bool] = Query(None),
    api_key_param: str = Depends(require_api_key)
):
    """
    Import settings from uploaded ZIP file data.
    
    Query Parameters:
        import_engine_config: Whether to import global engine customization
        import_proxy: Whether to import proxy settings
        import_loop_detection: Whether to import loop detection settings
        import_engine: Whether to import engine settings
    
    Returns:
        Summary of imported settings
    """
    import zipfile
    import io
    from .services.engine_config import EngineConfig, reload_config as reload_engine_config, save_config as save_engine_config
    from .services.settings_persistence import SettingsPersistence
    
    try:
        # Read the uploaded file data
        file_data = await request.body()
        
        if not file_data:
            raise HTTPException(status_code=400, detail="No file data provided")
        
        imported = {
            "engine_config": False,
            "proxy": False,
            "loop_detection": False,
            "engine": False,
            "errors": []
        }

        # Backward compatibility: old clients use import_custom_variant.
        effective_import_engine_config = bool(import_engine_config)
        if import_custom_variant is not None:
            effective_import_engine_config = bool(import_custom_variant)
        
        # Create a BytesIO object from the uploaded data
        zip_buffer = io.BytesIO(file_data)
        
        with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
            # Import global engine config (new format), with fallback to legacy custom file.
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
            
            # Import proxy settings
            if import_proxy and "proxy_settings.json" in zip_file.namelist():
                try:
                    from .proxy.config_helper import Config as ProxyConfig
                    proxy_data = zip_file.read("proxy_settings.json").decode('utf-8')
                    proxy_dict = json.loads(proxy_data)
                    
                    ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT = proxy_dict.get('initial_data_wait_timeout', ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT)
                    ProxyConfig.INITIAL_DATA_CHECK_INTERVAL = proxy_dict.get('initial_data_check_interval', ProxyConfig.INITIAL_DATA_CHECK_INTERVAL)
                    ProxyConfig.NO_DATA_TIMEOUT_CHECKS = proxy_dict.get('no_data_timeout_checks', ProxyConfig.NO_DATA_TIMEOUT_CHECKS)
                    ProxyConfig.NO_DATA_CHECK_INTERVAL = proxy_dict.get('no_data_check_interval', ProxyConfig.NO_DATA_CHECK_INTERVAL)
                    ProxyConfig.CONNECTION_TIMEOUT = proxy_dict.get('connection_timeout', ProxyConfig.CONNECTION_TIMEOUT)
                    ProxyConfig.STREAM_TIMEOUT = proxy_dict.get('stream_timeout', ProxyConfig.STREAM_TIMEOUT)
                    ProxyConfig.CHANNEL_SHUTDOWN_DELAY = proxy_dict.get('channel_shutdown_delay', ProxyConfig.CHANNEL_SHUTDOWN_DELAY)
                    
                    # Validate and set stream_mode
                    if 'stream_mode' in proxy_dict:
                        ProxyConfig.STREAM_MODE = proxy_dict['stream_mode']
                    
                    # Persist to file
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
            
            # Import loop detection settings
            if import_loop_detection and "loop_detection_settings.json" in zip_file.namelist():
                try:
                    loop_data = zip_file.read("loop_detection_settings.json").decode('utf-8')
                    loop_dict = json.loads(loop_data)
                    
                    cfg.STREAM_LOOP_DETECTION_ENABLED = loop_dict.get('enabled', cfg.STREAM_LOOP_DETECTION_ENABLED)
                    cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = loop_dict.get('threshold_seconds', cfg.STREAM_LOOP_DETECTION_THRESHOLD_S)
                    cfg.STREAM_LOOP_CHECK_INTERVAL_S = loop_dict.get('check_interval_seconds', cfg.STREAM_LOOP_CHECK_INTERVAL_S)
                    cfg.STREAM_LOOP_RETENTION_MINUTES = loop_dict.get('retention_minutes', cfg.STREAM_LOOP_RETENTION_MINUTES)
                    
                    # Update tracker retention
                    looping_streams_tracker.set_retention_minutes(cfg.STREAM_LOOP_RETENTION_MINUTES)
                    
                    # Persist to file
                    if SettingsPersistence.save_loop_detection_config(loop_dict):
                        imported["loop_detection"] = True
                        logger.info("Imported loop detection settings")
                    else:
                        error_msg = "Failed to persist loop detection settings to file"
                        logger.error(error_msg)
                        imported["errors"].append(error_msg)
                except Exception as e:
                    error_msg = f"Failed to import loop detection settings: {e}"
                    logger.error(error_msg)
                    imported["errors"].append(error_msg)
            
            # Import engine settings
            if import_engine and "engine_settings.json" in zip_file.namelist():
                try:
                    engine_data = zip_file.read("engine_settings.json").decode('utf-8')
                    engine_dict = json.loads(engine_data)
                    
                    # Update runtime config
                    if 'min_replicas' in engine_dict:
                        cfg.MIN_REPLICAS = engine_dict['min_replicas']
                    if 'max_replicas' in engine_dict:
                        cfg.MAX_REPLICAS = engine_dict['max_replicas']
                    if 'auto_delete' in engine_dict:
                        cfg.AUTO_DELETE = engine_dict['auto_delete']
                    
                    # Persist to file
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
        
        return {
            "message": "Settings imported successfully",
            "imported": imported
        }
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")
    except Exception as e:
        logger.error(f"Failed to import settings: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to import settings: {str(e)}")

# --- Cache Management ---

@app.get("/engine-cache/stats", tags=["Cache"])
async def engine_cache_stats(api_key: str = Depends(require_api_key)):
    """Get current cache usage statistics."""
    return state.cache_stats

@app.post("/engine-cache/purge", tags=["Cache"])
async def purge_engine_cache(api_key: str = Depends(require_api_key)):
    """Manually purge all cache volume contents."""
    from .services.engine_cache_manager import engine_cache_manager
    await engine_cache_manager.purge_all_contents()
    return {"status": "success", "message": "All cache volume contents purged"}


# --- M3U Proxy ---

@app.get("/modify_m3u", tags=["M3U"])
async def modify_m3u(
    m3u_url: str = Query(..., description="URL of the source M3U playlist"),
    host: str = Query(..., description="Replacement hostname or IP address"),
    port: str = Query(..., description="Replacement port (1-65535)"),
    timeout: Optional[float] = Query(None, description="HTTP request timeout in seconds (overrides M3U_TIMEOUT env var)"),
    mode: str = Query("default", description="Rewrite mode: 'default' or 'proxy'"),
):
    """Download an M3U playlist from *m3u_url* and rewrite its internal URLs.

    **default mode** – replaces ``http://127.0.0.1:<port>/`` and
    ``http://localhost:<port>/`` origins with ``http://host:port/``, and
    converts ``acestream://<id>`` links to
    ``http://host:port/ace/getstream?id=<id>``.

    **proxy mode** – rewrites every ``http``/``https`` URL as
    ``http://host:port/proxy?url=<percent-encoded-original>`` and similarly
    wraps ``acestream://`` links.
    """
    from .services.m3u import get_m3u_content, validate_host_port, modify_m3u_content

    # Validate mode
    if mode not in ("default", "proxy"):
        raise HTTPException(status_code=400, detail="Parameter 'mode' must be 'default' or 'proxy'.")

    # Validate timeout
    effective_timeout: float
    if timeout is not None:
        if timeout <= 0:
            raise HTTPException(status_code=400, detail="Parameter 'timeout' must be a positive number.")
        effective_timeout = timeout
    else:
        effective_timeout = cfg.M3U_TIMEOUT

    # Validate host and port
    ok, port_or_msg = validate_host_port(host.strip(), port.strip())
    if not ok:
        raise HTTPException(status_code=400, detail=port_or_msg)
    validated_port: int = port_or_msg  # type: ignore[assignment]

    # Download playlist
    content = get_m3u_content(m3u_url.strip(), effective_timeout)
    if content is None:
        raise HTTPException(status_code=400, detail="Failed to download the M3U file.")

    # Rewrite URLs
    modified = modify_m3u_content(content, host.strip(), validated_port, mode)

    return StreamingResponse(
        io.BytesIO(modified.encode("utf-8")),
        media_type="application/x-mpegURL",
        headers={"Content-Disposition": "attachment; filename=\"modified_playlist.m3u\""},
    )


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
