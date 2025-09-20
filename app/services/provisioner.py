import time
import re
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
    host_http_port: int
    container_http_port: int
    container_https_port: int

def start_container(req: StartRequest) -> str:
    cli = get_client()
    key, val = cfg.CONTAINER_LABEL.split("=")
    labels = {**req.labels, key: val}
    image_name = req.image or cfg.TARGET_IMAGE
    
    try:
        cont = safe(cli.containers.run,
            image_name,
            detach=True,
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
    return cont.id

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

def _parse_ports_from_conf(conf: str) -> tuple[int | None, int | None]:
    """
    Parse HTTP and HTTPS ports from user-provided CONF string.
    Returns (http_port, https_port) where None means not found.
    """
    http_port = None
    https_port = None
    
    if not conf:
        return http_port, https_port
    
    # Look for --http-port=XXXX
    http_match = re.search(r'--http-port=(\d+)', conf)
    if http_match:
        http_port = int(http_match.group(1))
    
    # Look for --https-port=XXXX  
    https_match = re.search(r'--https-port=(\d+)', conf)
    if https_match:
        https_port = int(https_match.group(1))
    
    return http_port, https_port


def _validate_user_ports(http_port: int | None, https_port: int | None) -> None:
    """
    Validate that user-provided ports are within valid port range (1-65535).
    Raises RuntimeError if ports are invalid.
    """
    if http_port is not None:
        if not (1 <= http_port <= 65535):
            raise RuntimeError(f"HTTP port {http_port} is outside valid port range (1-65535)")
    
    if https_port is not None:
        if not (1 <= https_port <= 65535):
            raise RuntimeError(f"HTTPS port {https_port} is outside valid port range (1-65535)")
    
    # Check for port conflicts (same port for both HTTP and HTTPS)
    if http_port is not None and https_port is not None and http_port == https_port:
        raise RuntimeError(f"HTTP and HTTPS cannot use the same port {http_port}")


def _reserve_user_ports(http_port: int | None, https_port: int | None) -> None:
    """
    Reserve user-provided ports in the allocator if they fall within managed ranges.
    This prevents the allocator from assigning these ports to other containers.
    """
    if http_port is not None:
        # Check if the user's HTTP port falls within the managed HTTP range
        http_min, http_max = map(int, cfg.ACE_HTTP_RANGE.split('-'))
        if http_min <= http_port <= http_max:
            alloc.reserve_http(http_port)
    
    if https_port is not None:
        # Check if the user's HTTPS port falls within the managed HTTPS range
        https_min, https_max = map(int, cfg.ACE_HTTPS_RANGE.split('-'))
        if https_min <= https_port <= https_max:
            alloc.reserve_https(https_port)


def start_acestream(req: AceProvisionRequest) -> AceProvisionResponse:
    host_http = req.host_port or alloc.alloc_host()
    
    # Use user-provided CONF if available, otherwise use default configuration
    if "CONF" in req.env:
        # User explicitly provided CONF (even if empty), use it as-is
        final_conf = req.env["CONF"]
        
        # Extract ports from user CONF to use for Docker port mapping
        user_http_port, user_https_port = _parse_ports_from_conf(final_conf)
        
        # Validate user-provided ports are within valid ranges
        _validate_user_ports(user_http_port, user_https_port)
        
        # Reserve user-provided ports if they fall within managed ranges
        _reserve_user_ports(user_http_port, user_https_port)
        
        if user_http_port is not None:
            # User specified HTTP port in CONF, use it for container port
            c_http = user_http_port
        else:
            # No HTTP port in user CONF, allocate one
            c_http = alloc.alloc_http()
            
        if user_https_port is not None:
            # User specified HTTPS port in CONF, use it for container port
            c_https = user_https_port
        else:
            # No HTTPS port in user CONF, allocate one avoiding HTTP port
            c_https = alloc.alloc_https(avoid=c_http)
    else:
        # No user CONF, use default orchestrator configuration
        c_http = alloc.alloc_http()
        c_https = alloc.alloc_https(avoid=c_http)
        conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
        final_conf = "\n".join(conf_lines)
    
    env = {**req.env, "CONF": final_conf}

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

    cli = get_client()
    cont = safe(cli.containers.run,
        req.image or cfg.TARGET_IMAGE,
        detach=True,
        environment=env,
        labels=labels,
        network=cfg.DOCKER_NETWORK if cfg.DOCKER_NETWORK else None,
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
    return AceProvisionResponse(container_id=cont.id, host_http_port=host_http, container_http_port=c_http, container_https_port=c_https)
