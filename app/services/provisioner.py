import time
import logging
from pydantic import BaseModel
from .docker_client import get_client, safe
from ..core.config import cfg
from .ports import alloc

logger = logging.getLogger(__name__)

ACESTREAM_LABEL_HTTP = "acestream.http_port"
ACESTREAM_LABEL_HTTPS = "acestream.https_port"
HOST_LABEL_HTTP = "host.http_port"
HOST_LABEL_HTTPS = "host.https_port"

class StartRequest(BaseModel):
    image: str | None = None
    env: dict = {}
    labels: dict = {}
    ports: dict = {}
    name_prefix: str = "svc"

class AceProvisionRequest(BaseModel):
    image: str | None = None
    labels: dict = {}
    env: dict = {}
    host_port: int | None = None  # optional fixed host port

class AceProvisionResponse(BaseModel):
    container_id: str
    container_name: str
    host_http_port: int
    container_http_port: int
    container_https_port: int

def start_container(req: StartRequest) -> dict:
    from .naming import generate_container_name
    
    cli = get_client()
    key, val = cfg.CONTAINER_LABEL.split("=")
    labels = {**req.labels, key: val}
    image_name = req.image or cfg.TARGET_IMAGE
    
    # Generate a meaningful container name
    container_name = generate_container_name(req.name_prefix)
    
    try:
        cont = safe(cli.containers.run,
            image_name,
            detach=True,
            name=container_name,
            environment=req.env or None,
            labels=labels,
            network=cfg.DOCKER_NETWORK if cfg.DOCKER_NETWORK else None,
            ports=req.ports or None,
            restart_policy={"Name": "unless-stopped"})
    except RuntimeError as e:
        # Provide more helpful error messages for common image issues
        error_msg = str(e).lower()
        if "not found" in error_msg or "pull access denied" in error_msg:
            raise RuntimeError(f"Image '{image_name}' not found. Please check TARGET_IMAGE setting or pull the image manually: docker pull {image_name}")
        elif "network" in error_msg:
            raise RuntimeError(f"Network error starting container with image '{image_name}': {e}")
        else:
            raise RuntimeError(f"Failed to start container with image '{image_name}': {e}")
    
    deadline = time.time() + cfg.STARTUP_TIMEOUT_S
    cont.reload()
    while cont.status not in ("running",) and time.time() < deadline:
        time.sleep(0.5); cont.reload()
    if cont.status != "running":
        cont.remove(force=True)
        raise RuntimeError(f"Container failed to start within {cfg.STARTUP_TIMEOUT_S}s (status: {cont.status})")
    
    # Get container name - should match what we set
    cont.reload()
    actual_container_name = cont.attrs.get("Name", "").lstrip("/")
    
    return {"container_id": cont.id, "container_name": actual_container_name}

def _release_ports_from_labels(labels: dict):
    try:
        hp = labels.get(HOST_LABEL_HTTP); alloc.free_host(int(hp) if hp else None)
    except Exception: pass
    try:
        if labels.get(HOST_LABEL_HTTPS):
            alloc.free_host(int(labels.get(HOST_LABEL_HTTPS)))
    except Exception: pass
    try:
        cp = labels.get(ACESTREAM_LABEL_HTTP); alloc.free_http(int(cp) if cp else None)
    except Exception: pass
    try:
        sp = labels.get(ACESTREAM_LABEL_HTTPS); alloc.free_https(int(sp) if sp else None)
    except Exception: pass
    
    # Release Gluetun ports if using Gluetun
    if cfg.GLUETUN_CONTAINER_NAME:
        try:
            hp = labels.get(HOST_LABEL_HTTP); alloc.free_gluetun_port(int(hp) if hp else None)
        except Exception: pass
        try:
            cp = labels.get(ACESTREAM_LABEL_HTTP); alloc.free_gluetun_port(int(cp) if cp else None)
        except Exception: pass

def clear_acestream_cache(container_id: str) -> tuple[bool, int]:
    """
    Clear the AceStream cache in a container.
    
    Args:
        container_id: The ID of the container to clear cache in
        
    Returns:
        tuple[bool, int]: (success, cache_size_bytes) - True if cache was cleared successfully, 
                         and the size of the cache before cleanup in bytes (0 if unknown)
    """
    try:
        cli = get_client()
        cont = cli.containers.get(container_id)
        
        # Check if container is running
        if cont.status != "running":
            logger.debug(f"Container {container_id[:12]} is not running, skipping cache cleanup")
            return (False, 0)
        
        # Get cache size before cleanup
        cache_size = 0
        try:
            size_result = cont.exec_run("du -sb /home/appuser/.ACEStream/.acestream_cache 2>/dev/null || echo 0", demux=False)
            if size_result.exit_code == 0:
                output = size_result.output.decode('utf-8').strip()
                if output and output != '0':
                    # Parse output like "12345\t/path/to/cache"
                    cache_size = int(output.split()[0])
        except Exception as e:
            logger.debug(f"Failed to get cache size for container {container_id[:12]}: {e}")
        
        # Execute cache cleanup command
        result = cont.exec_run("rm -rf /home/appuser/.ACEStream/.acestream_cache", demux=False)
        
        if result.exit_code == 0:
            # Only log if meaningful amount of cache was cleared (>0MB)
            cache_size_mb = cache_size / 1024 / 1024
            if cache_size_mb > 0:
                logger.info(f"Cleared {cache_size_mb:.1f}MB cache from {container_id[:12]}")
            return (True, cache_size)
        else:
            logger.warning(f"Cache cleanup command returned non-zero exit code {result.exit_code} for container {container_id[:12]}")
            return (False, cache_size)
    except Exception as e:
        logger.warning(f"Failed to clear AceStream cache for container {container_id[:12]}: {e}")
        return (False, 0)

