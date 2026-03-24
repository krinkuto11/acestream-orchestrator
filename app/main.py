from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Depends, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel
import asyncio
import io
import os
import json
import logging
import httpx
import threading
from types import SimpleNamespace
import time

from .utils.logging import setup
from .core.config import cfg
from .services.autoscaler import ensure_minimum, scale_to, can_stop_engine
from .services.provisioner import StartRequest, start_container, stop_container, AceProvisionRequest, AceProvisionResponse, start_acestream, HOST_LABEL_HTTP
from .services.health import sweep_idle
from .services.health_monitor import health_monitor
from .services.health_manager import health_manager
from .services.inspect import inspect_container, ContainerNotFound
from .services.state import state, load_state_from_db, cleanup_on_shutdown
from .models.schemas import StreamStartedEvent, StreamEndedEvent, EngineState, StreamState, StreamStatSnapshot, EventLog
from .services.collector import collector
from .services.event_logger import event_logger
from .services.stream_cleanup import stream_cleanup
from .services.monitor import docker_monitor
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
from .services.cache_monitoring_service import start_cache_monitoring
from .services.legacy_stream_monitoring import legacy_stream_monitoring_service
from .proxy.manager import ProxyManager
from .proxy.ace_api_client import AceLegacyApiClient, AceLegacyApiError

logger = logging.getLogger(__name__)

