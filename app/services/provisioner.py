import time
import logging
import docker
from docker.errors import NotFound
import threading
from typing import Optional, Dict, Any
from pydantic import BaseModel
from .docker_client import get_client, safe
from ..core.config import cfg
from .ports import alloc

logger = logging.getLogger(__name__)

# Lock and counters for VPN assignment to prevent race conditions during concurrent provisioning
_vpn_assignment_lock = threading.RLock()
# Track engines being provisioned per VPN (not yet in state) to ensure balanced allocation
_vpn_pending_engines: Dict[str, int] = {}


def _decrement_vpn_pending_counter(vpn_container: Optional[str]):
    """Safely decrement the pending engine counter for a VPN container."""
    if not vpn_container or cfg.VPN_MODE != 'redundant':
        return
    
    with _vpn_assignment_lock:
        if vpn_container in _vpn_pending_engines and _vpn_pending_engines[vpn_container] > 0:
            _vpn_pending_engines[vpn_container] -= 1
            logger.debug(f"Decremented pending counter for VPN '{vpn_container}' (now: {_vpn_pending_engines[vpn_container]})")

ACESTREAM_LABEL_HTTP = "acestream.http_port"
ACESTREAM_LABEL_HTTPS = "acestream.https_port"
ACESTREAM_LABEL_API = "acestream.api_port"
HOST_LABEL_HTTP = "host.http_port"
HOST_LABEL_HTTPS = "host.https_port"
HOST_LABEL_API = "host.api_port"
FORWARDED_LABEL = "acestream.forwarded"
VPN_CONTAINER_LABEL = "acestream.vpn_container"
ENGINE_VARIANT_LABEL = "acestream.engine_variant"

class StartRequest(BaseModel):
    image: str | None = None
    env: dict = {}
    labels: dict = {}
    ports: dict = {}
    name_prefix: str = "svc"

class AceProvisionRequest(BaseModel):
    labels: dict = {}
    env: dict = {}
    host_port: int | None = None  # optional fixed host port

class AceProvisionResponse(BaseModel):
    container_id: str
    container_name: str
    host_http_port: int
    container_http_port: int
    container_https_port: int
    host_api_port: Optional[int] = None
    container_api_port: Optional[int] = None


class EngineSpec(BaseModel):
    vpn_container: Optional[str] = None
    forwarded: bool = False
    p2p_port: Optional[int] = None
    host_http_port: int
    container_http_port: int
    container_https_port: int
    host_api_port: int
    container_api_port: int
    host_https_port: Optional[int] = None
    labels: Dict[str, str] = {}
    ports: Optional[Dict[str, int]] = None
    volumes: Dict[str, Any] = {}
    network_config: Dict[str, Any] = {}