def stop_container(container_id: str):
    cli = get_client()
    cont = cli.containers.get(container_id)
    labels = cont.labels or {}
    cont.stop(timeout=10)
    try:
        _release_ports_from_labels(labels)
    finally:
        cont.remove()

def _parse_conf_port(conf_string, port_type="http"):
    """
    Parse a CONF string to extract port number for given type.
    
    Args:
        conf_string: String like "--http-port=6879\n--https-port=6880\n--bind-all"
        port_type: "http" or "https"
    
    Returns:
        int: Port number or None if not found or invalid
    """
    if not conf_string:
        return None
        
    lines = conf_string.split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith(f"--{port_type}-port="):
            try:
                port_str = line.split('=', 1)[1]
                port = int(port_str)
                # Validate port range (1-65535)
                if 1 <= port <= 65535:
                    return port
            except (IndexError, ValueError):
                continue
    return None


def _get_network_config():
    """Get network configuration for container based on Gluetun setup."""
    if cfg.GLUETUN_CONTAINER_NAME:
        # Use Gluetun container's network stack
        return {
            "network_mode": f"container:{cfg.GLUETUN_CONTAINER_NAME}"
        }
    elif cfg.DOCKER_NETWORK:
        # Use specified Docker network
        return {
            "network": cfg.DOCKER_NETWORK
        }
    else:
        # Use default network
        return {
            "network": None
        }

def _check_gluetun_health_sync() -> bool:
    """Synchronous version of Gluetun health check."""
    try:
        from ..core.config import cfg
        from .docker_client import get_client
        from docker.errors import NotFound
        
        cli = get_client()
        container = cli.containers.get(cfg.GLUETUN_CONTAINER_NAME)
        container.reload()
        
        # Check container status
        if container.status != "running":
            return False
        
        # Check Docker health status if available
        health = container.attrs.get("State", {}).get("Health", {})
        if health:
            health_status = health.get("Status")
            if health_status == "unhealthy":
                return False
            elif health_status == "healthy":
                return True
            else:
                # Health status might be "starting" or "none"
                # Consider container healthy if running but health status is starting/none
                return True
        else:
            # No health check configured, consider healthy if running
            return True
            
    except NotFound:
        return False
    except Exception:
        return False

