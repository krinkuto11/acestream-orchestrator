import time
import logging
import docker
import threading
from typing import Optional, Dict
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
HOST_LABEL_HTTP = "host.http_port"
HOST_LABEL_HTTPS = "host.https_port"
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
        cp = labels.get(ACESTREAM_LABEL_HTTP); alloc.free_http(int(cp) if cp else None)
    except Exception: pass
    try:
        sp = labels.get(ACESTREAM_LABEL_HTTPS); alloc.free_https(int(sp) if sp else None)
    except Exception: pass
    
    # Release Gluetun ports if using Gluetun
    # Only free one port per container (use HOST_LABEL_HTTP as the primary port)
    # to match the reserve behavior and avoid double-counting
    if cfg.GLUETUN_CONTAINER_NAME:
        try:
            hp = labels.get(HOST_LABEL_HTTP)
            vpn_container = labels.get(VPN_CONTAINER_LABEL)
            alloc.free_gluetun_port(int(hp) if hp else None, vpn_container)
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
        # Use default network
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

def get_variant_config(variant: str):
    """
    Get the configuration for a specific engine variant.
    
    This is a public API for retrieving variant configuration.
    Supports custom variants when enabled via custom_variant_config.
    
    Args:
        variant: The engine variant name. Valid values are:
                 - 'krinkuto11-amd64' (default)
                 - 'jopsis-amd64'
                 - 'jopsis-arm32'
                 - 'jopsis-arm64'
                 - 'custom' (when custom variant is enabled)
    
    Returns:
        dict with keys:
            - image: Docker image name (always present)
            - config_type: "env" or "cmd" (always present)
            - base_args: Base arguments string (for ENV-based jopsis-amd64 variant)
            - base_cmd: Base command list (for CMD-based arm32/arm64 variants)
            - is_custom: True if this is a custom variant
    """
    # Check if custom variant is enabled and should override
    from .custom_variant_config import is_custom_variant_enabled, get_config, build_variant_config_from_custom
    
    if is_custom_variant_enabled():
        try:
            custom_config = get_config()
            if custom_config:
                logger.info("Using custom engine variant configuration")
                return build_variant_config_from_custom(custom_config)
        except Exception as e:
            logger.error(f"Failed to load custom variant config, falling back to standard variants: {e}")
    
    configs = {
        "krinkuto11-amd64": {
            "image": "ghcr.io/krinkuto11/nano-ace:latest",
            "config_type": "cmd",
            "base_cmd": ["/acestream/acestreamengine", "--client-console", "--bind-all"]
        },
        "jopsis-amd64": {
            "image": "jopsis/acestream:x64",
            "config_type": "env",
            "base_args": "--client-console --bind-all --service-remote-access --access-token acestream --service-access-token root --stats-report-peers --live-cache-type memory --live-cache-size 209715200 --vod-cache-type memory --cache-dir /acestream/.ACEStream --vod-drop-max-age 120 --max-file-size 2147483648 --live-buffer 25 --vod-buffer 10 --max-connections 500 --max-peers 50 --max-upload-slots 50 --auto-slots 0 --download-limit 0 --upload-limit 0 --stats-report-interval 2 --stats-report-peers --slots-manager-use-cpu-limit 1 --core-skip-have-before-playback-pos 1 --core-dlr-periodic-check-interval 5 --check-live-pos-interval 5 --refill-buffer-interval 1 --webrtc-allow-outgoing-connections 1 --allow-user-config --log-debug 0 --log-max-size 15000000 --log-backup-count 1"
        },
        "jopsis-arm32": {
            "image": f"jopsis/acestream:{cfg.ENGINE_ARM32_VERSION}",
            "config_type": "cmd",
            "base_cmd": ["python", "main.py", "--bind-all", "--client-console", "--live-cache-type", "memory", "--live-mem-cache-size", "104857600", "--disable-sentry", "--log-stdout"]
        },
        "jopsis-arm64": {
            "image": f"jopsis/acestream:{cfg.ENGINE_ARM64_VERSION}",
            "config_type": "cmd",
            "base_cmd": ["python", "main.py", "--bind-all", "--client-console", "--live-cache-type", "memory", "--live-mem-cache-size", "104857600", "--disable-sentry", "--log-stdout"]
        }
    }
    return configs.get(variant, configs["krinkuto11-amd64"])

# Alias for backward compatibility with existing tests that import _get_variant_config
# (test_engine_variants.py, demo_engine_variants.py, test_p2p_port_variants.py)
_get_variant_config = get_variant_config