class ResourceScheduler:
    """Atomically resolves VPN node, forwarding role, and port reservations for new engines."""

    def __init__(self):
        self._lock = _vpn_assignment_lock

    def schedule(self, req: "AceProvisionRequest", engine_variant_name: str, base_volumes: Optional[Dict[str, Any]] = None) -> EngineSpec:
        user_conf = req.env.get("CONF")
        user_http_port = _parse_conf_port(user_conf, "http") if user_conf else None
        user_https_port = _parse_conf_port(user_conf, "https") if user_conf else None
        user_api_port = _parse_conf_port(user_conf, "api") if user_conf else None

        vpn_container = None
        pending_incremented = False

        try:
            with self._lock:
                vpn_container = self._select_vpn_container_locked()
                if cfg.VPN_MODE == 'redundant' and vpn_container:
                    _vpn_pending_engines[vpn_container] = _vpn_pending_engines.get(vpn_container, 0) + 1
                    pending_incremented = True

                self._validate_vpn_health_locked(vpn_container)

                ports_info = alloc.allocate_engine_ports(
                    use_gluetun=bool(cfg.GLUETUN_CONTAINER_NAME),
                    vpn_container=vpn_container,
                    requested_host_port=req.host_port,
                    user_http_port=user_http_port,
                    user_https_port=user_https_port,
                    user_api_port=user_api_port,
                    map_https=bool(cfg.ACE_MAP_HTTPS),
                )

                forwarded, p2p_port = self._elect_forwarded_engine_locked(vpn_container)

                key, val = cfg.CONTAINER_LABEL.split("=", 1)
                labels = {
                    **req.labels,
                    key: val,
                    ACESTREAM_LABEL_HTTP: str(ports_info["container_http_port"]),
                    ACESTREAM_LABEL_HTTPS: str(ports_info["container_https_port"]),
                    ACESTREAM_LABEL_API: str(ports_info["container_api_port"]),
                    HOST_LABEL_HTTP: str(ports_info["host_http_port"]),
                    HOST_LABEL_API: str(ports_info["host_api_port"]),
                    ENGINE_VARIANT_LABEL: engine_variant_name,
                }

                if vpn_container:
                    labels[VPN_CONTAINER_LABEL] = vpn_container
                if forwarded:
                    labels[FORWARDED_LABEL] = "true"
                if ports_info.get("host_https_port") is not None:
                    labels[HOST_LABEL_HTTPS] = str(ports_info["host_https_port"])

                docker_ports = None
                if not cfg.GLUETUN_CONTAINER_NAME:
                    docker_ports = {
                        f"{ports_info['container_http_port']}/tcp": ports_info["host_http_port"],
                        f"{ports_info['container_api_port']}/tcp": ports_info["host_api_port"],
                    }
                    if ports_info.get("host_https_port") is not None:
                        docker_ports[f"{ports_info['container_https_port']}/tcp"] = ports_info["host_https_port"]

                return EngineSpec(
                    vpn_container=vpn_container,
                    forwarded=forwarded,
                    p2p_port=p2p_port,
                    host_http_port=ports_info["host_http_port"],
                    container_http_port=ports_info["container_http_port"],
                    container_https_port=ports_info["container_https_port"],
                    host_api_port=ports_info["host_api_port"],
                    container_api_port=ports_info["container_api_port"],
                    host_https_port=ports_info.get("host_https_port"),
                    labels=labels,
                    ports=docker_ports,
                    volumes=dict(base_volumes or {}),
                    network_config=_get_network_config(vpn_container),
                )
        except Exception:
            if pending_incremented:
                _decrement_vpn_pending_counter(vpn_container)
            raise

    def _select_vpn_container_locked(self) -> Optional[str]:
        if not cfg.GLUETUN_CONTAINER_NAME:
            return None

        from .state import state

        if cfg.VPN_MODE != 'redundant' or not cfg.GLUETUN_CONTAINER_NAME_2:
            return cfg.GLUETUN_CONTAINER_NAME

        recovery_target = state.get_vpn_recovery_target()
        if recovery_target:
            logger.info(f"VPN recovery mode active: assigning engine to recovery target VPN '{recovery_target}'")
            return recovery_target

        if state.is_emergency_mode():
            emergency_info = state.get_emergency_mode_info()
            healthy_vpn = emergency_info['healthy_vpn']
            logger.info(f"Emergency mode active: assigning engine to healthy VPN '{healthy_vpn}'")
            return healthy_vpn

        vpn1_name = cfg.GLUETUN_CONTAINER_NAME
        vpn2_name = cfg.GLUETUN_CONTAINER_NAME_2

        vpn1_engines = len(state.get_engines_by_vpn(vpn1_name)) + _vpn_pending_engines.get(vpn1_name, 0)
        vpn2_engines = len(state.get_engines_by_vpn(vpn2_name)) + _vpn_pending_engines.get(vpn2_name, 0)

        vpn1_healthy = self._vpn_is_healthy(vpn1_name)
        vpn2_healthy = self._vpn_is_healthy(vpn2_name)

        if vpn1_healthy and vpn2_healthy:
            selected = vpn1_name if vpn1_engines <= vpn2_engines else vpn2_name
        elif vpn1_healthy and not vpn2_healthy:
            selected = vpn1_name
        elif vpn2_healthy and not vpn1_healthy:
            selected = vpn2_name
        else:
            raise RuntimeError("Both VPN containers are unhealthy - cannot schedule AceStream engine")

        logger.info(f"Scheduling new engine on VPN '{selected}' (VPN1: {vpn1_engines} engines, VPN2: {vpn2_engines} engines)")
        return selected

    def _validate_vpn_health_locked(self, vpn_container: Optional[str]):
        if not vpn_container:
            return

        if not self._vpn_is_healthy(vpn_container):
            raise RuntimeError(f"VPN container '{vpn_container}' is not healthy - cannot schedule AceStream engine")

    @staticmethod
    def _vpn_is_healthy(vpn_container: str) -> bool:
        from .state import state
        from .gluetun import gluetun_monitor

        # Prefer immediate informer state when available.
        vpn_nodes = {node.get("container_name"): node for node in state.list_vpn_nodes()}
        node = vpn_nodes.get(vpn_container)
        if node and not bool(node.get("healthy")):
            return False

        health = gluetun_monitor.is_healthy(vpn_container)
        if health is True:
            return True
        if health is False:
            return _check_gluetun_health_sync(vpn_container)
        return _check_gluetun_health_sync(vpn_container)

    @staticmethod
    def _elect_forwarded_engine_locked(vpn_container: Optional[str]) -> tuple[bool, Optional[int]]:
        if not cfg.GLUETUN_CONTAINER_NAME:
            return False, None

        from .state import state
        from .gluetun import get_forwarded_port_sync

        is_forwarded = False
        p2p_port = None

        if cfg.VPN_MODE == 'redundant' and vpn_container:
            if not state.has_forwarded_engine_for_vpn(vpn_container):
                p2p_port = get_forwarded_port_sync(vpn_container)
                is_forwarded = p2p_port is not None
                if is_forwarded:
                    logger.info(f"Scheduled forwarded engine for VPN '{vpn_container}' with P2P port {p2p_port}")
        else:
            if not state.has_forwarded_engine():
                p2p_port = get_forwarded_port_sync(vpn_container)
                is_forwarded = p2p_port is not None
                if is_forwarded:
                    logger.info(f"Scheduled forwarded engine with P2P port {p2p_port}")

        return is_forwarded, p2p_port


