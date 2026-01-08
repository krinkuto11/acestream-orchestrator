from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Optional, List
from datetime import datetime, timezone
import asyncio
import os
import json
import logging
import httpx

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
from .services.metrics import update_custom_metrics
from .services.auth import require_api_key
from .services.db import engine
from .models.db_models import Base
from .services.reindex import reindex_existing
from .services.gluetun import gluetun_monitor
from .services.docker_stats import get_container_stats, get_multiple_container_stats, get_total_stats
from .services.docker_stats_collector import docker_stats_collector
from .services.cache import start_cleanup_task, stop_cleanup_task, invalidate_cache, get_cache
from .services.acexy import acexy_sync_service
from .services.stream_loop_detector import stream_loop_detector
from .services.looping_streams import looping_streams_tracker
from .services.proxy_manager import ProxyManager

logger = logging.getLogger(__name__)

setup()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - Ensure clean start (dry run)
    Base.metadata.create_all(bind=engine)
    cleanup_on_shutdown()  # Clean any existing state and containers after DB is ready
    
    # Load custom variant configuration early to ensure it's available
    from .services.custom_variant_config import load_config as load_custom_config, save_config as save_custom_config
    from .services.template_manager import get_active_template_id, get_template
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
                    template_config = template.config.copy(deep=True)
                    template_config.enabled = custom_config.enabled
                    save_custom_config(template_config)
                    logger.info(f"Successfully loaded template '{template.name}' (slot {active_template_id})")
                else:
                    logger.warning(f"Active template {active_template_id} not found, using current config")
        else:
            logger.debug("Custom engine variant is disabled or not configured")
    except Exception as e:
        logger.warning(f"Failed to load custom variant config during startup: {e}")
    
    # Load state from database first
    load_state_from_db()
    
    # Initialize looping streams tracker with configured retention
    looping_streams_tracker.set_retention_minutes(cfg.STREAM_LOOP_RETENTION_MINUTES)
    
    # Start Gluetun monitoring BEFORE provisioning to avoid race condition
    # This ensures health checks work when ensure_minimum() tries to start engines
    await gluetun_monitor.start()
    
    # Wait for Gluetun to become healthy before provisioning engines
    # This prevents the slow startup issue where each engine creation waits 30s
    if cfg.GLUETUN_CONTAINER_NAME:
        logger.info("Waiting for Gluetun to become healthy before provisioning engines...")
        max_wait_time = 60  # Maximum 60 seconds to wait for Gluetun
        wait_start = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - wait_start) < max_wait_time:
            if gluetun_monitor.is_healthy() is True:
                logger.info("Gluetun is healthy - proceeding with engine provisioning")
                
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
                
                break
            await asyncio.sleep(1)
        else:
            logger.warning(f"Gluetun did not become healthy within {max_wait_time}s - proceeding anyway")
    
    # Now provision engines with Gluetun health checks working
    # On startup, provision MIN_REPLICAS total containers
    ensure_minimum(initial_startup=True)
    
    # Start remaining monitoring services
    asyncio.create_task(collector.start())
    asyncio.create_task(stream_cleanup.start())  # Start stream cleanup service
    asyncio.create_task(docker_monitor.start())  # Start Docker monitoring
    asyncio.create_task(health_monitor.start())  # Start health monitoring  
    asyncio.create_task(health_manager.start())  # Start proactive health management
    asyncio.create_task(docker_stats_collector.start())  # Start Docker stats collection
    asyncio.create_task(acexy_sync_service.start())  # Start Acexy sync service
    asyncio.create_task(stream_loop_detector.start())  # Start stream loop detection
    asyncio.create_task(looping_streams_tracker.start())  # Start looping streams tracker
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
    await acexy_sync_service.stop()  # Stop Acexy sync service
    await stream_loop_detector.stop()  # Stop stream loop detector
    await looping_streams_tracker.stop()  # Stop looping streams tracker
    await stop_cleanup_task()  # Stop cache cleanup
    
    # Give a small delay to ensure any pending operations complete
    await asyncio.sleep(0.1)
    
    cleanup_on_shutdown()