setup()

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
    cleanup_on_shutdown()  # Clean any existing state and containers after DB is ready
    
    # Load custom variant configuration early to ensure it's available
    from .services.custom_variant_config import load_config as load_custom_config, save_config as save_custom_config
    from .services.template_manager import get_active_template_id, get_template, set_active_template, list_templates
    try:
        custom_config = load_custom_config()
        if custom_config and custom_config.enabled:
            logger.info(f"Loaded custom engine variant configuration (platform: {custom_config.platform})")
            
            # If custom variant is enabled, load the active template if one was previously set
            active_template_id = get_active_template_id()
            if active_template_id is not None:
                logger.info(f"Loading previously active template {active_template_id} for custom variant")
                template = get_template(active_template_id)
                if template:
                    # Apply the template configuration, but preserve the enabled state
                    template_config = template.config.model_copy(deep=True)
                    template_config.enabled = custom_config.enabled
                    save_custom_config(template_config)
                    # Ensure active template is set (in case it was only loaded but not set)
                    set_active_template(active_template_id)
                    logger.info(f"Successfully loaded template '{template.name}' (slot {active_template_id})")
                else:
                    logger.warning(f"Active template {active_template_id} not found, using current config")
            else:
                # Custom variant is enabled but no active template
                # Try to auto-load the first available template
                logger.info("Custom variant enabled but no active template, checking for available templates...")
                templates = list_templates()
                first_available = next((t for t in templates if t['exists']), None)
                
                if first_available:
                    logger.info(f"Auto-loading first available template (slot {first_available['slot_id']})")
                    template = get_template(first_available['slot_id'])
                    if template:
                        # Apply the template configuration, but preserve the enabled state
                        template_config = template.config.model_copy(deep=True)
                        template_config.enabled = custom_config.enabled
                        save_custom_config(template_config)
                        # Set as active template
                        set_active_template(first_available['slot_id'])
                        logger.info(f"Successfully auto-loaded template '{template.name}' (slot {first_available['slot_id']})")
                    else:
                        logger.warning(f"Failed to load template from slot {first_available['slot_id']}")
                else:
                    # No templates available - ensure we have a valid platform loaded
                    logger.info("No templates available, using current custom variant config")
                    if not custom_config.platform:
                        logger.warning("Custom variant enabled but no platform set, detecting platform...")
                        from .services.custom_variant_config import detect_platform
                        custom_config.platform = detect_platform()
                        save_custom_config(custom_config)
                        logger.info(f"Set custom variant platform to detected platform: {custom_config.platform}")
        else:
            logger.debug("Custom engine variant is disabled or not configured")
    except Exception as e:
        logger.warning(f"Failed to load custom variant config during startup: {e}")
    
    # Load persisted settings (Proxy and Loop Detection)
    from .services.settings_persistence import SettingsPersistence
    from .proxy.config_helper import Config as ProxyConfig
    
    # Load proxy settings
    try:
        proxy_settings = SettingsPersistence.load_proxy_config()
        if proxy_settings:
            logger.info("Loading persisted proxy settings")
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
            if 'max_streams_per_engine' in proxy_settings:
                cfg.MAX_STREAMS_PER_ENGINE = proxy_settings['max_streams_per_engine']
            if 'stream_mode' in proxy_settings:
                # Validate stream_mode before loading
                mode = proxy_settings['stream_mode']
                ProxyConfig.STREAM_MODE = mode
            if 'control_mode' in proxy_settings:
                mode = str(proxy_settings['control_mode']).upper()
                if mode in ['LEGACY_HTTP', 'LEGACY_API']:
                    ProxyConfig.CONTROL_MODE = mode
            if 'legacy_api_preflight_tier' in proxy_settings:
                tier = str(proxy_settings['legacy_api_preflight_tier']).strip().lower()
                if tier in ['light', 'deep']:
                    ProxyConfig.LEGACY_API_PREFLIGHT_TIER = tier
            logger.info("Proxy settings loaded from persistent storage")
    except Exception as e:
        logger.warning(f"Failed to load persisted proxy settings: {e}")
    
    # Load loop detection settings
    try:
        loop_settings = SettingsPersistence.load_loop_detection_config()
        if loop_settings:
            logger.info("Loading persisted loop detection settings")
            if 'enabled' in loop_settings:
                cfg.STREAM_LOOP_DETECTION_ENABLED = loop_settings['enabled']
            if 'threshold_seconds' in loop_settings:
                cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = loop_settings['threshold_seconds']
            if 'check_interval_seconds' in loop_settings:
                cfg.STREAM_LOOP_CHECK_INTERVAL_S = loop_settings['check_interval_seconds']
            if 'retention_minutes' in loop_settings:
                cfg.STREAM_LOOP_RETENTION_MINUTES = loop_settings['retention_minutes']
            logger.info("Loop detection settings loaded from persistent storage")
    except Exception as e:
        logger.warning(f"Failed to load persisted loop detection settings: {e}")
    
    # Load engine settings
    try:
        engine_settings = SettingsPersistence.load_engine_settings()
        if engine_settings:
            logger.info("Loading persisted engine settings")
            if 'min_replicas' in engine_settings:
                cfg.MIN_REPLICAS = engine_settings['min_replicas']
            if 'max_replicas' in engine_settings:
                cfg.MAX_REPLICAS = engine_settings['max_replicas']
            if 'auto_delete' in engine_settings:
                cfg.AUTO_DELETE = engine_settings['auto_delete']
            if 'engine_variant' in engine_settings:
                # Update engine variant preference
                cfg.ENGINE_VARIANT = engine_settings['engine_variant']
                # Do NOT override custom_config.enabled from engine_settings
                # custom_engine_variant.json should be the source of truth for its own enabled state
                pass
            
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
                        
            logger.info(f"Engine settings loaded from persistent storage: MIN_REPLICAS={cfg.MIN_REPLICAS}, MAX_REPLICAS={cfg.MAX_REPLICAS}, AUTO_DELETE={cfg.AUTO_DELETE}, ENGINE_VARIANT={cfg.ENGINE_VARIANT}, MANUAL_MODE={engine_settings.get('manual_mode', False)}")
        else:
            # No persisted settings found - create default settings from current config
            logger.info("No persisted engine settings found, creating defaults")
            from .services.custom_variant_config import detect_platform
            default_settings = {
                "min_replicas": cfg.MIN_REPLICAS,
                "max_replicas": cfg.MAX_REPLICAS,
                "auto_delete": cfg.AUTO_DELETE,
                "engine_variant": cfg.ENGINE_VARIANT,
                "use_custom_variant": custom_config.enabled if custom_config else False,
                "platform": detect_platform(),
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
            logger.info("Loading persisted orchestrator settings")
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
                    setattr(cfg, _cfg_attr, orchestrator_settings[_json_key])
            logger.info("Orchestrator settings loaded from persistent storage")
    except Exception as e:
        logger.warning(f"Failed to load persisted orchestrator settings: {e}")

    # Load VPN settings
    try:
        vpn_settings = SettingsPersistence.load_vpn_config()
        if vpn_settings:
            logger.info("Loading persisted VPN settings")
            vpn_enabled = vpn_settings.get('enabled', False)
            if 'vpn_mode' in vpn_settings:
                cfg.VPN_MODE = vpn_settings['vpn_mode']
            if vpn_enabled:
                cfg.GLUETUN_CONTAINER_NAME = vpn_settings.get('container_name') or None
                cfg.GLUETUN_CONTAINER_NAME_2 = vpn_settings.get('container_name_2') or None
            else:
                cfg.GLUETUN_CONTAINER_NAME = None
                cfg.GLUETUN_CONTAINER_NAME_2 = None
            if 'api_port' in vpn_settings:
                cfg.GLUETUN_API_PORT = vpn_settings['api_port']
            if 'port_range_1' in vpn_settings:
                cfg.GLUETUN_PORT_RANGE_1 = vpn_settings['port_range_1'] or None
            if 'port_range_2' in vpn_settings:
                cfg.GLUETUN_PORT_RANGE_2 = vpn_settings['port_range_2'] or None
            if 'health_check_interval_s' in vpn_settings:
                cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S = vpn_settings['health_check_interval_s']
            if 'port_cache_ttl_s' in vpn_settings:
                cfg.GLUETUN_PORT_CACHE_TTL_S = vpn_settings['port_cache_ttl_s']
            if 'restart_engines_on_reconnect' in vpn_settings:
                cfg.VPN_RESTART_ENGINES_ON_RECONNECT = vpn_settings['restart_engines_on_reconnect']
            if 'unhealthy_restart_timeout_s' in vpn_settings:
                cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S = vpn_settings['unhealthy_restart_timeout_s']
            logger.info(f"VPN settings loaded from persistent storage: enabled={vpn_enabled}, mode={cfg.VPN_MODE}, container={cfg.GLUETUN_CONTAINER_NAME}")
    except Exception as e:
        logger.warning(f"Failed to load persisted VPN settings: {e}")

    # Load state from database first
    load_state_from_db()
    
    # Initialize looping streams tracker with configured retention
    looping_streams_tracker.set_retention_minutes(cfg.STREAM_LOOP_RETENTION_MINUTES)
    
    async def _provision_worker():
        """Provision engines in background after VPN is healthy."""
        state.enter_reprovisioning_mode()
        
        if cfg.GLUETUN_CONTAINER_NAME:
            logger.info("Waiting for Gluetun to become healthy before provisioning engines...")
            max_wait_time = 60  # Maximum 60 seconds to wait for Gluetun
            wait_start = asyncio.get_event_loop().time()
            
            is_healthy = False
            if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
                logger.info(f"Waiting for both Gluetun containers ({cfg.GLUETUN_CONTAINER_NAME}, {cfg.GLUETUN_CONTAINER_NAME_2}) to become healthy...")
                v1_task = gluetun_monitor.wait_for_healthy(timeout=max_wait_time, container_name=cfg.GLUETUN_CONTAINER_NAME)
                v2_task = gluetun_monitor.wait_for_healthy(timeout=max_wait_time, container_name=cfg.GLUETUN_CONTAINER_NAME_2)
                v1, v2 = await asyncio.gather(v1_task, v2_task)
                is_healthy = v1 and v2
                if is_healthy:
                    logger.info("Both Gluetun containers are healthy - proceeding with engine provisioning")
            else:
                logger.info(f"Waiting for Gluetun ({cfg.GLUETUN_CONTAINER_NAME}) to become healthy...")
                is_healthy = await gluetun_monitor.wait_for_healthy(timeout=max_wait_time)
                if is_healthy:
                    logger.info("Gluetun is healthy - proceeding with engine provisioning")

            if is_healthy:
                # Log VPN location information for all healthy VPN containers
                from .services.gluetun import get_vpn_status
                try:
                    vpn_status = get_vpn_status()
                    
                    # Check VPN1 location
                    if vpn_status.get("vpn1") and vpn_status["vpn1"].get("public_ip"):
                        logger.info(f"VPN1 ({vpn_status['vpn1']['container_name']}) status: "
                                  f"IP={vpn_status['vpn1']['public_ip']}, "
                                  f"Provider={vpn_status['vpn1'].get('provider', 'Unknown')}, "
                                  f"Country={vpn_status['vpn1'].get('country', 'Unknown')}, "
                                  f"City={vpn_status['vpn1'].get('city', 'Unknown')}")
                    
                    # Check VPN2 location (redundant mode)
                    if vpn_status.get("vpn2") and vpn_status["vpn2"].get("public_ip"):
                        logger.info(f"VPN2 ({vpn_status['vpn2']['container_name']}) status: "
                                  f"IP={vpn_status['vpn2']['public_ip']}, "
                                  f"Provider={vpn_status['vpn2'].get('provider', 'Unknown')}, "
                                  f"Country={vpn_status['vpn2'].get('country', 'Unknown')}, "
                                  f"City={vpn_status['vpn2'].get('city', 'Unknown')}")
                    
                    # For single VPN mode
                    if vpn_status.get("mode") == "single" and vpn_status.get("public_ip"):
                        if not vpn_status.get("vpn1"):  # Already logged above if vpn1 exists
                            logger.info(f"VPN ({vpn_status['container_name']}) status: "
                                      f"IP={vpn_status['public_ip']}, "
                                      f"Provider={vpn_status.get('provider', 'Unknown')}, "
                                      f"Country={vpn_status.get('country', 'Unknown')}, "
                                      f"City={vpn_status.get('city', 'Unknown')}")
                    
                except Exception as e:
                    logger.error(f"Failed to get VPN status: {e}")
            else:
                logger.warning(f"Gluetun did not become healthy within {max_wait_time}s - proceeding anyway")
        
        # Now provision engines with Gluetun health checks working
        # On startup, provision MIN_REPLICAS total containers
        # We invoke start_acestream directly here instead of autoscaler to isolate
        # initial provisioning from periodic maintenance logic like circuit breakers
        from app.services.provisioner import start_acestream, AceProvisionRequest
        
        target_count = cfg.MIN_REPLICAS
        logger.info(f"Starting {target_count} AceStream containers for initial startup")
        
        provisioned = 0
        failed = 0
        for i in range(target_count):
            try:
                logger.debug(f"Provisioning initial engine {i+1}/{target_count}")
                req = AceProvisionRequest(labels={}, env={})
                response = start_acestream(req)
                if response and response.container_id:
                    logger.info(f"Successfully started AceStream container {response.container_id[:12]} ({i+1}/{target_count})")
                    provisioned += 1
                else:
                    logger.error(f"Failed to start AceStream container {i+1}/{target_count}: No response")
                    failed += 1
            except Exception as e:
                logger.error(f"Failed to provision engine {i+1}/{target_count}: {e}")
                failed += 1
                
        logger.info(f"Initial provisioning complete: {provisioned}/{target_count} engines started ({failed} failed)")
        
        # Trigger state re-index to ensure visibility across APIs
        reindex_existing()
        state.exit_reprovisioning_mode()

    # Start Gluetun monitoring BEFORE provisioning to avoid race condition
    # This ensures health checks work when ensure_minimum() tries to start engines
    await gluetun_monitor.start()
    asyncio.create_task(_provision_worker())
    
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
    asyncio.create_task(docker_monitor.start())  # Start Docker monitoring
    asyncio.create_task(health_monitor.start())  # Start health monitoring  
    asyncio.create_task(health_manager.start())  # Start proactive health management
    asyncio.create_task(docker_stats_collector.start())  # Start Docker stats collection
    asyncio.create_task(stream_loop_detector.start())  # Start stream loop detection
    asyncio.create_task(stream_loop_detector.start())  # Start stream loop detection
    asyncio.create_task(looping_streams_tracker.start())  # Start looping streams tracker
    
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
    await docker_monitor.stop()  # Stop Docker monitoring
    await health_monitor.stop()  # Stop health monitoring
    await health_manager.stop()  # Stop health management
    await docker_stats_collector.stop()  # Stop Docker stats collector
    await gluetun_monitor.stop()  # Stop Gluetun monitoring
    await stream_loop_detector.stop()  # Stop stream loop detector
    await looping_streams_tracker.stop()  # Stop looping streams tracker
    await legacy_stream_monitoring_service.stop_all()  # Stop legacy monitor sessions
    await stop_cleanup_task()  # Stop cache cleanup
    
    # Give a small delay to ensure any pending operations complete
    await asyncio.sleep(0.1)
    
    cleanup_on_shutdown()

__version__ = "1.5.3"

app = FastAPI(
    title="On-Demand Orchestrator",
    version=__version__,
    lifespan=lifespan
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# Read APIs
@app.get("/engines", response_model=List[EngineState])
def get_engines():
    """Get all engines with Docker verification and VPN health filtering."""
    engines = state.list_engines()
    
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
            
            # In redundant mode, filter by VPN health
            if cfg.VPN_MODE == 'redundant' and engine.vpn_container:
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
                # Single VPN mode or no VPN - include the engine
                verified_engines.append(engine)
        
        # Enrich engines with version info and forwarded port
        for engine in verified_engines:
            # Only set engine_variant if not already set (from labels)
            # This ensures old engines keep their original variant name until reprovisioned
            from .services.custom_variant_config import is_custom_variant_enabled
            if not engine.engine_variant:
                # Engine has no variant set from labels, use current configuration
                if is_custom_variant_enabled():
                    # For custom variants, use the template name as engine_variant
                    template_name = get_active_template_name()
                    if template_name:
                        engine.engine_variant = template_name
                    else:
                        # Fallback to config if no template name
                        engine.engine_variant = cfg.ENGINE_VARIANT
                else:
                    # For standard variants, use configured variant name
                    engine.engine_variant = cfg.ENGINE_VARIANT
            
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
        
        # Sort engines by port number for consistent ordering
        verified_engines.sort(key=lambda e: e.port)
        return verified_engines
    except Exception as e:
        # If verification fails, return state as is but still sorted
        logger.debug(f"Engine verification failed for /engines endpoint: {e}")
        engines.sort(key=lambda e: e.port)
        return engines

@app.get("/engines/with-metrics")
def get_engines_with_metrics():
    """Get all engines with aggregated stream metrics (peers, download/upload speeds)."""
    engines = state.list_engines()
    all_active_streams = state.list_streams_with_stats(status="started")
    monitor_loads = state.get_active_monitor_load_by_engine()
    
    # Group streams by container_id and aggregate metrics
    engine_metrics = {}
    for stream in all_active_streams:
        container_id = stream.container_id
        if container_id not in engine_metrics:
            engine_metrics[container_id] = {
                'total_peers': 0,
                'total_speed_down': 0,
                'total_speed_up': 0,
                'stream_count': 0,
                'monitor_stream_count': 0
            }
        
        # Aggregate metrics from active streams
        engine_metrics[container_id]['stream_count'] += 1
        if stream.peers is not None:
            engine_metrics[container_id]['total_peers'] += stream.peers
        if stream.speed_down is not None:
            engine_metrics[container_id]['total_speed_down'] += stream.speed_down
        if stream.speed_up is not None:
            engine_metrics[container_id]['total_speed_up'] += stream.speed_up
    
    # Enrich engine data with metrics
    result = []
    for engine in engines:
        monitor_stream_count = monitor_loads.get(engine.container_id, 0)
        engine_dict = engine.model_dump()
        metrics = engine_metrics.get(engine.container_id, {
            'total_peers': 0,
            'total_speed_down': 0,
            'total_speed_up': 0,
            'stream_count': 0,
            'monitor_stream_count': 0
        })
        metrics['monitor_stream_count'] = monitor_stream_count
        metrics['stream_count'] = int(metrics.get('stream_count', 0)) + monitor_stream_count
        engine_dict.update(metrics)
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
    Get extended statistics for a stream by querying the AceStream analyze_content API.
    This returns additional metadata like content_type, title, is_live, mime, categories, etc.
    """
    from .utils.acestream_api import get_stream_extended_stats, fetch_extended_content_info
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

    # Additional stable cache key to share results across stream session IDs
    # for the same content (e.g. infohash|legacy-<session>). This keeps the
    # panel fast during reconnects/failovers.
    content_cache_key = f"stream_extended_stats:content:{stream.key}"
    cached_content_stats = cache.get(content_cache_key)
    if cached_content_stats is not None:
        cache.set(cache_key, cached_content_stats, ttl=3600.0)
        return cached_content_stats
    
    extended_stats = None

    # Preferred path: stat URL available (legacy HTTP flow)
    if stream.stat_url:
        extended_stats = await get_stream_extended_stats(stream.stat_url)
    else:
        # Legacy API compatibility path: no stat URL/command URL available.
        # Resolve infohash from stream key or API port, then query analyze_content directly.
        engine_state = state.get_engine(stream.container_id)
        if engine_state:
            infohash = stream.key if stream.key_type == "infohash" else None

            # Fast path: legacy stream IDs usually have infohash prefix
            # (<infohash>|legacy-<session>). Use it directly to avoid an API
            # connect/auth/resolve round-trip on request path.
            if not infohash and "|" in stream_id:
                maybe_infohash = stream_id.split("|", 1)[0].strip().lower()
                if len(maybe_infohash) == 40 and all(c in "0123456789abcdef" for c in maybe_infohash):
                    infohash = maybe_infohash

            if not infohash and engine_state.api_port:
                def _resolve_infohash_via_legacy_api() -> Optional[str]:
                    client = AceLegacyApiClient(
                        host=engine_state.host,
                        port=engine_state.api_port or 62062,
                        connect_timeout=2,
                        response_timeout=2,
                    )
                    try:
                        client.connect()
                        client.authenticate()
                        loadresp, _ = client.resolve_content(stream.key, session_id="0")
                        return loadresp.get("infohash")
                    except Exception:
                        return None
                    finally:
                        try:
                            client.shutdown()
                        except Exception:
                            pass

                try:
                    infohash = await asyncio.wait_for(
                        asyncio.to_thread(_resolve_infohash_via_legacy_api),
                        timeout=3.0,
                    )
                except asyncio.TimeoutError:
                    infohash = None

            if infohash:
                extended_stats = await fetch_extended_content_info(
                    engine_state.host,
                    engine_state.port,
                    infohash,
                )
                if isinstance(extended_stats, dict) and "infohash" not in extended_stats:
                    extended_stats["infohash"] = infohash

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
    Get live position data for a stream from stat URL or LEGACY_API probe.
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
        # Fetch livepos data from stat URL (LEGACY_HTTP path)
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

    # LEGACY_API path (no stat_url): ask active proxy stream manager for a probe.
    try:
        from .proxy.server import ProxyServer

        proxy = ProxyServer.get_instance()
        manager = proxy.stream_managers.get(stream.key) if proxy else None
        if not manager:
            raise HTTPException(status_code=400, detail="Legacy stream manager not available")

        probe = await asyncio.to_thread(
            manager.collect_legacy_stats_probe,
            1,
            1.0,
            True,
        )
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
    Get VPN (Gluetun) status information with location data (cached for 3 seconds).
    
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
    
    # Cache for 3 seconds
    cache.set(cache_key, vpn_status, ttl=3.0)
    
    return vpn_status

@app.get("/vpn/publicip")
def get_vpn_publicip_endpoint():
    """Get VPN public IP address."""
    from .services.gluetun import get_vpn_public_ip
    public_ip = get_vpn_public_ip()
    if public_ip:
        return {"public_ip": public_ip}
    else:
        raise HTTPException(status_code=503, detail="Unable to retrieve VPN public IP")

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
    Get comprehensive orchestrator status for proxy integration (cached for 2 seconds).
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
            "engine_variant": cfg.ENGINE_VARIANT,
            "debug_mode": cfg.DEBUG_MODE
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Cache for 2 seconds (UI polls every 5s)
    cache.set(cache_key, result, ttl=2.0)
    
    return result

# Custom Engine Variant endpoints
from .services.custom_variant_config import (
    detect_platform, 
    get_config as get_custom_config, 
    save_config as save_custom_config,
    reload_config,
    CustomVariantConfig
)

# Template management
from .services.template_manager import (
    list_templates,
    get_template,
    save_template,
    delete_template,
    export_template,
    import_template,
    set_active_template,
    get_active_template_id,
    get_active_template_name,
    rename_template
)

# Global state for reprovisioning tracking
_reprovision_state = {
    "in_progress": False,
    "status": "idle",  # idle, in_progress, success, error
    "message": None,
    "timestamp": None,
    "total_engines": 0,
    "engines_stopped": 0,
    "engines_provisioned": 0,
    "current_engine_id": None,
    "current_phase": None  # stopping, cleaning, provisioning, complete
}

@app.get("/custom-variant/platform")
def get_platform_info():
    """Get detected platform information."""
    return {
        "platform": detect_platform(),
        "supported_platforms": ["amd64", "arm32", "arm64"]
    }

@app.get("/custom-variant/config")
def get_custom_variant_config():
    """Get current custom variant configuration."""
    config = get_custom_config()
    if config:
        return config.dict()
    else:
        raise HTTPException(status_code=500, detail="Failed to load custom variant configuration")

@app.post("/custom-variant/config", dependencies=[Depends(require_api_key)])
def update_custom_variant_config(config: CustomVariantConfig):
    """
    Update custom variant configuration.
    """
    
    # Save the configuration
    success = save_custom_config(config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    
    # Sync configuration changes to active template if one exists
    # This prevents the issue where app restart overwrites custom config with active template data
    try:
        from .services.template_manager import get_active_template_id, get_template, save_template
        active_id = get_active_template_id()
        if active_id is not None:
            template = get_template(active_id)
            if template:
                # Update the template with the new configuration
                save_template(active_id, template.name, config)
                logger.info(f"Synced configuration changes to active template '{template.name}' (slot {active_id})")
    except Exception as e:
        logger.warning(f"Failed to sync configuration to active template: {e}")
    
    # Reload the configuration to ensure it's active
    reload_config()
    
    return {
        "message": "Configuration saved successfully",
        "config": config.dict()
    }

@app.get("/custom-variant/reprovision/status")
def get_reprovision_status():
    """Get current reprovisioning status."""
    return _reprovision_state

@app.post("/custom-variant/reprovision", dependencies=[Depends(require_api_key)])
async def reprovision_all_engines(background_tasks: BackgroundTasks):
    """
    Delete all engines and reprovision them with current settings.
    This is a potentially disruptive operation.
    This operation runs entirely in the background to avoid blocking the API/UI.
    """
    global _reprovision_state
    
    # Check if already in progress
    if _reprovision_state["in_progress"]:
        raise HTTPException(
            status_code=409,
            detail="Reprovisioning operation already in progress"
        )
    
    from .services.health import list_managed
    
    # Get engine count for response before marking in progress
    engines = list_managed()
    engine_count = len(engines)
    
    # Mark as in progress
    _reprovision_state = {
        "in_progress": True,
        "status": "in_progress",
        "message": "Reprovisioning engines...",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_engines": engine_count,
        "engines_stopped": 0,
        "engines_provisioned": 0,
        "current_engine_id": None,
        "current_phase": "preparing"
    }
    
    # Perform all reprovisioning work in background task
    def reprovision_task():
        global _reprovision_state
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        try:
            # Enter reprovisioning mode to coordinate with other services
            state.enter_reprovisioning_mode()
            
            # Get current engines (refresh list in background context)
            from .services.health import list_managed
            engines = list_managed()
            engine_ids = [c.id for c in engines]
            total_engines = len(engine_ids)
            
            logger.info(f"Starting reprovision of {total_engines} engines with new custom variant settings")
            
            # Update state: stopping phase
            _reprovision_state.update({
                "total_engines": total_engines,
                "current_phase": "stopping",
                "message": f"Stopping {total_engines} engines concurrently..."
            })
            
            # Delete all engines concurrently (similar to shutdown)
            # Use ThreadPoolExecutor to stop containers concurrently without overwhelming Docker socket
            max_workers = min(10, total_engines) if total_engines > 0 else 1  # Cap at 10 concurrent operations
            stopped_count = 0
            
            def stop_engine_wrapper(engine_id, idx, total):
                """Wrapper function to stop engine and update state"""
                try:
                    stop_container(engine_id)
                    logger.info(f"Stopped engine {engine_id[:12]} ({idx}/{total})")
                    return (True, engine_id, None)
                except Exception as e:
                    logger.error(f"Failed to stop engine {engine_id[:12]}: {e}")
                    return (False, engine_id, str(e))
            
            if engine_ids:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all stop tasks
                    future_to_engine = {
                        executor.submit(stop_engine_wrapper, engine_id, idx, total_engines): engine_id
                        for idx, engine_id in enumerate(engine_ids, 1)
                    }
                    
                    # Process results as they complete
                    for future in as_completed(future_to_engine):
                        success, engine_id, error = future.result()
                        if success:
                            stopped_count += 1
                            _reprovision_state["engines_stopped"] = stopped_count
                            _reprovision_state.update({
                                "message": f"Stopped {stopped_count}/{total_engines} engines..."
                            })
            
            logger.info(f"Stopped {stopped_count}/{total_engines} engines concurrently")
            
            # Update state: cleaning phase
            _reprovision_state.update({
                "current_phase": "cleaning",
                "current_engine_id": None,
                "message": "Cleaning up state..."
            })
            
            # Clear state
            cleanup_on_shutdown()
            
            # Switch HLS mode back to TS (MPEG-TS) if it was set to HLS
            # This prevents issues when users change engine variants since HLS may not be supported on all variants
            current_stream_mode = os.getenv('PROXY_STREAM_MODE', 'TS')
            if current_stream_mode == 'HLS':
                logger.info("Switching stream mode from HLS to TS (MPEG-TS) for reprovisioning")
                os.environ['PROXY_STREAM_MODE'] = 'TS'
                # Reload the config to pick up the change
                from .proxy.config_helper import Config
                Config.STREAM_MODE = 'TS'
            
            # Reload custom config to ensure we have latest settings
            reload_config()
            
            # Give time for cleanup and monitoring systems to settle
            time.sleep(2)
            
            # Update state: provisioning phase
            _reprovision_state.update({
                "current_phase": "provisioning",
                "message": f"Provisioning {cfg.MIN_REPLICAS} new engines concurrently..."
            })
            
            # Reprovision minimum replicas concurrently
            # Use ThreadPoolExecutor to start containers concurrently
            target_engines = cfg.MIN_REPLICAS
            max_provision_workers = min(5, target_engines)  # Cap at 5 concurrent provisions to avoid overwhelming Docker
            provisioned_count = 0
            
            def provision_engine_wrapper(idx, total):
                """Wrapper function to provision engine and update state"""
                try:
                    response = start_acestream(AceProvisionRequest())
                    if response and response.container_id:
                        logger.info(f"Successfully provisioned engine {response.container_id[:12]} ({idx}/{total})")
                        return (True, response.container_id, None)
                    else:
                        logger.error(f"Failed to provision engine {idx}/{total}: No response or container ID")
                        return (False, None, "No response or container ID")
                except Exception as e:
                    logger.error(f"Failed to provision engine {idx}/{total}: {e}")
                    return (False, None, str(e))
            
            if target_engines > 0:
                with ThreadPoolExecutor(max_workers=max_provision_workers) as executor:
                    # Submit all provision tasks
                    future_to_idx = {
                        executor.submit(provision_engine_wrapper, idx, target_engines): idx
                        for idx in range(1, target_engines + 1)
                    }
                    
                    # Process results as they complete
                    for future in as_completed(future_to_idx):
                        success, container_id, error = future.result()
                        if success:
                            provisioned_count += 1
                            _reprovision_state["engines_provisioned"] = provisioned_count
                            _reprovision_state.update({
                                "message": f"Provisioned {provisioned_count}/{target_engines} new engines..."
                            })
            
            logger.info(f"Successfully provisioned {provisioned_count}/{target_engines} engines concurrently")
            
            # Reindex after provisioning to ensure state consistency
            logger.info("Reindexing after reprovisioning to pick up new containers")
            try:
                from .services.reindex import reindex_existing
                reindex_existing()
            except Exception as e:
                logger.error(f"Failed to reindex after reprovisioning: {e}")
            
            # Exit reprovisioning mode
            state.exit_reprovisioning_mode()
            
            # Update state: complete
            _reprovision_state = {
                "in_progress": False,
                "status": "success",
                "message": f"Successfully reprovisioned all engines ({stopped_count} stopped, {provisioned_count} provisioned)",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_engines": total_engines,
                "engines_stopped": stopped_count,
                "engines_provisioned": provisioned_count,
                "current_engine_id": None,
                "current_phase": "complete"
            }
        except Exception as e:
            logger.error(f"Failed to reprovision engines: {e}")
            
            # Exit reprovisioning mode on error
            try:
                state.exit_reprovisioning_mode()
            except Exception:
                pass
            
            _reprovision_state = {
                "in_progress": False,
                "status": "error",
                "message": f"Failed to reprovision engines: {str(e)}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_engines": _reprovision_state.get("total_engines", 0),
                "engines_stopped": _reprovision_state.get("engines_stopped", 0),
                "engines_provisioned": 0,
                "current_engine_id": _reprovision_state.get("current_engine_id"),
                "current_phase": "error"
            }
    
    background_tasks.add_task(reprovision_task)
    
    return {
        "message": f"Started reprovisioning of {engine_count} engines",
        "deleted_count": engine_count
    }

# Template Management Endpoints
@app.get("/custom-variant/templates")
def list_all_templates():
    """List all template slots with metadata."""
    templates = list_templates()
    active_template_id = get_active_template_id()
    
    return {
        "templates": templates,
        "active_template_id": active_template_id
    }

@app.get("/custom-variant/templates/{slot_id}")
def get_template_by_id(slot_id: int):
    """Get a specific template by slot ID."""
    if slot_id < 1 or slot_id > 10:
        raise HTTPException(status_code=400, detail="Template slot_id must be between 1 and 10")
    
    template = get_template(slot_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template {slot_id} not found")
    
    return template.to_dict()

@app.post("/custom-variant/templates/{slot_id}", dependencies=[Depends(require_api_key)])
def save_template_to_slot(slot_id: int, request: dict):
    """Save a template to a specific slot."""
    if slot_id < 1 or slot_id > 10:
        raise HTTPException(status_code=400, detail="Template slot_id must be between 1 and 10")
    
    if "name" not in request or "config" not in request:
        raise HTTPException(status_code=400, detail="Request must include 'name' and 'config'")
    
    try:
        config = CustomVariantConfig(**request["config"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid config: {str(e)}")
    
    # Validate the configuration
    is_valid, error_msg = validate_config(config)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid configuration: {error_msg}")
    
    success = save_template(slot_id, request["name"], config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save template")
    
    return {"message": f"Template {slot_id} saved successfully"}

@app.delete("/custom-variant/templates/{slot_id}", dependencies=[Depends(require_api_key)])
def delete_template_by_id(slot_id: int):
    """Delete a template from a specific slot."""
    if slot_id < 1 or slot_id > 10:
        raise HTTPException(status_code=400, detail="Template slot_id must be between 1 and 10")
    
    # Don't allow deleting the active template
    if slot_id == get_active_template_id():
        raise HTTPException(status_code=400, detail="Cannot delete the currently active template")
    
    success = delete_template(slot_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Template {slot_id} not found")
    
    return {"message": f"Template {slot_id} deleted successfully"}

@app.patch("/custom-variant/templates/{slot_id}/rename", dependencies=[Depends(require_api_key)])
def rename_template_endpoint(slot_id: int, request: dict):
    """Rename a template."""
    if slot_id < 1 or slot_id > 10:
        raise HTTPException(status_code=400, detail="Template slot_id must be between 1 and 10")
    
    if "name" not in request:
        raise HTTPException(status_code=400, detail="Request must include 'name'")
    
    new_name = request["name"].strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Template name cannot be empty")
    
    success = rename_template(slot_id, new_name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Template {slot_id} not found or rename failed")
    
    return {"message": f"Template {slot_id} renamed to '{new_name}' successfully"}

@app.post("/custom-variant/templates/{slot_id}/activate", dependencies=[Depends(require_api_key)])
def activate_template(slot_id: int):
    """Activate a template (load it as current config)."""
    if slot_id < 1 or slot_id > 10:
        raise HTTPException(status_code=400, detail="Template slot_id must be between 1 and 10")
    
    template = get_template(slot_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template {slot_id} not found")
    
    # Get current config to preserve enabled state
    current_config = get_custom_config()
    current_enabled = current_config.enabled if current_config else False
    
    # Save the template config as the current custom variant config, preserving enabled state
    template_config = template.config.model_copy(deep=True)
    template_config.enabled = current_enabled
    success = save_custom_config(template_config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to activate template")
    
    # Reload the configuration
    reload_config()
    
    # Set as active template
    set_active_template(slot_id)
    
    return {
        "message": f"Template '{template.name}' activated successfully",
        "template_id": slot_id,
        "template_name": template.name
    }

@app.get("/custom-variant/templates/{slot_id}/export")
def export_template_endpoint(slot_id: int):
    """Export a template as JSON."""
    if slot_id < 1 or slot_id > 10:
        raise HTTPException(status_code=400, detail="Template slot_id must be between 1 and 10")
    
    json_data = export_template(slot_id)
    if not json_data:
        raise HTTPException(status_code=404, detail=f"Template {slot_id} not found")
    
    from starlette.responses import Response
    return Response(
        content=json_data,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=template_{slot_id}.json"}
    )

@app.post("/custom-variant/templates/{slot_id}/import", dependencies=[Depends(require_api_key)])
def import_template_endpoint(slot_id: int, request: dict):
    """Import a template from JSON."""
    if slot_id < 1 or slot_id > 10:
        raise HTTPException(status_code=400, detail="Template slot_id must be between 1 and 10")
    
    if "json_data" not in request:
        raise HTTPException(status_code=400, detail="Request must include 'json_data'")
    
    success, error_msg = import_template(slot_id, request["json_data"])
    if not success:
        raise HTTPException(status_code=400, detail=error_msg)
    
    return {"message": f"Template {slot_id} imported successfully"}

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
    return [
        EventLog(
            id=e.id,
            timestamp=e.timestamp if e.timestamp.tzinfo else e.timestamp.replace(tzinfo=timezone.utc),
            event_type=e.event_type,
            category=e.category,
            message=e.message,
            details=e.details or {},
            container_id=e.container_id,
            stream_id=e.stream_id
        )
        for e in events
    ]

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
    content_id: str
    stream_name: Optional[str] = None
    interval_s: float = 1.0
    run_seconds: int = 0
    per_sample_timeout_s: float = 1.0
    engine_container_id: Optional[str] = None


class LegacyStreamMonitorM3UParseRequest(BaseModel):
    m3u_content: str


@app.post("/ace/monitor/legacy/start", dependencies=[Depends(require_api_key)])
async def start_legacy_stream_monitor(req: LegacyStreamMonitorStartRequest):
    """Start a background legacy API monitor that collects STATUS every interval.

    The monitor uses LOADASYNC/START once, does not stream to clients, and gathers
    STATUS/livepos telemetry only for observability.
    """
    try:
        monitor = await legacy_stream_monitoring_service.start_monitor(
            content_id=req.content_id,
            stream_name=req.stream_name,
            interval_s=req.interval_s,
            run_seconds=req.run_seconds,
            per_sample_timeout_s=req.per_sample_timeout_s,
            engine_container_id=req.engine_container_id,
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
async def get_legacy_stream_monitor(monitor_id: str):
    """Get a single legacy monitoring session including recent STATUS history."""
    monitor = await legacy_stream_monitoring_service.get_monitor(monitor_id)
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

@app.get("/ace/preflight")
def ace_preflight(
    id: str = Query(..., description="AceStream content ID (infohash or content_id)"),
    tier: str = Query("light", description="Availability probe tier: light or deep"),
):
    """Run a short availability probe and canonicalize content IDs before playback."""
    from .proxy.config_helper import Config as ProxyConfig
    import requests

    normalized_tier = (tier or "light").strip().lower()
    if normalized_tier not in {"light", "deep"}:
        raise HTTPException(status_code=400, detail="tier must be 'light' or 'deep'")

    control_mode = (ProxyConfig.CONTROL_MODE or "LEGACY_HTTP").upper()

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

    if control_mode == "LEGACY_API":
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
            preflight_result = client.preflight(id, tier=normalized_tier)
            return {
                "control_mode": control_mode,
                "tier": normalized_tier,
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

    # HTTP control fallback for compatibility in LEGACY_HTTP mode.
    url = f"http://{selected_engine.host}:{selected_engine.port}/ace/getstream"
    pid = f"preflight-{int(time.time())}"
    params = {"id": id, "format": "json", "pid": pid}
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
        "engine": {
            "container_id": selected_engine.container_id,
            "host": selected_engine.host,
            "port": selected_engine.port,
            "api_port": selected_engine.api_port,
            "forwarded": selected_engine.forwarded,
        },
        "result": result,
    }

@app.get("/ace/getstream")
async def ace_getstream(
    id: str = Query(..., description="AceStream content ID (infohash or content_id)"),
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
        id: AceStream content ID (infohash or content_id)
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
    
    # Get current stream mode
    stream_mode = ProxyConfig.STREAM_MODE
    control_mode = (ProxyConfig.CONTROL_MODE or 'LEGACY_HTTP').upper()

    if stream_mode == 'HLS' and control_mode == 'LEGACY_API':
        raise HTTPException(
            status_code=400,
            detail="HLS mode requires control_mode='LEGACY_HTTP'"
        )
    
    # Check if stream is on the looping blacklist
    if looping_streams_tracker.is_looping(id):
        logger.warning(f"Stream request denied: {id} is on looping blacklist")
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

    reusable_monitor_session = await legacy_stream_monitoring_service.get_reusable_session_for_content(id)
    if reusable_monitor_session:
        monitor_engine = reusable_monitor_session.get("engine") or {}
        logger.info(
            "Reusing monitor session %s for stream %s on engine %s",
            reusable_monitor_session.get("monitor_id"),
            id,
            str(monitor_engine.get("container_id") or "unknown")[:12],
        )
    
    reservation_engine_id = None
    
    def rollback_reservation():
        if reservation_engine_id:
            try:
                from app.proxy.manager import ProxyManager
                redis = ProxyManager.get_instance().redis_client
                if redis:
                    pending_key = f"ace_proxy:engine:{reservation_engine_id}:pending"
                    decr_script = """
                    local current = redis.call('GET', KEYS[1])
                    if current and tonumber(current) > 0 then
                        return redis.call('DECR', KEYS[1])
                    else
                        return 0
                    end
                    """
                    redis.eval(decr_script, 1, pending_key)
                    logger.debug(f"Rolled back pending reservation for engine {reservation_engine_id[:12]}")
            except Exception as e:
                logger.warning(f"Failed to rollback reservation for engine {reservation_engine_id[:12]}: {e}")
    
    def select_best_engine():
        """Select the best available engine using layer-based load balancing.
        
        Returns tuple of (selected_engine, current_load)
        Raises HTTPException if no engines available or all at capacity.
        """
        return select_best_engine_shared(reserve_pending=True)
    
    try:
        # Handle HLS mode differently from TS mode
        if stream_mode == 'HLS':
            # For HLS, use the FastAPI-based HLS proxy
            from app.proxy.hls_proxy import HLSProxyServer
            import requests
            from uuid import uuid4
            
            # Get HLS proxy instance
            hls_proxy = HLSProxyServer.get_instance()
            
            # Check if channel already exists - do this BEFORE engine selection to avoid
            # unnecessary computation and logging on every manifest/segment request
            if hls_proxy.has_channel(id):
                # Channel already exists - reuse existing session
                # Skip engine selection and just serve the manifest
                logger.debug(f"HLS channel {id} already exists, serving manifest to client {client_id} from {client_ip}")
                
                try:
                    # Track client activity for this request
                    hls_proxy.record_client_activity(id, client_ip)
                    
                    # Use async version to avoid blocking the event loop
                    manifest_content = await hls_proxy.get_manifest_async(id)
                    
                    # Note: In HLS, clients make multiple requests (manifest + segments)
                    # Client activity is tracked on each request, not per connection
                    # DO NOT remove client here - let inactivity timeout handle cleanup
                    
                    elapsed = time.perf_counter() - request_started_at
                    observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                    observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                    return StreamingResponse(
                        iter([manifest_content.encode('utf-8')]),
                        media_type="application/vnd.apple.mpegurl",
                        headers={
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Connection": "keep-alive",
                        }
                    )
                except TimeoutError as e:
                    logger.error(f"Timeout getting HLS manifest: {e}")
                    raise HTTPException(status_code=503, detail=f"Timeout waiting for stream buffer: {str(e)}")
            
            # Channel doesn't exist - either reuse monitor session or select engine.
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
                f"Selected engine {selected_engine.container_id[:12]} for new {stream_mode} stream {id} "
                f"(forwarded={selected_engine.forwarded}, current_load={current_load})"
            )
            
            logger.info(
                f"Client {client_id} initializing new {stream_mode} stream {id} from {client_ip}"
            )
            
            try:
                if reusable_monitor_session:
                    logger.info("Using playback URL from monitoring session for HLS stream")
                    # Get API key from environment
                    api_key = os.getenv('API_KEY')

                    monitor_session = reusable_monitor_session.get("session") or {}
                    monitor_playback_session_id = monitor_session.get('playback_session_id')
                    if not monitor_playback_session_id:
                        monitor_playback_session_id = f"hls-reuse-{id[:16]}-{int(time.time())}"
                    session_info = {
                        'playback_session_id': monitor_playback_session_id,
                        'stat_url': monitor_session.get('stat_url') or '',
                        'command_url': monitor_session.get('command_url') or '',
                        'is_live': 1,
                        'owns_engine_session': False,
                    }
                else:
                    # Request new session from AceStream engine
                    hls_url = f"http://{selected_engine.host}:{selected_engine.port}/ace/manifest.m3u8"
                    pid = str(uuid4())
                    params = {
                        "id": id,
                        "format": "json",
                        "pid": pid
                    }

                    logger.info(f"Requesting HLS stream from engine: {hls_url}")
                    response = requests.get(hls_url, params=params, timeout=10)
                    response.raise_for_status()
                    data = response.json()

                    if data.get("error"):
                        error_msg = data['error']
                        logger.error(f"AceStream engine returned error: {error_msg}")
                        raise HTTPException(status_code=500, detail=f"AceStream engine error: {error_msg}")

                    # Get session info from response
                    resp_data = data.get("response", {})
                    playback_url = resp_data.get("playback_url")

                    if not playback_url:
                        logger.error("No playback_url in AceStream response")
                        raise HTTPException(status_code=500, detail="No playback URL in engine response")

                    logger.info(f"HLS playback URL: {playback_url}")

                    # Get API key from environment
                    api_key = os.getenv('API_KEY')

                    # Prepare session info for event tracking
                    session_info = {
                        'playback_session_id': resp_data.get('playback_session_id'),
                        'stat_url': resp_data.get('stat_url'),
                        'command_url': resp_data.get('command_url'),
                        'is_live': resp_data.get('is_live', 1),
                        'owns_engine_session': True,
                    }

                # Initialize HLS proxy channel
                hls_proxy.initialize_channel(
                    channel_id=id,
                    playback_url=playback_url,
                    engine_host=selected_engine.host,
                    engine_port=selected_engine.port,
                    engine_container_id=selected_engine.container_id,
                    session_info=session_info,
                    api_key=api_key
                )
                
                # Track client activity and get manifest for the newly created channel
                # Use async version to avoid blocking the event loop
                hls_proxy.record_client_activity(id, client_ip)
                manifest_content = await hls_proxy.get_manifest_async(id)
                
                elapsed = time.perf_counter() - request_started_at
                observe_proxy_request(stream_mode, "/ace/getstream", elapsed, success=True, status_code=200)
                observe_proxy_ttfb(stream_mode, "/ace/getstream", elapsed)

                return StreamingResponse(
                    iter([manifest_content.encode('utf-8')]),
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
            # TS mode - use existing ts_proxy architecture
            if reusable_monitor_session:
                monitor_engine = reusable_monitor_session.get("engine") or {}
                selected_engine = SimpleNamespace(
                    container_id=monitor_engine.get("container_id"),
                    host=monitor_engine.get("host"),
                    port=monitor_engine.get("port"),
                    api_port=monitor_engine.get("api_port") or 62062,
                    forwarded=bool(monitor_engine.get("forwarded")),
                )
                monitor_loads = state.get_active_monitor_load_by_engine()
                current_load = len(state.list_streams(status="started", container_id=selected_engine.container_id)) + monitor_loads.get(selected_engine.container_id, 0)
            else:
                selected_engine, current_load = select_best_engine()
                reservation_engine_id = selected_engine.container_id
            
            logger.info(
                f"Selected engine {selected_engine.container_id[:12]} for {stream_mode} stream {id} "
                f"(forwarded={selected_engine.forwarded}, current_load={current_load})"
            )
            
            logger.info(
                f"Client {client_id} connecting to {stream_mode} stream {id} from {client_ip}"
            )
            
            # Get proxy instance
            proxy = ProxyManager.get_instance()
            
            # Start stream if not exists (idempotent)
            success = proxy.start_stream(
                content_id=id,
                engine_host=selected_engine.host,
                engine_port=selected_engine.port,
                engine_api_port=selected_engine.api_port,
                engine_container_id=selected_engine.container_id,
                existing_session=reusable_monitor_session,
            )
            
            if not success:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to start stream session"
                )
            
            # Create stream generator
            generator = create_stream_generator(
                content_id=id,
                client_id=client_id,
                client_ip=client_ip,
                client_user_agent=user_agent,
                stream_initializing=(control_mode == 'LEGACY_API')
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



@app.get("/ace/hls/{content_id}/segment/{segment_path:path}")
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
        
        # Track client activity for this segment request
        client_ip = get_client_ip(request)
        hls_proxy.record_client_activity(content_id, client_ip)
        
        segment_data = hls_proxy.get_segment(content_id, segment_path)
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




@app.get("/ace/manifest.m3u8")
async def ace_manifest(
    id: str = Query(..., description="AceStream content ID (infohash or content_id)"),
):
    """Proxy endpoint for AceStream HLS streams (M3U8).
    
    Note: This endpoint is deprecated. Use /ace/getstream which now supports both TS and HLS modes
    based on the proxy configuration.
    
    Args:
        id: AceStream content ID (infohash or content_id)
        
    Returns:
        Redirects to /ace/getstream
    """
    raise HTTPException(
        status_code=301,
        detail="This endpoint is deprecated. Use /ace/getstream which now supports both TS and HLS modes based on proxy settings.",
        headers={"Location": f"/ace/getstream?id={id}"}
    )


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
    from .proxy.server import ProxyServer
    from .proxy.hls_proxy import HLSProxyServer
    from .proxy.redis_keys import RedisKeys
    import redis
    
    try:
        # First check if this is an HLS stream
        hls_proxy = HLSProxyServer.get_instance()
        if hls_proxy.has_channel(stream_key):
            # This is an HLS stream - get client info from HLS proxy
            client_manager = hls_proxy.client_managers.get(stream_key)
            if not client_manager:
                return {"clients": []}
            
            # HLS proxy tracks clients by IP address
            with client_manager.lock:
                clients = []
                import time
                current_time = time.time()
                for client_ip, last_activity in client_manager.last_activity.items():
                    clients.append({
                        "client_id": client_ip,
                        "ip_address": client_ip,
                        "last_active": last_activity,
                        "connected_at": last_activity,  # We don't track connection time separately
                        "user_agent": "HLS Client",
                        "worker_id": "hls_proxy",
                        "inactive_seconds": current_time - last_activity
                    })
                return {"clients": clients}
        
        # Not an HLS stream, check TS proxy
        proxy_server = ProxyServer.get_instance()
        
        # Get client manager for this stream
        client_manager = proxy_server.client_managers.get(stream_key)
        if not client_manager:
            # Stream not active in proxy, return empty list
            return {"clients": []}
        
        # Get clients from Redis
        redis_client = proxy_server.redis_client
        if not redis_client:
            return {"clients": []}
        
        clients_set_key = RedisKeys.clients(stream_key)
        client_ids = redis_client.smembers(clients_set_key)
        
        clients = []
        for client_id_bytes in client_ids:
            client_id = client_id_bytes.decode('utf-8')
            client_key = RedisKeys.client_metadata(stream_key, client_id)
            
            # Get client metadata
            client_data = redis_client.hgetall(client_key)
            if client_data:
                # Decode Redis data
                client_info = {}
                for key, value in client_data.items():
                    key_str = key.decode('utf-8')
                    value_str = value.decode('utf-8')
                    
                    # Convert numeric fields with appropriate type
                    if key_str in ['chunks_sent']:
                        # Integer fields
                        try:
                            client_info[key_str] = int(value_str)
                        except ValueError:
                            client_info[key_str] = value_str
                    elif key_str in ['connected_at', 'last_active', 'bytes_sent', 'stats_updated_at']:
                        # Float/timestamp fields
                        try:
                            client_info[key_str] = float(value_str)
                        except ValueError:
                            client_info[key_str] = value_str
                    else:
                        client_info[key_str] = value_str
                
                client_info['client_id'] = client_id
                clients.append(client_info)
        
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
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "chunk_size": ProxyConfig.CHUNK_SIZE,
        "buffer_chunk_size": ProxyConfig.BUFFER_CHUNK_SIZE,
        "redis_chunk_ttl": ProxyConfig.REDIS_CHUNK_TTL,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": ProxyConfig.CONTROL_MODE,
        "legacy_api_preflight_tier": ConfigHelper.legacy_api_preflight_tier(),
        "engine_variant": cfg.ENGINE_VARIANT,
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
    stream_timeout: Optional[int] = None,
    channel_shutdown_delay: Optional[int] = None,
    max_streams_per_engine: Optional[int] = None,
    stream_mode: Optional[str] = None,
    control_mode: Optional[str] = None,
    legacy_api_preflight_tier: Optional[str] = None,
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
        connection_timeout: Connection timeout in seconds (min: 5, max: 60)
        stream_timeout: Stream timeout in seconds (min: 10, max: 300)
        channel_shutdown_delay: Delay before shutting down idle streams in seconds (min: 1, max: 60)
        max_streams_per_engine: Maximum streams per engine before provisioning new engine (min: 1, max: 20)
        stream_mode: Stream mode - 'TS' for MPEG-TS or 'HLS' for HLS streaming
        control_mode: Engine control mode - 'LEGACY_HTTP' (default) or 'LEGACY_API' (optional)
        legacy_api_preflight_tier: Legacy API preflight tier - 'light' or 'deep'
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
    
    if stream_timeout is not None:
        if stream_timeout < 10 or stream_timeout > 300:
            raise HTTPException(status_code=400, detail="stream_timeout must be between 10 and 300 seconds")
        ProxyConfig.STREAM_TIMEOUT = stream_timeout
    
    if channel_shutdown_delay is not None:
        if channel_shutdown_delay < 1 or channel_shutdown_delay > 60:
            raise HTTPException(status_code=400, detail="channel_shutdown_delay must be between 1 and 60 seconds")
        ProxyConfig.CHANNEL_SHUTDOWN_DELAY = channel_shutdown_delay
    
    if max_streams_per_engine is not None:
        if max_streams_per_engine < 1 or max_streams_per_engine > 20:
            raise HTTPException(status_code=400, detail="max_streams_per_engine must be between 1 and 20")
        cfg.MAX_STREAMS_PER_ENGINE = max_streams_per_engine
    
    if stream_mode is not None:
        if stream_mode not in ['TS', 'HLS']:
            raise HTTPException(status_code=400, detail="stream_mode must be either 'TS' or 'HLS'")

        # Legacy API control currently supports TS flow only.
        if stream_mode == 'HLS' and ProxyConfig.CONTROL_MODE == 'LEGACY_API':
            raise HTTPException(status_code=400, detail="HLS mode is only supported with control_mode='LEGACY_HTTP'")
        
        ProxyConfig.STREAM_MODE = stream_mode

    if control_mode is not None:
        normalized_control_mode = str(control_mode).upper()
        if normalized_control_mode not in ['LEGACY_HTTP', 'LEGACY_API']:
            raise HTTPException(status_code=400, detail="control_mode must be either 'LEGACY_HTTP' or 'LEGACY_API'")

        if normalized_control_mode == 'LEGACY_API' and ProxyConfig.STREAM_MODE == 'HLS':
            raise HTTPException(status_code=400, detail="control_mode='LEGACY_API' requires stream_mode='TS'")

        ProxyConfig.CONTROL_MODE = normalized_control_mode

    if legacy_api_preflight_tier is not None:
        normalized_tier = str(legacy_api_preflight_tier).strip().lower()
        if normalized_tier not in ['light', 'deep']:
            raise HTTPException(status_code=400, detail="legacy_api_preflight_tier must be either 'light' or 'deep'")
        ProxyConfig.LEGACY_API_PREFLIGHT_TIER = normalized_tier

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
        f"stream_timeout={ProxyConfig.STREAM_TIMEOUT}, "
        f"channel_shutdown_delay={ProxyConfig.CHANNEL_SHUTDOWN_DELAY}, "
        f"max_streams_per_engine={cfg.MAX_STREAMS_PER_ENGINE}, "
        f"stream_mode={ProxyConfig.STREAM_MODE}, "
        f"control_mode={ProxyConfig.CONTROL_MODE}, "
        f"legacy_api_preflight_tier={ConfigHelper.legacy_api_preflight_tier()}"
    )
    
    # Persist settings to JSON file
    from .services.settings_persistence import SettingsPersistence
    config_to_save = {
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": ProxyConfig.CONTROL_MODE,
        "legacy_api_preflight_tier": ConfigHelper.legacy_api_preflight_tier(),
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
        logger.info("Proxy configuration persisted to JSON file")
    
    return {
        "message": "Proxy configuration updated and persisted",
        "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
        "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
        "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
        "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
        "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
        "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
        "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
        "max_streams_per_engine": cfg.MAX_STREAMS_PER_ENGINE,
        "stream_mode": ProxyConfig.STREAM_MODE,
        "control_mode": ProxyConfig.CONTROL_MODE,
        "legacy_api_preflight_tier": ConfigHelper.legacy_api_preflight_tier(),
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


class VPNSettingsUpdate(BaseModel):
    """Model for updating VPN (Gluetun) settings."""
    enabled: Optional[bool] = None
    vpn_mode: Optional[str] = None           # 'single' or 'redundant'
    container_name: Optional[str] = None
    container_name_2: Optional[str] = None
    api_port: Optional[int] = None
    port_range_1: Optional[str] = None       # redundant mode
    port_range_2: Optional[str] = None       # redundant mode
    health_check_interval_s: Optional[int] = None
    port_cache_ttl_s: Optional[int] = None
    restart_engines_on_reconnect: Optional[bool] = None
    unhealthy_restart_timeout_s: Optional[int] = None


@app.get("/settings/orchestrator")
def get_orchestrator_settings():
    """Get current orchestrator core configuration settings."""
    from .services.settings_persistence import SettingsPersistence

    persisted = SettingsPersistence.load_orchestrator_config()
    if persisted:
        return persisted

    # Return defaults from runtime cfg
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
        current["collect_interval_s"] = settings.collect_interval_s
        cfg.COLLECT_INTERVAL_S = settings.collect_interval_s

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


@app.get("/settings/vpn")
def get_vpn_settings():
    """Get current VPN (Gluetun) configuration settings."""
    from .services.settings_persistence import SettingsPersistence

    persisted = SettingsPersistence.load_vpn_config()
    if persisted:
        return persisted

    defaults = {
        "enabled": bool(cfg.GLUETUN_CONTAINER_NAME),
        "vpn_mode": cfg.VPN_MODE,
        "container_name": cfg.GLUETUN_CONTAINER_NAME or "",
        "container_name_2": cfg.GLUETUN_CONTAINER_NAME_2 or "",
        "api_port": cfg.GLUETUN_API_PORT,
        "port_range_1": cfg.GLUETUN_PORT_RANGE_1 or "",
        "port_range_2": cfg.GLUETUN_PORT_RANGE_2 or "",
        "health_check_interval_s": cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S,
        "port_cache_ttl_s": cfg.GLUETUN_PORT_CACHE_TTL_S,
        "restart_engines_on_reconnect": cfg.VPN_RESTART_ENGINES_ON_RECONNECT,
        "unhealthy_restart_timeout_s": cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S,
    }
    try:
        SettingsPersistence.save_vpn_config(defaults)
    except Exception:
        pass
    return defaults


@app.post("/settings/vpn", dependencies=[Depends(require_api_key)])
async def update_vpn_settings(settings: VPNSettingsUpdate):
    """Update VPN (Gluetun) configuration settings at runtime and persist."""
    from .services.settings_persistence import SettingsPersistence

    current = SettingsPersistence.load_vpn_config() or {
        "enabled": bool(cfg.GLUETUN_CONTAINER_NAME),
        "vpn_mode": cfg.VPN_MODE,
        "container_name": cfg.GLUETUN_CONTAINER_NAME or "",
        "container_name_2": cfg.GLUETUN_CONTAINER_NAME_2 or "",
        "api_port": cfg.GLUETUN_API_PORT,
        "port_range_1": cfg.GLUETUN_PORT_RANGE_1 or "",
        "port_range_2": cfg.GLUETUN_PORT_RANGE_2 or "",
        "health_check_interval_s": cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S,
        "port_cache_ttl_s": cfg.GLUETUN_PORT_CACHE_TTL_S,
        "restart_engines_on_reconnect": cfg.VPN_RESTART_ENGINES_ON_RECONNECT,
        "unhealthy_restart_timeout_s": cfg.VPN_UNHEALTHY_RESTART_TIMEOUT_S,
    }

    if settings.enabled is not None:
        current["enabled"] = settings.enabled

    if settings.vpn_mode is not None:
        if settings.vpn_mode not in ('single', 'redundant'):
            raise HTTPException(status_code=400, detail="vpn_mode must be 'single' or 'redundant'")
        current["vpn_mode"] = settings.vpn_mode
        cfg.VPN_MODE = settings.vpn_mode

    if settings.container_name is not None:
        current["container_name"] = settings.container_name
        cfg.GLUETUN_CONTAINER_NAME = settings.container_name or None

    if settings.container_name_2 is not None:
        current["container_name_2"] = settings.container_name_2
        cfg.GLUETUN_CONTAINER_NAME_2 = settings.container_name_2 or None

    if settings.api_port is not None:
        if not (1 <= settings.api_port <= 65535):
            raise HTTPException(status_code=400, detail="api_port must be 1-65535")
        current["api_port"] = settings.api_port
        cfg.GLUETUN_API_PORT = settings.api_port

    if settings.port_range_1 is not None:
        current["port_range_1"] = settings.port_range_1
        cfg.GLUETUN_PORT_RANGE_1 = settings.port_range_1 or None

    if settings.port_range_2 is not None:
        current["port_range_2"] = settings.port_range_2
        cfg.GLUETUN_PORT_RANGE_2 = settings.port_range_2 or None

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

    # Apply enabled flag: clear container name if VPN is disabled
    if not current.get("enabled"):
        cfg.GLUETUN_CONTAINER_NAME = None
        cfg.GLUETUN_CONTAINER_NAME_2 = None
    else:
        cfg.GLUETUN_CONTAINER_NAME = current.get("container_name") or None
        cfg.GLUETUN_CONTAINER_NAME_2 = current.get("container_name_2") or None

    # Restart gluetun monitor to pick up new config
    try:
        await gluetun_monitor.stop()
        await gluetun_monitor.start()
        logger.info("Gluetun monitor restarted with new VPN config")
    except Exception as e:
        logger.warning(f"Failed to restart gluetun monitor: {e}")

    if SettingsPersistence.save_vpn_config(current):
        logger.info("VPN settings persisted")
    else:
        logger.warning("Failed to persist VPN settings")

    return {"message": "VPN settings updated and persisted", **current}


# ============================================================================
# Engine Settings Endpoints
# ============================================================================

class EngineSettingsUpdate(BaseModel):
    """Model for updating engine settings."""
    min_replicas: Optional[int] = None
    max_replicas: Optional[int] = None
    auto_delete: Optional[bool] = None
    engine_variant: Optional[str] = None
    use_custom_variant: Optional[bool] = None
    platform: Optional[str] = None  # Read-only, just for compatibility
    manual_mode: Optional[bool] = None
    manual_engines: Optional[List[Dict[str, Any]]] = None  # list of {"host": "ip", "port": port}

@app.get("/settings/engine")
def get_engine_settings():
    """Get current engine configuration settings."""
    from .services.custom_variant_config import detect_platform, get_config as get_custom_config
    
    # Try to load from persisted settings first
    from .services.settings_persistence import SettingsPersistence
    persisted = SettingsPersistence.load_engine_settings()
    
    # Detected platform is always real-time
    current_platform = detect_platform()
    
    # If persisted settings exist, use them but override platform field
    if persisted:
        # Update platform in the response to match real-time detection
        # This ensures the UI always shows the correct architecture
        persisted["platform"] = current_platform
        
        # Ensure variants are compatible with the platform for the UI
        variant = persisted.get("engine_variant")
        if current_platform in ["arm64", "arm32"] and (not variant or "amd64" in variant):
            # Force correction for ARM platforms if they have an amd64 variant configured
            new_variant = "AceServe-arm64" if current_platform == "arm64" else "AceServe-arm32"
            persisted["engine_variant"] = new_variant
            logger.info(f"Corrected incompatible engine variant '{variant}' to '{new_variant}' for platform '{current_platform}'")
            
        return persisted
    
    # Build default response from current runtime config
    custom_config = get_custom_config()
    
    default_settings = {
        "min_replicas": cfg.MIN_REPLICAS,
        "max_replicas": cfg.MAX_REPLICAS,
        "auto_delete": cfg.AUTO_DELETE,
        "engine_variant": cfg.ENGINE_VARIANT,
        "use_custom_variant": custom_config.enabled if custom_config else False,
        "platform": current_platform,
        "manual_mode": False,
        "manual_engines": [],
    }
    
    # Save defaults for future use
    try:
        if SettingsPersistence.save_engine_settings(default_settings):
            logger.info("Created default engine settings on first access")
    except Exception as e:
        logger.warning(f"Failed to save default engine settings: {e}")
    
    return default_settings

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
    from .services.custom_variant_config import detect_platform
    
    # Load current persisted settings or use runtime config as base
    current_platform = detect_platform()
    current_settings = SettingsPersistence.load_engine_settings() or {
        "min_replicas": cfg.MIN_REPLICAS,
        "max_replicas": cfg.MAX_REPLICAS,
        "auto_delete": cfg.AUTO_DELETE,
        "engine_variant": cfg.ENGINE_VARIANT,
        "use_custom_variant": False,
        "platform": current_platform,
        "manual_mode": False,
        "manual_engines": [],
    }
    
    # Always ensure the platform field in persisted settings is corrected to real-time
    current_settings["platform"] = current_platform
    
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
    
    if settings.engine_variant is not None:
        current_settings["engine_variant"] = settings.engine_variant
        # Update cfg.ENGINE_VARIANT at runtime so it takes effect during reprovisioning
        cfg.ENGINE_VARIANT = settings.engine_variant
    
    if settings.use_custom_variant is not None:
        current_settings["use_custom_variant"] = settings.use_custom_variant
        # Update custom variant config
        from .services.custom_variant_config import get_config as get_custom_config, save_config as save_custom_config
        custom_config = get_custom_config()
        if custom_config:
            custom_config.enabled = settings.use_custom_variant
            save_custom_config(custom_config)
            
    if settings.manual_mode is not None:
        current_settings["manual_mode"] = settings.manual_mode
        
    if settings.manual_engines is not None:
        current_settings["manual_engines"] = settings.manual_engines
        
    # Inject manual engines into state if manual mode is enabled
    if current_settings.get("manual_mode"):
        from .services.state import state
        from .models.schemas import EngineState
        
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
    
    # Persist settings to JSON file
    if SettingsPersistence.save_engine_settings(current_settings):
        logger.info(f"Engine settings persisted: {current_settings}")
    else:
        logger.warning("Failed to persist engine settings to JSON file")
    
    return {
        "message": "Engine settings updated and persisted",
        **current_settings
    }


# ============================================================================
# Settings Backup/Restore Endpoints
# ============================================================================

# Backup format version - increment when backup structure changes
BACKUP_FORMAT_VERSION = "1.0"

@app.get("/settings/export")
async def export_settings(api_key_param: str = Depends(require_api_key)):
    """
    Export all settings (custom engine settings, templates, proxy, loop detection) as a ZIP file.
    
    Returns:
        ZIP file containing all settings as JSON files
    """
    import zipfile
    import io
    
    try:
        # Create an in-memory ZIP file
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Export custom engine variant config
            try:
                custom_config = get_custom_config()
                if custom_config:
                    config_json = json.dumps(custom_config.dict(), indent=2)
                    zip_file.writestr("custom_engine_variant.json", config_json)
                    logger.info("Added custom engine variant config to backup")
            except Exception as e:
                logger.warning(f"Failed to export custom engine variant config: {e}")
            
            # Export all custom templates
            try:
                templates_list = list_templates()
                for template_info in templates_list:
                    if template_info['exists']:
                        template = get_template(template_info['slot_id'])
                        if template:
                            template_json = json.dumps(template.to_dict(), indent=2)
                            zip_file.writestr(f"templates/template_{template.slot_id}.json", template_json)
                logger.info(f"Added {sum(1 for t in templates_list if t['exists'])} templates to backup")
            except Exception as e:
                logger.warning(f"Failed to export templates: {e}")
            
            # Export active template ID
            try:
                active_id = get_active_template_id()
                if active_id is not None:
                    active_template_json = json.dumps({"active_template_id": active_id}, indent=2)
                    zip_file.writestr("active_template.json", active_template_json)
                    logger.info(f"Added active template ID ({active_id}) to backup")
            except Exception as e:
                logger.warning(f"Failed to export active template ID: {e}")
            
            # Export proxy settings
            try:
                from .proxy.config_helper import Config as ProxyConfig
                proxy_settings = {
                    "initial_data_wait_timeout": ProxyConfig.INITIAL_DATA_WAIT_TIMEOUT,
                    "initial_data_check_interval": ProxyConfig.INITIAL_DATA_CHECK_INTERVAL,
                    "no_data_timeout_checks": ProxyConfig.NO_DATA_TIMEOUT_CHECKS,
                    "no_data_check_interval": ProxyConfig.NO_DATA_CHECK_INTERVAL,
                    "connection_timeout": ProxyConfig.CONNECTION_TIMEOUT,
                    "stream_timeout": ProxyConfig.STREAM_TIMEOUT,
                    "channel_shutdown_delay": ProxyConfig.CHANNEL_SHUTDOWN_DELAY,
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
    import_custom_variant: bool = Query(True),
    import_templates: bool = Query(True),
    import_proxy: bool = Query(True),
    import_loop_detection: bool = Query(True),
    import_engine: bool = Query(True),
    api_key_param: str = Depends(require_api_key)
):
    """
    Import settings from uploaded ZIP file data.
    
    Query Parameters:
        import_custom_variant: Whether to import custom engine variant config
        import_templates: Whether to import templates  
        import_proxy: Whether to import proxy settings
        import_loop_detection: Whether to import loop detection settings
        import_engine: Whether to import engine settings
    
    Returns:
        Summary of imported settings
    """
    import zipfile
    import io
    from .services.settings_persistence import SettingsPersistence
    
    try:
        # Read the uploaded file data
        file_data = await request.body()
        
        if not file_data:
            raise HTTPException(status_code=400, detail="No file data provided")
        
        imported = {
            "custom_variant": False,
            "templates": 0,
            "active_template": False,
            "proxy": False,
            "loop_detection": False,
            "engine": False,
            "errors": []
        }
        
        # Create a BytesIO object from the uploaded data
        zip_buffer = io.BytesIO(file_data)
        
        with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
            # Import custom engine variant config
            if import_custom_variant and "custom_engine_variant.json" in zip_file.namelist():
                try:
                    config_data = zip_file.read("custom_engine_variant.json").decode('utf-8')
                    config_dict = json.loads(config_data)
                    config = CustomVariantConfig(**config_dict)
                    if save_custom_config(config):
                        reload_config()
                        imported["custom_variant"] = True
                        logger.info("Imported custom engine variant config")
                except Exception as e:
                    error_msg = f"Failed to import custom engine variant config: {e}"
                    logger.error(error_msg)
                    imported["errors"].append(error_msg)
            
            # Import templates
            if import_templates:
                template_files = [f for f in zip_file.namelist() if f.startswith("templates/")]
                for template_file in template_files:
                    try:
                        template_data = zip_file.read(template_file).decode('utf-8')
                        template_dict = json.loads(template_data)
                        
                        slot_id = template_dict['slot_id']
                        name = template_dict['name']
                        config = CustomVariantConfig(**template_dict['config'])
                        
                        if save_template(slot_id, name, config):
                            imported["templates"] += 1
                            logger.info(f"Imported template {slot_id}: {name}")
                    except Exception as e:
                        error_msg = f"Failed to import template from {template_file}: {e}"
                        logger.error(error_msg)
                        imported["errors"].append(error_msg)
            
            # Import active template ID
            if import_templates and "active_template.json" in zip_file.namelist():
                try:
                    active_data = zip_file.read("active_template.json").decode('utf-8')
                    active_dict = json.loads(active_data)
                    active_id = active_dict.get('active_template_id')
                    if active_id is not None:
                        set_active_template(active_id)
                        imported["active_template"] = True
                        logger.info(f"Set active template to {active_id}")
                except Exception as e:
                    error_msg = f"Failed to import active template ID: {e}"
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