def start_acestream(req: AceProvisionRequest) -> AceProvisionResponse:
    from .naming import generate_container_name
    import time
    
    # Check Gluetun health if configured
    if cfg.GLUETUN_CONTAINER_NAME:
        from .gluetun import gluetun_monitor
        
        # Check if Gluetun is healthy before starting engine
        try:
            # Use a shorter timeout since we should have verified Gluetun health during startup
            timeout = 5  # Reduced from 30 to 5 seconds
            start_time = time.time()
            
            while (time.time() - start_time) < timeout:
                current_health = gluetun_monitor.is_healthy()
                if current_health is True:
                    break
                elif current_health is False:
                    # Force a fresh health check
                    import asyncio
                    try:
                        # Try to get health status synchronously
                        if _check_gluetun_health_sync():
                            break
                    except Exception:
                        pass
                
                time.sleep(0.5)  # Check more frequently since timeout is shorter
            else:
                # Timeout reached without becoming healthy
                raise RuntimeError(f"Gluetun VPN container '{cfg.GLUETUN_CONTAINER_NAME}' is not healthy - cannot start AceStream engine")
                
        except Exception as e:
            raise RuntimeError(f"Failed to verify Gluetun health: {e}")
    
    # Check if user provided CONF and extract ports from it
    user_conf = req.env.get("CONF")
    user_http_port = _parse_conf_port(user_conf, "http") if user_conf else None
    user_https_port = _parse_conf_port(user_conf, "https") if user_conf else None
    
    # Determine ports to use
    if user_http_port is not None:
        # User specified http port in CONF - use it for both container and host binding
        c_http = user_http_port
        host_http = req.host_port or user_http_port  # Use same port for host binding
        # Reserve this port to avoid conflicts
        if cfg.GLUETUN_CONTAINER_NAME:
            alloc.reserve_gluetun_port(c_http)
        else:
            alloc.reserve_http(c_http)
    else:
        # No user http port - use orchestrator allocation
        if cfg.GLUETUN_CONTAINER_NAME:
            # When using Gluetun, allocate from the Gluetun port range
            host_http = alloc.alloc_gluetun_port()
            c_http = host_http  # Same port for container and host
        else:
            # Normal allocation
            host_http = req.host_port or alloc.alloc_host()
            c_http = host_http  # Use same port for internal container to match acestream-http-proxy expectations
            # Reserve this port to avoid conflicts
            alloc.reserve_http(c_http)
    
    if user_https_port is not None:
        # User specified https port in CONF - use it
        c_https = user_https_port
        # Reserve this port to avoid conflicts
        if cfg.GLUETUN_CONTAINER_NAME:
            alloc.reserve_gluetun_port(c_https)
        else:
            alloc.reserve_https(c_https)
    else:
        # No user https port - use orchestrator allocation
        if cfg.GLUETUN_CONTAINER_NAME:
            # When using Gluetun, use a port in the Gluetun range (avoid HTTP port)
            for attempt in range(cfg.MAX_ACTIVE_REPLICAS):
                c_https = alloc.alloc_gluetun_port()
                if c_https != c_http:  # Ensure HTTPS port is different from HTTP
                    break
            else:
                raise RuntimeError("Could not allocate HTTPS port different from HTTP port")
        else:
            # Normal allocation
            c_https = alloc.alloc_https(avoid=c_http)

    # Use user-provided CONF if available, otherwise use default configuration
    if "CONF" in req.env:
        # User explicitly provided CONF (even if empty), use it as-is
        final_conf = req.env["CONF"]
    else:
        # No user CONF, use default orchestrator configuration
        conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
        final_conf = "\n".join(conf_lines)
    
    # Set environment variables required by acestream-http-proxy image
    env = {
        **req.env, 
        "CONF": final_conf,
        "HTTP_PORT": str(c_http),
        "HTTPS_PORT": str(c_https),
        "BIND_ALL": "true",
        "INTERNAL_BUFFERING": 60,
        "CACHE_LIMIT": 1
    }
    
    # Add P2P_PORT when using Gluetun
    if cfg.GLUETUN_CONTAINER_NAME:
        from .gluetun import get_forwarded_port_sync
        p2p_port = get_forwarded_port_sync()
        if p2p_port:
            env["P2P_PORT"] = str(p2p_port)
        else:
            # If we can't get the forwarded port, we'll continue without it
            # The AceStream engine will use its default P2P port behavior
            pass

    key, val = cfg.CONTAINER_LABEL.split("=")
    labels = {**req.labels, key: val,
              ACESTREAM_LABEL_HTTP: str(c_http),
              ACESTREAM_LABEL_HTTPS: str(c_https),
              HOST_LABEL_HTTP: str(host_http)}

    # Skip port mappings when using Gluetun - ports are already mapped through Gluetun container
    if cfg.GLUETUN_CONTAINER_NAME:
        ports = None
    else:
        ports = {f"{c_http}/tcp": host_http}
        if cfg.ACE_MAP_HTTPS:
            host_https = alloc.alloc_host()
            ports[f"{c_https}/tcp"] = host_https
            labels[HOST_LABEL_HTTPS] = str(host_https)

    # Generate a meaningful container name with retry logic for conflicts
    container_name = generate_container_name("acestream")

    # Determine network configuration based on Gluetun setup
    network_config = _get_network_config()

    cli = get_client()
    
    # Build container arguments, conditionally including ports
    container_args = {
        "image": req.image or cfg.TARGET_IMAGE,
        "detach": True,
        "name": container_name,
        "environment": env,
        "labels": labels,
        **network_config,
        "restart_policy": {"Name": "unless-stopped"}
    }
    
    # Only add ports if not using Gluetun (ports are handled by Gluetun container)
    if ports is not None:
        container_args["ports"] = ports
    
    # Retry container creation with different names if there are conflicts
    max_retries = 5
    for attempt in range(max_retries):
        try:
            cont = safe(cli.containers.run, **container_args)
            break
        except RuntimeError as e:
            # Check if this is a naming conflict
            if "Conflict" in str(e) and "name" in str(e).lower() and attempt < max_retries - 1:
                # Generate a new name and try again
                import time
                time.sleep(0.1)  # Small delay to avoid rapid conflicts
                container_name = generate_container_name("acestream")
                container_args["name"] = container_name
                continue
            else:
                # Not a naming conflict or max retries reached, re-raise
                raise
    deadline = time.time() + cfg.STARTUP_TIMEOUT_S
    cont.reload()
    while cont.status not in ("running",) and time.time() < deadline:
        time.sleep(0.5); cont.reload()
    if cont.status != "running":
        _release_ports_from_labels(labels)
        cont.remove(force=True)
        raise RuntimeError("Arranque AceStream fallido")

    
    # Get container name - should match what we set
    cont.reload()
    actual_container_name = cont.attrs.get("Name", "").lstrip("/")
    
    return AceProvisionResponse(
        container_id=cont.id, 
        container_name=actual_container_name,
        host_http_port=host_http, 
        container_http_port=c_http, 
        container_https_port=c_https
    )
