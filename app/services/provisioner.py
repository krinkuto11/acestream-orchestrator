import time
from pydantic import BaseModel
from .docker_client import get_client, safe
from ..core.config import cfg
from .ports import alloc

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

def start_acestream(req: AceProvisionRequest) -> AceProvisionResponse:
    from .naming import generate_container_name
    
    # Check Gluetun health if configured
    if cfg.GLUETUN_CONTAINER_NAME:
        import asyncio
        from .gluetun import gluetun_monitor
        
        # Check if Gluetun is healthy before starting engine
        try:
            loop = asyncio.get_event_loop()
            if not loop.run_until_complete(gluetun_monitor.wait_for_healthy(timeout=30)):
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
        alloc.reserve_http(c_http)
    else:
        # No user http port - use orchestrator allocation
        host_http = req.host_port or alloc.alloc_host()
        c_http = host_http  # Use same port for internal container to match acestream-http-proxy expectations
        # Reserve this port to avoid conflicts
        alloc.reserve_http(c_http)
    
    if user_https_port is not None:
        # User specified https port in CONF - use it
        c_https = user_https_port
        # Reserve this port to avoid conflicts
        alloc.reserve_https(c_https)
    else:
        # No user https port - use orchestrator allocation
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
        "BIND_ALL": "true"
    }

    key, val = cfg.CONTAINER_LABEL.split("=")
    labels = {**req.labels, key: val,
              ACESTREAM_LABEL_HTTP: str(c_http),
              ACESTREAM_LABEL_HTTPS: str(c_https),
              HOST_LABEL_HTTP: str(host_http)}

    ports = {f"{c_http}/tcp": host_http}
    if cfg.ACE_MAP_HTTPS:
        host_https = alloc.alloc_host()
        ports[f"{c_https}/tcp"] = host_https
        labels[HOST_LABEL_HTTPS] = str(host_https)

    # Generate a meaningful container name
    container_name = generate_container_name("acestream")

    # Determine network configuration based on Gluetun setup
    network_config = _get_network_config()

    cli = get_client()
    cont = safe(cli.containers.run,
        req.image or cfg.TARGET_IMAGE,
        detach=True,
        name=container_name,
        environment=env,
        labels=labels,
        **network_config,
        ports=ports,
        restart_policy={"Name": "unless-stopped"})
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