resource_scheduler = ResourceScheduler()

def start_container(req: StartRequest) -> dict:
    from .naming import generate_container_name
    
    start_time = time.time()
    
    cli = get_client()
    key, val = cfg.CONTAINER_LABEL.split("=")
    labels = {**req.labels, key: val}
    if not req.image:
        raise ValueError("Image must be provided for container creation")
    image_name = req.image
    
    # Generate a meaningful container name
    container_name = generate_container_name(req.name_prefix)
    
    logger.debug(f"Starting container: name={container_name}, image={image_name}")
    
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
        duration = time.time() - start_time
        logger.error(f"Failed to start container {container_name}: {e} (duration: {duration:.2f}s)")
        # Provide more helpful error messages for common image issues
        error_msg = str(e).lower()
        if "not found" in error_msg or "pull access denied" in error_msg:
            raise RuntimeError(f"Image '{image_name}' not found. Please pull the image manually: docker pull {image_name}")
        elif "network" in error_msg:
            raise RuntimeError(f"Network error starting container with image '{image_name}': {e}")
        else:
            raise RuntimeError(f"Failed to start container with image '{image_name}': {e}")
    
    deadline = time.time() + cfg.STARTUP_TIMEOUT_S
    cont.reload()
    while cont.status not in ("running",) and time.time() < deadline:
        time.sleep(0.5); cont.reload()
    if cont.status != "running":
        duration = time.time() - start_time
        logger.error(f"Container {container_name} ({cont.id[:12]}) failed to start within {cfg.STARTUP_TIMEOUT_S}s (status: {cont.status}, duration: {duration:.2f}s)")
        cont.remove(force=True)
        raise RuntimeError(f"Container failed to start within {cfg.STARTUP_TIMEOUT_S}s (status: {cont.status})")
    
    # Get container name - should match what we set
    cont.reload()
    actual_container_name = cont.attrs.get("Name", "").lstrip("/")
    
    duration = time.time() - start_time
    logger.info(f"Container started successfully: {actual_container_name} ({cont.id[:12]}, duration: {duration:.2f}s)")
    
    # Check for slow provisioning (stress indicator)
    if duration > cfg.STARTUP_TIMEOUT_S * 0.5:
        logger.warning(f"Slow container provisioning detected: {duration:.2f}s (>{cfg.STARTUP_TIMEOUT_S * 0.5}s threshold) for {cont.id[:12]}")
    
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
        if labels.get(HOST_LABEL_API):
            alloc.free_host(int(labels.get(HOST_LABEL_API)))
    except Exception: pass
    try:
        cp = labels.get(ACESTREAM_LABEL_HTTP); alloc.free_http(int(cp) if cp else None)
    except Exception: pass
    try:
        sp = labels.get(ACESTREAM_LABEL_HTTPS); alloc.free_https(int(sp) if sp else None)
    except Exception: pass
    
    # Release Gluetun ports if using Gluetun.
    # HTTP and legacy API ports are both allocated from the Gluetun range.
    if cfg.GLUETUN_CONTAINER_NAME:
        try:
            hp = labels.get(HOST_LABEL_HTTP)
            vpn_container = labels.get(VPN_CONTAINER_LABEL)
            alloc.free_gluetun_port(int(hp) if hp else None, vpn_container)
        except Exception: pass
        try:
            ap = labels.get(HOST_LABEL_API)
            hp = labels.get(HOST_LABEL_HTTP)
            vpn_container = labels.get(VPN_CONTAINER_LABEL)
            # Avoid double-free if API and HTTP share the same port.
            if ap and ap != hp:
                alloc.free_gluetun_port(int(ap), vpn_container)
        except Exception: pass