app = FastAPI(title="On-Demand Orchestrator", lifespan=lifespan)
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
        
        running_container_ids = {c.id for c in list_managed() if c.status == "running"}
        
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
                version_info = get_engine_version_info_sync(engine.host, engine.port)
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
    from .utils.acestream_api import get_stream_extended_stats
    
    # Get the stream from state
    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    if not stream.stat_url:
        raise HTTPException(status_code=400, detail="Stream has no stat URL")
    
    # Fetch extended stats
    extended_stats = await get_stream_extended_stats(stream.stat_url)
    
    if extended_stats is None:
        raise HTTPException(status_code=503, detail="Unable to fetch extended stats from AceStream engine")
    
    return extended_stats

@app.get("/streams/{stream_id}/livepos")
async def get_stream_livepos(stream_id: str):
    """
    Get live position data for a stream from its stat URL.
    This returns livepos information including current position, buffer, and timestamps.
    """
    import httpx
    
    # Get the stream from state
    stream = state.get_stream(stream_id)
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    if not stream.stat_url:
        raise HTTPException(status_code=400, detail="Stream has no stat URL")
    
    # Fetch livepos data from stat URL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(stream.stat_url)
            response.raise_for_status()
            data = response.json()
            
            # Extract response payload
            payload = data.get("response")
            if not payload:
                raise HTTPException(status_code=503, detail="No response data from stat URL")
            
            # Extract livepos data
            livepos = payload.get("livepos")
            if not livepos:
                # Stream might not be live or doesn't have livepos data
                return {
                    "has_livepos": False,
                    "is_live": payload.get("is_live", 0) == 1
                }
            
            # Return livepos data with additional context
            # Extract all relevant fields from livepos according to AceStream API
            return {
                "has_livepos": True,
                "is_live": payload.get("is_live", 0) == 1,
                "livepos": {
                    "pos": livepos.get("pos"),
                    "live_first": livepos.get("live_first") or livepos.get("first_ts") or livepos.get("first"),
                    "live_last": livepos.get("live_last") or livepos.get("last_ts") or livepos.get("last"),
                    "first_ts": livepos.get("first_ts") or livepos.get("first"),
                    "last_ts": livepos.get("last_ts") or livepos.get("last"),
                    "buffer_pieces": livepos.get("buffer_pieces")
                }
            }
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch livepos for stream {stream_id}: {e}")
        raise HTTPException(status_code=503, detail=f"Failed to fetch livepos data: {str(e)}")
    except Exception as e:
        logger.error(f"Error processing livepos for stream {stream_id}: {e}")
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