def start_acestream(req: AceProvisionRequest) -> AceProvisionResponse:
    from .naming import generate_container_name
    import time
    
    provision_start = time.time()
    
    logger.debug(f"Starting AceStream engine: labels={req.labels}, custom_env={bool(req.env)}")
    
    # Determine VPN assignment and check health
    # Use lock to prevent race conditions during concurrent provisioning in redundant mode
    vpn_container = None
    if cfg.GLUETUN_CONTAINER_NAME:
        from .gluetun import gluetun_monitor
        from .state import state
        
        # In redundant mode, assign engine to VPN with round-robin load balancing
        # Lock ensures VPN selection and pending counter update are atomic
        with _vpn_assignment_lock:
            if cfg.VPN_MODE == 'redundant' and cfg.GLUETUN_CONTAINER_NAME_2:
                # Check if in VPN recovery mode - force assign to recovery target
                recovery_target = state.get_vpn_recovery_target()
                if recovery_target:
                    vpn_container = recovery_target
                    logger.info(f"VPN recovery mode active: assigning engine to recovery target VPN '{vpn_container}'")
                # Check if in emergency mode - only assign to healthy VPN
                elif state.is_emergency_mode():
                    emergency_info = state.get_emergency_mode_info()
                    vpn_container = emergency_info['healthy_vpn']
                    logger.info(f"Emergency mode active: assigning engine to healthy VPN '{vpn_container}'")
                else:
                    # Normal redundant mode: Count engines per VPN (including pending ones being provisioned)
                    vpn1_name = cfg.GLUETUN_CONTAINER_NAME
                    vpn2_name = cfg.GLUETUN_CONTAINER_NAME_2
                    
                    # Count engines already in state
                    vpn1_engines = len(state.get_engines_by_vpn(vpn1_name))
                    vpn2_engines = len(state.get_engines_by_vpn(vpn2_name))
                    
                    # Add pending engines currently being provisioned
                    vpn1_pending = _vpn_pending_engines.get(vpn1_name, 0)
                    vpn2_pending = _vpn_pending_engines.get(vpn2_name, 0)
                    vpn1_engines += vpn1_pending
                    vpn2_engines += vpn2_pending
                    
                    # Check health of both VPNs
                    vpn1_healthy = gluetun_monitor.is_healthy(vpn1_name)
                    vpn2_healthy = gluetun_monitor.is_healthy(vpn2_name)
                    
                    # Determine VPN assignment based on health and load
                    if vpn1_healthy and vpn2_healthy:
                        # Both healthy: use round-robin to balance load
                        vpn_container = vpn1_name if vpn1_engines <= vpn2_engines else vpn2_name
                    elif vpn1_healthy and not vpn2_healthy:
                        # Only VPN1 healthy: use it
                        vpn_container = vpn1_name
                    elif vpn2_healthy and not vpn1_healthy:
                        # Only VPN2 healthy: use it
                        vpn_container = vpn2_name
                    else:
                        # Both unhealthy: fail provisioning
                        raise RuntimeError("Both VPN containers are unhealthy - cannot start AceStream engine")
                    
                    logger.info(f"Assigning new engine to VPN '{vpn_container}' (VPN1: {vpn1_engines} engines, VPN2: {vpn2_engines} engines)")
                
                # Atomically increment pending counter for selected VPN (applies to all modes)
                _vpn_pending_engines[vpn_container] = _vpn_pending_engines.get(vpn_container, 0) + 1
            else:
                # Single VPN mode
                vpn_container = cfg.GLUETUN_CONTAINER_NAME
        
        # Check if assigned VPN is healthy
        try:
            timeout = 5
            start_time = time.time()
            
            while (time.time() - start_time) < timeout:
                current_health = gluetun_monitor.is_healthy(vpn_container)
                if current_health is True:
                    break
                elif current_health is False:
                    # Force a fresh health check
                    import asyncio
                    try:
                        if _check_gluetun_health_sync(vpn_container):
                            break
                    except Exception:
                        pass
                
                time.sleep(0.5)
            else:
                # Timeout reached without becoming healthy
                raise RuntimeError(f"VPN container '{vpn_container}' is not healthy - cannot start AceStream engine")
                
        except Exception as e:
            raise RuntimeError(f"Failed to verify VPN health for '{vpn_container}': {e}")
    
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
            alloc.reserve_gluetun_port(c_http, vpn_container)
        else:
            alloc.reserve_http(c_http)
    else:
        # No user http port - use orchestrator allocation
        if cfg.GLUETUN_CONTAINER_NAME:
            # When using Gluetun, allocate from the VPN-specific port range
            host_http = alloc.alloc_gluetun_port(vpn_container)
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
        # HTTPS ports always use the regular HTTPS range, not Gluetun ports
        # HTTPS ports don't count against MAX_REPLICAS
        alloc.reserve_https(c_https)
    else:
        # No user https port - use orchestrator allocation
        # HTTPS ports always use the regular HTTPS range, regardless of Gluetun
        # HTTPS ports don't count against MAX_REPLICAS
        c_https = alloc.alloc_https(avoid=c_http)

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
    
    # Determine if this engine should be the forwarded engine
    # Only one engine should have the forwarded port per VPN when using Gluetun
    is_forwarded = False
    p2p_port = None
    if cfg.GLUETUN_CONTAINER_NAME:
        from .state import state
        
        # In redundant mode, check if this VPN already has a forwarded engine
        # In single mode, check if any forwarded engine exists
        if cfg.VPN_MODE == 'redundant' and vpn_container:
            if not state.has_forwarded_engine_for_vpn(vpn_container):
                # This VPN doesn't have a forwarded engine yet
                is_forwarded = True
                from .gluetun import get_forwarded_port_sync
                p2p_port = get_forwarded_port_sync(vpn_container)
                if p2p_port:
                    logger.info(f"Provisioning new forwarded engine for VPN '{vpn_container}' with P2P port {p2p_port}")
                else:
                    logger.warning(f"VPN '{vpn_container}' has no forwarded port available, provisioning non-forwarded engine")
                    is_forwarded = False
            else:
                logger.info(f"Forwarded engine already exists for VPN '{vpn_container}', provisioning non-forwarded engine")
        else:
            # Single VPN mode - only one forwarded engine total
            if not state.has_forwarded_engine():
                # This will be the forwarded engine
                is_forwarded = True
                from .gluetun import get_forwarded_port_sync
                p2p_port = get_forwarded_port_sync(vpn_container)
                if p2p_port:
                    logger.info(f"Provisioning new forwarded engine with P2P port {p2p_port}")
                else:
                    logger.warning("No forwarded port available, provisioning non-forwarded engine")
                    is_forwarded = False
            else:
                logger.info("Forwarded engine already exists, provisioning non-forwarded engine")
    
    # Prepare environment variables and command based on variant type
    env = {**req.env}
    cmd = None
    
    if variant_config["config_type"] == "env":
        # ENV-based variants (jopsis-amd64 only)
        # Note: Custom amd64 variants now use CMD-based configuration with Nano-Ace
        # Legacy custom variants with base_args would still use this path
        uses_acestream_args = (
            cfg.ENGINE_VARIANT == "jopsis-amd64" or 
            (variant_config.get("is_custom") and variant_config.get("base_args") is not None)
        )
        
        if uses_acestream_args:
            # Use ACESTREAM_ARGS environment variable with base args + port settings
            # This applies to jopsis-amd64 and custom variants with base_args
            base_args = variant_config.get("base_args", "")
            port_args = f" --http-port {c_http} --https-port {c_https}"
            # Add P2P port if available
            if p2p_port:
                port_args += f" --port {p2p_port}"
            env["ACESTREAM_ARGS"] = base_args + port_args
    else:
        # CMD-based variants (krinkuto11-amd64, jopsis-arm32, jopsis-arm64, custom variants with base_cmd)
        # Append port settings to base command
        base_cmd = variant_config.get("base_cmd", [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        # Add P2P port if available
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
        cmd = base_cmd + port_args

    key, val = cfg.CONTAINER_LABEL.split("=")
    labels = {**req.labels, key: val,
              ACESTREAM_LABEL_HTTP: str(c_http),
              ACESTREAM_LABEL_HTTPS: str(c_https),
              HOST_LABEL_HTTP: str(host_http),
              ENGINE_VARIANT_LABEL: engine_variant_name}
    
    # Add VPN container label if using VPN
    if vpn_container:
        labels[VPN_CONTAINER_LABEL] = vpn_container
    
    # Add forwarded label if this is the forwarded engine
    if is_forwarded:
        labels[FORWARDED_LABEL] = "true"

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

    # Determine network configuration based on VPN setup
    network_config = _get_network_config(vpn_container)

    cli = get_client()
    
    # Determine memory limit to apply
    # Priority: custom variant config > global env config
    memory_limit = None
    if variant_config.get("is_custom"):
        # Check if custom variant has memory limit configured
        from .custom_variant_config import get_config as get_custom_config
        custom_config = get_custom_config()
        if custom_config and custom_config.memory_limit:
            memory_limit = custom_config.memory_limit
            logger.info(f"Applying custom variant memory limit: {memory_limit}")
    
    # Fall back to global config if no custom limit
    if not memory_limit and cfg.ENGINE_MEMORY_LIMIT:
        memory_limit = cfg.ENGINE_MEMORY_LIMIT
        logger.info(f"Applying global memory limit: {memory_limit}")
    
    # Configure volume mounts for custom variants
    volumes = None
    if variant_config.get("is_custom"):
        from .custom_variant_config import get_config as get_custom_config, DEFAULT_TORRENT_FOLDER_PATH
        custom_config = get_custom_config()
        
        # Check if torrent folder mount is enabled
        if custom_config and custom_config.torrent_folder_mount_enabled:
            if custom_config.torrent_folder_host_path:
                # Get the container path - use user-specified if set, otherwise use default
                container_path = custom_config.torrent_folder_container_path or DEFAULT_TORRENT_FOLDER_PATH
                
                # Check if user has overridden the cache-dir parameter
                # If they have, we should use that path + /collected_torrent_files
                # Note: ~ is expanded to /root because AceStream runs as root in the container
                for param in custom_config.parameters:
                    if param.name == "--cache-dir" and param.enabled and param.value:
                        cache_dir = param.value.replace("~", "/root")  # AceStream runs as root
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
    
    # Add volumes if configured
    if volumes:
        container_args["volumes"] = volumes
    
    # Add command for CMD-based variants
    if cmd is not None:
        container_args["command"] = cmd
    
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
        duration = time.time() - provision_start
        logger.error(f"AceStream engine {cont.id[:12]} failed to start (status: {cont.status}, duration: {duration:.2f}s)")
        _release_ports_from_labels(labels)
        cont.remove(force=True)
        raise RuntimeError("Arranque AceStream fallido")

    
    # Get container name - should match what we set
    cont.reload()
    actual_container_name = cont.attrs.get("Name", "").lstrip("/")
    
    duration = time.time() - provision_start
    logger.info(f"AceStream engine started successfully: {actual_container_name} ({cont.id[:12]}, HTTP port: {host_http}, duration: {duration:.2f}s)")
    
    # Check for slow provisioning (stress indicator)
    if duration > cfg.STARTUP_TIMEOUT_S * 0.7:
        logger.warning(f"Slow AceStream provisioning detected: {duration:.2f}s (>{cfg.STARTUP_TIMEOUT_S * 0.7}s threshold) for {cont.id[:12]}")
    
    # Add engine to state immediately to prevent race conditions during sequential provisioning
    # This ensures that subsequent calls to has_forwarded_engine() will see this engine
    from .state import state
    from ..models.schemas import EngineState
    from ..models.db_models import EngineRow
    from .db import SessionLocal
    
    # Determine host based on VPN configuration
    if vpn_container:
        # Use the assigned VPN container as the host
        engine_host = vpn_container
    elif cfg.GLUETUN_CONTAINER_NAME:
        # Backwards compatibility for single VPN mode
        engine_host = cfg.GLUETUN_CONTAINER_NAME
    else:
        engine_host = actual_container_name or "127.0.0.1"
    
    # Create engine state immediately
    now = state.now()
    engine = EngineState(
        container_id=cont.id,
        container_name=actual_container_name,
        host=engine_host,
        port=host_http,
        labels=labels,
        forwarded=is_forwarded,
        first_seen=now,
        last_seen=now,
        streams=[],
        health_status="unknown",
        last_health_check=None,
        last_stream_usage=None,
        vpn_container=vpn_container,
        engine_variant=engine_variant_name
    )
    
    # Add to in-memory state
    state.engines[cont.id] = engine
    
    # Decrement pending counter now that engine is in state (in redundant VPN mode)
    _decrement_vpn_pending_counter(vpn_container)
    
    # Persist to database
    try:
        with SessionLocal() as s:
            s.merge(EngineRow(
                engine_key=cont.id,
                container_id=cont.id,
                container_name=actual_container_name,
                host=engine_host,
                port=host_http,
                labels=labels,
                forwarded=is_forwarded,
                first_seen=now,
                last_seen=now,
                vpn_container=vpn_container
            ))
            s.commit()
    except Exception as e:
        # Log but don't fail provisioning if database write fails
        logger.warning(f"Failed to persist engine to database: {e}")
    
    # Mark this engine as forwarded in state if it was designated as such
    # This must be done AFTER adding to state so set_forwarded_engine can find it
    if is_forwarded:
        state.set_forwarded_engine(cont.id)
        logger.info(f"Engine {cont.id[:12]} provisioned as forwarded engine")
    
    return AceProvisionResponse(
        container_id=cont.id, 
        container_name=actual_container_name,
        host_http_port=host_http, 
        container_http_port=c_http, 
        container_https_port=c_https
    )