def stop_container(container_id: str, force: bool = False):
    """
    Stop and remove a container.
    
    Args:
        container_id: Docker container ID or name
        force: If True, uses 'docker rm -f' to kill and remove immediately. 
               Should be used during shutdown for speed and reliability.
    """
    cli = get_client()
    try:
        cont = cli.containers.get(container_id)
        labels = cont.labels or {}
        
        # Get name for cache cleanup BEFORE removal
        c_name = cont.attrs.get("Name", "").lstrip("/")
        
        if force:
            logger.info(f"Forcibly destroying container {container_id[:12]}")
            # docker-py's remove(force=True) is equivalent to docker rm -f
            cont.remove(force=True)
        else:
            logger.info(f"Stopping container {container_id[:12]}")
            try:
                # Try graceful stop first
                cont.stop(timeout=10)
            except Exception as e:
                logger.warning(f"Graceful stop failed for {container_id[:12]}: {e}")
            
            try:
                # Remove after stopping
                cont.remove(force=True)
            except Exception as e:
                logger.error(f"Failed to remove container {container_id[:12]}: {e}")

        # Resource cleanup (ports and disk cache)
        # We do this after removal attempt to ensure we don't block removal if cleanup fails
        try:
            _release_ports_from_labels(labels)
        except Exception as e:
            logger.warning(f"Failed to release ports for {container_id}: {e}")
            
        try:
             # Use the captured name
             from .engine_cache_manager import engine_cache_manager
             engine_cache_manager.cleanup_cache(c_name)
        except Exception as e:
            logger.warning(f"Failed to cleanup cache for {container_id[:12]}: {e}")

            
    except NotFound:
        # Container already gone, nothing to do but maybe log it
        logger.debug(f"Container {container_id[:12]} already removed or not found")
    except Exception as e:
        logger.error(f"Unexpected error during stop_container for {container_id}: {e}")



def clear_acestream_cache(container_id: Optional[str] = None) -> bool:
    """Backward-compatible cache cleanup hook used by legacy tests and scripts."""
    try:
        from .engine_cache_manager import engine_cache_manager
        if container_id:
            engine_cache_manager.cleanup_cache(container_id)
        return True
    except Exception as e:
        logger.warning(f"Failed to clear AceStream cache for {container_id or 'all'}: {e}")
        return False

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


def _get_network_config(vpn_container: Optional[str] = None):
    """Get network configuration for container based on VPN setup."""
    if vpn_container:
        # Use specific VPN container's network stack
        return {
            "network_mode": f"container:{vpn_container}"
        }
    elif cfg.GLUETUN_CONTAINER_NAME:
        # Use primary Gluetun container's network stack (backwards compatibility)
        return {
            "network_mode": f"container:{cfg.GLUETUN_CONTAINER_NAME}"
        }
    elif cfg.DOCKER_NETWORK:
        # Use specified Docker network
        return {
            "network": cfg.DOCKER_NETWORK
        }
    else:
        # Detect orchestrator network if no explicit network or VPN is configured
        # This ensuring engines can reach each other and the orchestrator in "no VPN" mode
        from .docker_client import get_orchestrator_network
        orch_network = get_orchestrator_network()
        if orch_network:
            return {"network": orch_network}
            
        # Fallback to default bridge network
        return {
            "network": None
        }

