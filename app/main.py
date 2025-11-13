from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
from datetime import datetime, timezone
import asyncio
import os
import json
import logging

from .utils.logging import setup
from .utils.debug_logger import init_debug_logger, get_debug_logger
from .core.config import cfg
from .services.autoscaler import ensure_minimum, scale_to, can_stop_engine
from .services.provisioner import StartRequest, start_container, stop_container, AceProvisionRequest, AceProvisionResponse, start_acestream, HOST_LABEL_HTTP
from .services.health import sweep_idle
from .services.health_monitor import health_monitor
from .services.health_manager import health_manager
from .services.inspect import inspect_container, ContainerNotFound
from .services.state import state, load_state_from_db, cleanup_on_shutdown
from .models.schemas import StreamStartedEvent, StreamEndedEvent, EngineState, StreamState, StreamStatSnapshot
from .services.collector import collector
from .services.monitor import docker_monitor
from .services.metrics import update_custom_metrics
from .services.auth import require_api_key
from .services.db import engine
from .models.db_models import Base
from .services.reindex import reindex_existing
from .services.gluetun import gluetun_monitor

logger = logging.getLogger(__name__)

setup()

# Initialize debug logger if enabled
debug_logger = init_debug_logger(enabled=cfg.DEBUG_MODE, log_dir=cfg.DEBUG_LOG_DIR)
if cfg.DEBUG_MODE:
    logger.info(f"Debug mode enabled. Logs will be written to: {cfg.DEBUG_LOG_DIR}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - Ensure clean start (dry run)
    Base.metadata.create_all(bind=engine)
    cleanup_on_shutdown()  # Clean any existing state and containers after DB is ready
    
    # Load state from database first
    load_state_from_db()
    
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
    asyncio.create_task(docker_monitor.start())  # Start Docker monitoring
    asyncio.create_task(health_monitor.start())  # Start health monitoring  
    asyncio.create_task(health_manager.start())  # Start proactive health management
    reindex_existing()  # Final reindex to ensure all containers are properly tracked
    
    yield
    
    # Shutdown
    await collector.stop()
    await docker_monitor.stop()  # Stop Docker monitoring
    await health_monitor.stop()  # Stop health monitoring
    await health_manager.stop()  # Stop health management
    await gluetun_monitor.stop()  # Stop Gluetun monitoring
    
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
    from fastapi.responses import FileResponse
    
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
    stop_container(container_id)
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
    return state.on_stream_started(evt)

@app.post("/events/stream_ended", dependencies=[Depends(require_api_key)])
def ev_stream_ended(evt: StreamEndedEvent, bg: BackgroundTasks):
    st = state.on_stream_ended(evt)
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
            # Add engine variant from config
            engine.engine_variant = cfg.ENGINE_VARIANT
            
            # Get engine version info
            try:
                version_info = get_engine_version_info_sync(engine.host, engine.port)
                if version_info:
                    engine.platform = version_info.get("platform")
                    engine.version = version_info.get("version")
            except Exception as e:
                logger.debug(f"Could not get version info for engine {engine.container_id[:12]}: {e}")
            
            # Get forwarded port for forwarded engines
            if engine.forwarded and engine.vpn_container:
                try:
                    port = get_forwarded_port_sync(engine.vpn_container)
                    if port:
                        engine.forwarded_port = port
                except Exception as e:
                    logger.debug(f"Could not get forwarded port for engine {engine.container_id[:12]}: {e}")
        
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

@app.get("/streams", response_model=List[StreamState])
def get_streams(status: Optional[str] = Query(None, pattern="^(started|ended)$"), container_id: Optional[str] = None):
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
    Get VPN (Gluetun) status information with location data.
    
    Location data (provider, country, city, region) is now obtained directly from:
    - Provider: VPN_SERVICE_PROVIDER docker environment variable
    - Location: Gluetun's /v1/publicip/ip endpoint
    """
    vpn_status = get_vpn_status()
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
    Get comprehensive orchestrator status for proxy integration.
    This endpoint provides all the information a proxy needs to understand
    the orchestrator's current state including VPN, provisioning, and health status.
    
    Enhanced to provide detailed provisioning status with recovery guidance.
    """
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
    
    return {
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

# WebSocket endpoint removed - using simple polling approach