@app.get("/acexy/status")
def get_acexy_status_endpoint():
    """
    Get Acexy proxy integration status.
    
    Returns information about the Acexy sync service including:
    - Whether Acexy integration is enabled
    - Acexy URL being used
    - Health status of Acexy connection
    - Sync interval configuration
    """
    return acexy_sync_service.get_status()

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
    engines_with_streams = len(set(stream.container_id for stream in active_streams))
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
        "acexy": acexy_sync_service.get_status(),
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
    validate_config,
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
    Validates the configuration before saving.
    """
    # Validate the configuration
    is_valid, error_msg = validate_config(config)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid configuration: {error_msg}")
    
    # Save the configuration
    success = save_custom_config(config)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save configuration")
    
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
                "message": f"Stopping {total_engines} engines..."
            })
            
            # Delete all engines
            for idx, engine_id in enumerate(engine_ids, 1):
                try:
                    _reprovision_state.update({
                        "current_engine_id": engine_id[:12],
                        "message": f"Stopping engine {idx}/{total_engines}: {engine_id[:12]}"
                    })
                    
                    stop_container(engine_id)
                    logger.info(f"Stopped engine {engine_id[:12]} ({idx}/{total_engines})")
                    
                    _reprovision_state["engines_stopped"] = idx
                except Exception as e:
                    logger.error(f"Failed to stop engine {engine_id[:12]}: {e}")
                    # Continue with remaining engines even if one fails
            
            # Update state: cleaning phase
            _reprovision_state.update({
                "current_phase": "cleaning",
                "current_engine_id": None,
                "message": "Cleaning up state..."
            })
            
            # Clear state
            cleanup_on_shutdown()
            
            # Reload custom config to ensure we have latest settings
            reload_config()
            
            # Give time for cleanup and monitoring systems to settle
            time.sleep(2)
            
            # Update state: provisioning phase
            _reprovision_state.update({
                "current_phase": "provisioning",
                "message": f"Provisioning {cfg.MIN_REPLICAS} new engines..."
            })
            
            # Reprovision minimum replicas
            ensure_minimum(initial_startup=True)
            logger.info(f"Successfully reprovisioned engines with new settings")
            
            # Exit reprovisioning mode
            state.exit_reprovisioning_mode()
            
            # Update state: complete
            _reprovision_state = {
                "in_progress": False,
                "status": "success",
                "message": f"Successfully reprovisioned all engines ({total_engines} stopped, {cfg.MIN_REPLICAS} provisioned)",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total_engines": total_engines,
                "engines_stopped": total_engines,
                "engines_provisioned": cfg.MIN_REPLICAS,
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
    template_config = template.config.copy(deep=True)
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

@app.get("/ace/getstream")
async def ace_getstream(
    id: str = Query(..., description="AceStream content ID (infohash or content_id)"),
):
    """Proxy endpoint for AceStream video streams.
    
    This endpoint:
    1. Selects the best available engine (prioritizes forwarded, balances load)
    2. Multiplexes multiple clients to the same stream
    3. Automatically manages stream lifecycle
    
    Args:
        id: AceStream content ID (infohash or content_id)
        
    Returns:
        Streaming response with video data
    """
    from fastapi.responses import StreamingResponse
    from uuid import uuid4
    
    proxy_manager = ProxyManager.get_instance()
    
    # Generate unique client ID
    client_id = str(uuid4())
    
    try:
        # Get or create session for this stream
        session = await proxy_manager.get_or_create_session(id)
        
        if not session:
            raise HTTPException(
                status_code=503,
                detail="Unable to provision stream. No engines available or stream initialization failed."
            )
        
        # Add this client to the session
        await proxy_manager.add_client(id, client_id)
        
        logger.info(
            f"Client {client_id} connected to stream {id} "
            f"(total clients: {session.client_manager.get_client_count()})"
        )
        
        # Stream data generator
        async def stream_generator():
            stream_failed = False
            try:
                async for chunk in session.stream_data(client_id):
                    yield chunk
            except RuntimeError as e:
                # Stream failed to start or encountered an error
                logger.error(f"Error streaming to client {client_id}: {e}")
                stream_failed = True
                # Remove the failed session from proxy manager
                await proxy_manager.remove_failed_session(id, reason=f"stream_error: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Error streaming to client {client_id}: {e}")
                stream_failed = True
                raise
            finally:
                # Remove client when done (only if session wasn't already removed due to failure)
                if not stream_failed:
                    remaining = await proxy_manager.remove_client(id, client_id)
                    logger.info(
                        f"Client {client_id} disconnected from stream {id} "
                        f"(remaining clients: {remaining})"
                    )
                else:
                    logger.info(f"Client {client_id} disconnected due to stream failure")
        
        # Return streaming response
        return StreamingResponse(
            stream_generator(),
            media_type="video/mp2t",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Connection": "keep-alive",
            }
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Unexpected error in ace_getstream: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/ace/manifest.m3u8")
async def ace_manifest(
    id: str = Query(..., description="AceStream content ID (infohash or content_id)"),
):
    """Proxy endpoint for AceStream HLS streams (M3U8).
    
    Note: This is a placeholder. Full HLS support would require
    additional implementation to handle manifest parsing and segment proxying.
    
    Args:
        id: AceStream content ID (infohash or content_id)
        
    Returns:
        HLS manifest
    """
    # For now, redirect to getstream endpoint which works for most players
    raise HTTPException(
        status_code=501,
        detail="HLS/M3U8 streaming not yet implemented. Use /ace/getstream for MPEG-TS streaming."
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
    
    return {
        "message": "Stream loop detection configuration updated",
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
    
    This endpoint is used by Acexy proxy to check if a stream is looping
    before selecting an engine. If a stream ID is in this list, Acexy
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