def _check_gluetun_health_sync(container_name: Optional[str] = None) -> bool:
    """Synchronous version of VPN health check."""
    try:
        from ..core.config import cfg
        from .docker_client import get_client
        from docker.errors import NotFound
        
        target_container = container_name or cfg.GLUETUN_CONTAINER_NAME
        if not target_container:
            return False
        
        cli = get_client()
        container = cli.containers.get(target_container)
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

def get_variant_config(variant: str) -> dict:
    """
    Get the Docker image and base command/arguments for a specific variant.
    
    Supported standard variants:
                 - 'AceServe-amd64' (default)
                 - 'AceServe-arm32'
                 - 'AceServe-arm64'
                 - 'custom' (when custom variant is enabled)
    
    Returns:
        dict with keys:
            - image: Docker image name (always present)
            - config_type: "env" or "cmd" (always present)
            - base_args: Base arguments string (for ENV-based AceServe-amd64 variant)
            - base_cmd: Base command list (for CMD-based arm32/arm64 variants)
            - is_custom: True if this is a custom variant
    """
    # Check if custom variant is enabled and should override
    from .custom_variant_config import (
        is_custom_variant_enabled, 
        get_config, 
        build_variant_config_from_custom,
        detect_platform
    )
    
    if is_custom_variant_enabled():
        try:
            custom_config = get_config()
            if custom_config:
                logger.debug("Using custom engine variant configuration")
                return build_variant_config_from_custom(custom_config)
        except Exception as e:
            logger.error(f"Failed to load custom variant config, falling back to standard variants: {e}")
    configs = {
        "AceServe-amd64": {
            "image": "ghcr.io/krinkuto11/acestream:latest-amd64",
            "config_type": "cmd",
            "base_cmd": ["python", "main.py", "--bind-all", "--live-cache-type", "memory", "--live-mem-cache-size", "52428800", "--live-buffer", "25", "--disable-sentry", "--log-stdout", "--disable-upnp"],
        },
        "AceServe-arm32": {
            "image": "ghcr.io/krinkuto11/acestream:latest-arm32",
            "config_type": "cmd",
            "base_cmd": ["python", "main.py", "--bind-all", "--live-cache-type", "memory", "--live-mem-cache-size", "104857600", "--live-buffer", "25", "--disable-sentry", "--log-stdout", "--disable-upnp"],
        },
        "AceServe-arm64": {
            "image": "ghcr.io/krinkuto11/acestream:latest-arm64",
            "config_type": "cmd",
            "base_cmd": ["python", "main.py", "--bind-all", "--live-cache-type", "memory", "--live-mem-cache-size", "104857600", "--live-buffer", "25", "--disable-sentry", "--log-stdout", "--disable-upnp"],
        }
    }
    
    # Determine current platform to ensure compatibility
    current_platform = detect_platform()
    
    # Validation and Fallback Logic:
    # 1. If variant is not in configs at all, we MUST fallback
    # 2. If variant is "amd64" but we are on ARM, we MUST fallback
    
    needs_fallback = False
    if variant not in configs:
        needs_fallback = True
        logger.warning(f"Engine variant '{variant}' not found")
    elif current_platform in ["arm64", "arm32"] and "amd64" in variant:
        needs_fallback = True
        logger.warning(f"Engine variant '{variant}' is incompatible with platform '{current_platform}'")
        
    if needs_fallback:
        if current_platform == "arm64":
            fallback = "AceServe-arm64"
        elif current_platform == "arm32":
            fallback = "AceServe-arm32"
        else:
            fallback = "AceServe-amd64"
        
        logger.info(f"Falling back to engine variant '{fallback}' (platform: {current_platform})")
        return configs[fallback]
        
    return configs[variant]

# Alias for backward compatibility with existing tests that import _get_variant_config
# (test_engine_variants.py, demo_engine_variants.py, test_p2p_port_variants.py)
_get_variant_config = get_variant_config

def start_acestream(req: AceProvisionRequest, engine_spec: Optional[EngineSpec] = None) -> AceProvisionResponse:
    from .naming import generate_container_name
    import time
    
    provision_start = time.time()
    
    logger.debug(f"Starting AceStream engine: labels={req.labels}, custom_env={bool(req.env)}")

    # Get variant configuration
    variant_config = get_variant_config(cfg.ENGINE_VARIANT)
    
    # Determine the actual engine variant name (important for custom variants)
    from .custom_variant_config import is_custom_variant_enabled, get_config
    from .template_manager import get_active_template_name
    if is_custom_variant_enabled():
        # For custom variants, use the template name if available
        template_name = get_active_template_name()
        if template_name:
            engine_variant_name = template_name
        else:
            # Fallback to platform if no template name
            custom_config = get_config()
            if custom_config:
                engine_variant_name = f"{custom_config.platform}"
            else:
                engine_variant_name = cfg.ENGINE_VARIANT
    else:
        # Use the configured variant name
        engine_variant_name = cfg.ENGINE_VARIANT

    # Determine memory limit to apply
    # Priority: custom variant config > global env config
    memory_limit = None
    if variant_config.get("is_custom"):
        from .custom_variant_config import get_config as get_custom_config
        custom_config = get_custom_config()
        if custom_config and custom_config.memory_limit:
            memory_limit = custom_config.memory_limit
            logger.info(f"Applying custom variant memory limit: {memory_limit}")

    if not memory_limit and cfg.ENGINE_MEMORY_LIMIT:
        memory_limit = cfg.ENGINE_MEMORY_LIMIT
        logger.info(f"Applying global memory limit: {memory_limit}")

    # Resolve base volumes from variant settings before scheduling.
    volumes = None
    if variant_config.get("is_custom"):
        from .custom_variant_config import get_config as get_custom_config, DEFAULT_TORRENT_FOLDER_PATH
        custom_config = get_custom_config()
        if custom_config and custom_config.torrent_folder_mount_enabled:
            if custom_config.torrent_folder_host_path:
                container_path = custom_config.torrent_folder_container_path or DEFAULT_TORRENT_FOLDER_PATH
                for param in custom_config.parameters:
                    if param.name == "--cache-dir" and param.enabled and param.value:
                        cache_dir = param.value.replace("~", "/dev/shm")
                        container_path = f"{cache_dir}/collected_torrent_files"
                        break
                volumes = {
                    custom_config.torrent_folder_host_path: {
                        'bind': container_path,
                        'mode': 'rw'
                    }
                }
                logger.info(f"Mounting torrent folder: {custom_config.torrent_folder_host_path} -> {container_path}")
            else:
                logger.warning("Torrent folder mount enabled but host path not configured")

    # Phase 4: accept a fully resolved EngineSpec from the scheduler when provided.
    if engine_spec is None:
        engine_spec = resource_scheduler.schedule(req, engine_variant_name, base_volumes=volumes)
    elif volumes and not engine_spec.volumes:
        engine_spec.volumes = dict(volumes)

    vpn_container = engine_spec.vpn_container
    is_forwarded = engine_spec.forwarded
    p2p_port = engine_spec.p2p_port
    c_http = engine_spec.container_http_port
    c_https = engine_spec.container_https_port
    c_api = engine_spec.container_api_port
    host_http = engine_spec.host_http_port
    host_api = engine_spec.host_api_port
    labels = dict(engine_spec.labels)
    ports = engine_spec.ports
    network_config = dict(engine_spec.network_config)
    
    # Prepare environment variables and command based on variant type
    env = {**req.env}
    cmd = None
    base_cmd = variant_config.get("base_cmd", [])
    base_args = variant_config.get("base_args", "")
    
    if variant_config["config_type"] == "env":
        # Legacy ENV-based configuration (mainly for custom variants with base_args)
        base_args = variant_config.get("base_args", "")
        port_args = f" --http-port {c_http} --https-port {c_https} --api-port {c_api}"
        # Add P2P port if available
        if p2p_port:
            port_args += f" --port {p2p_port}"
        env["ACESTREAM_ARGS"] = base_args + port_args
    else:
        # CMD-based variants (krinkuto11, AceServe, and all custom variants with base_cmd)
        
        # We need to map CMD configuration differently for distinct containers
        # User requested that AceServe variants in default mode only receive --http-port and --port (P2P)
        # We check the image name to identify AceServe variants even after platform fallbacks
        image_name = variant_config.get("image", "").lower()
        is_aceserve_default = not variant_config.get("is_custom") and ("aceserve" in image_name or "krinkuto11" in image_name)
        
        if is_aceserve_default:
            # Minimal ports for AceServe as per user requirement. AceServe docker images already 
            # have --bind-all etc. in their default parameters.
            port_args = ["--http-port", str(c_http), "--api-port", str(c_api)]
        else:
            # Standard ports for other variants (including krinkuto11 and custom)
            port_args = ["--http-port", str(c_http), "--https-port", str(c_https), "--api-port", str(c_api)]
            
        # Add P2P port if available
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
            
        cmd = base_cmd + port_args

    # Generate a meaningful container name with retry logic for conflicts
    container_name = generate_container_name("acestream")

    cli = get_client()
    
    # Build container arguments, conditionally including ports
    container_args = {
        "image": variant_config["image"],
        "detach": True,
        "name": container_name,
        "environment": env,
        "labels": labels,
        **network_config,
        "restart_policy": {"Name": "unless-stopped"}
    }
    
    # Add memory limit if configured
    if memory_limit:
        container_args["mem_limit"] = memory_limit
    
    if engine_spec.volumes:
        container_args["volumes"] = dict(engine_spec.volumes)

    # Add command for CMD-based variants
    if cmd is not None:
        container_args["command"] = cmd
    
    # Only add ports if not using Gluetun (ports are handled by Gluetun container)
    if ports is not None:
        container_args["ports"] = ports
    
    # Capture base volumes before adding retry-specific cache mounts.
    base_volumes = container_args.get("volumes", {}).copy()
    max_retries = 5
    cont = None
    try:
        for attempt in range(max_retries):
            current_volumes = base_volumes.copy()
            from .engine_cache_manager import engine_cache_manager
            if engine_cache_manager.is_enabled():
                if engine_cache_manager.setup_cache(container_name):
                    cache_mount = engine_cache_manager.get_mount_config(container_name)
                    if cache_mount:
                        current_volumes.update(cache_mount)
                        logger.info(f"Mounted disk cache for {container_name}")

            if current_volumes:
                container_args["volumes"] = current_volumes
            elif "volumes" in container_args:
                del container_args["volumes"]

            try:
                cont = safe(cli.containers.run, **container_args)
                break
            except RuntimeError as e:
                if "Conflict" in str(e) and "name" in str(e).lower() and attempt < max_retries - 1:
                    old_name = container_name
                    time.sleep(0.1)
                    container_name = generate_container_name("acestream")
                    container_args["name"] = container_name
                    try:
                        if engine_cache_manager.is_enabled():
                            engine_cache_manager.cleanup_cache(old_name)
                    except Exception:
                        pass
                    continue
                raise

        if cont is None:
            raise RuntimeError("Unable to create AceStream container after retry attempts")
    except Exception:
        _release_ports_from_labels(labels)
        _decrement_vpn_pending_counter(vpn_container)
        raise

    # Do not poll for running status; lifecycle confirmation is informer-driven.
    attrs_name = str((cont.attrs or {}).get("Name", "")).lstrip("/") if getattr(cont, "attrs", None) else ""
    raw_name = attrs_name or getattr(cont, "name", "") or ""
    actual_container_name = str(raw_name).lstrip("/")
    
    duration = time.time() - provision_start
    logger.info(
        f"AceStream engine create submitted: {actual_container_name} "
        f"({cont.id[:12]}, HTTP port: {host_http}, duration: {duration:.2f}s). "
        "Running state will be confirmed via Docker events."
    )
    
    # Check for slow provisioning (stress indicator)
    if duration > cfg.STARTUP_TIMEOUT_S * 0.7:
        logger.warning(f"Slow AceStream provisioning detected: {duration:.2f}s (>{cfg.STARTUP_TIMEOUT_S * 0.7}s threshold) for {cont.id[:12]}")
    
    # Decrement pending counter now that create request has succeeded.
    _decrement_vpn_pending_counter(vpn_container)
    
    return AceProvisionResponse(
        container_id=cont.id, 
        container_name=actual_container_name,
        host_http_port=host_http, 
        container_http_port=c_http, 
        container_https_port=c_https,
        host_api_port=host_api,
        container_api_port=c_api
    )
