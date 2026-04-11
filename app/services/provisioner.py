import time
import logging
import docker
import httpx
from docker.errors import NotFound
import threading
import hashlib
import json
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
    if not vpn_container:
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
ENGINE_CONFIG_HASH_LABEL = "acestream.config_hash"
ENGINE_CONFIG_GENERATION_LABEL = "acestream.config_generation"

PORT_FORWARDING_NATIVE_PROVIDERS = {
    "private internet access",
    "perfect privacy",
    "privatevpn",
    "protonvpn",
}

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
    # Resource Identifiers
    vpn_container_id: Optional[str] = None
    container_name: str
    image: str
    
    # Runtime Configuration
    command: list[str] = []
    env_vars: Dict[str, str] = {}
    labels: Dict[str, str] = {}
    
    # Network Configuration
    network_mode: str = "bridge"
    ports: Optional[Dict[str, int]] = None  # { container_port/protocol: host_port }
    
    # Hardware Constraints
    mem_limit: Optional[str] = None
    
    # Legacy/Metadata fields for internal tracking
    host_http_port: int
    container_http_port: int
    host_api_port: int
    container_api_port: int
    forwarded: bool = False
    p2p_port: Optional[int] = None
    volumes: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ResourceScheduler:
    """Atomically resolves VPN node, forwarding role, and port reservations for new engines."""

    def __init__(self):
        self._lock = _vpn_assignment_lock

    def schedule_new_engine(self, extra_reserved_names: list[str] = None) -> Optional[EngineSpec]:
        """
        The Brain: Resolve all resources needed for a new engine.
        Returns a complete, immutable EngineSpec that can be executed as an intent.
        """
        from .naming import generate_container_name
        from .engine_config import (
            EngineConfig,
            build_engine_customization_args,
            detect_platform,
            get_config as get_engine_config,
            resolve_engine_image,
        )
        
        # 1. Resolve Global Configuration
        engine_platform = detect_platform()
        engine_image = resolve_engine_image(engine_platform)
        engine_variant_name = f"global-{engine_platform}"
        engine_config = get_engine_config() or EngineConfig()
        
        # 2. Memory Limits
        mem_limit = engine_config.memory_limit or cfg.ENGINE_MEMORY_LIMIT
        
        # 3. Schedule Resources (VPN and Ports)
        # For simplicity in this declarative refactor, we mock a request
        req = AceProvisionRequest() 
        spec_base = self.schedule(req, engine_variant_name)
        
        # 4. Finalize Spec Details
        container_name = generate_container_name("acestream", extra_exclude=extra_reserved_names)
        
        # Prepare command
        cmd = [
            "python", "main.py",
            "--http-port", str(spec_base.container_http_port),
            "--api-port", str(spec_base.container_api_port),
        ]
        if spec_base.p2p_port:
            cmd.extend(["--port", str(spec_base.p2p_port)])
        
        cmd.extend(build_engine_customization_args(engine_config))

        # Build final intent
        return EngineSpec(
            vpn_container_id=spec_base.vpn_container_id, # This is mapped in schedule() refactor below
            container_name=container_name,
            image=engine_image,
            command=cmd,
            env_vars=req.env,
            labels=spec_base.labels,
            network_mode=spec_base.network_mode,
            ports=spec_base.ports,
            mem_limit=mem_limit,
            host_http_port=spec_base.host_http_port,
            container_http_port=spec_base.container_http_port,
            host_api_port=spec_base.host_api_port,
            container_api_port=spec_base.container_api_port,
            forwarded=spec_base.forwarded,
            p2p_port=spec_base.p2p_port,
            volumes=spec_base.volumes
        )

    @staticmethod
    def release_resources(spec: EngineSpec):
        """Rollback port allocations if an intent execution fails."""
        try:
            _release_ports_from_labels(spec.labels)
            _decrement_vpn_pending_counter(spec.vpn_container_id)
        except Exception as e:
            logger.warning(f"Resource cleanup failed during rollback for {spec.container_name}: {e}")

    def schedule(self, req: "AceProvisionRequest", engine_variant_name: str, base_volumes: Optional[Dict[str, Any]] = None) -> EngineSpec:
        from .state import state

        user_conf = req.env.get("CONF")
        user_http_port = _parse_conf_port(user_conf, "http") if user_conf else None
        user_https_port = _parse_conf_port(user_conf, "https") if user_conf else None
        user_api_port = _parse_conf_port(user_conf, "api") if user_conf else None

        vpn_container = None
        pending_incremented = False

        try:
            with self._lock:
                require_forwarding_capable = self._should_prefer_forwarding_capable_node_locked()
                vpn_container = self._select_vpn_container_locked(require_forwarding_capable=require_forwarding_capable)
                if vpn_container:
                    _vpn_pending_engines[vpn_container] = _vpn_pending_engines.get(vpn_container, 0) + 1
                    pending_incremented = True

                self._validate_vpn_health_locked(vpn_container)

                ports_info = alloc.allocate_engine_ports(
                    use_gluetun=bool(vpn_container),
                    vpn_container=vpn_container,
                    requested_host_port=req.host_port,
                    user_http_port=user_http_port,
                    user_https_port=user_https_port,
                    user_api_port=user_api_port,
                    map_https=bool(cfg.ACE_MAP_HTTPS),
                )

                forwarded, p2p_port = self._elect_forwarded_engine_locked(vpn_container)

                key, val = cfg.CONTAINER_LABEL.split("=", 1)
                target_config = state.get_target_engine_config()
                target_hash = str(target_config.get("config_hash") or "").strip()
                if not target_hash:
                    target_hash = compute_current_engine_config_hash()
                    target_config = state.set_target_engine_config(target_hash)

                labels = {
                    **req.labels,
                    key: val,
                    ACESTREAM_LABEL_HTTP: str(ports_info["container_http_port"]),
                    ACESTREAM_LABEL_HTTPS: str(ports_info["container_https_port"]),
                    ACESTREAM_LABEL_API: str(ports_info["container_api_port"]),
                    HOST_LABEL_HTTP: str(ports_info["host_http_port"]),
                    HOST_LABEL_API: str(ports_info["host_api_port"]),
                    ENGINE_VARIANT_LABEL: engine_variant_name,
                    ENGINE_CONFIG_HASH_LABEL: target_hash,
                    ENGINE_CONFIG_GENERATION_LABEL: str(int(target_config.get("generation") or 0)),
                }

                if vpn_container:
                    labels[VPN_CONTAINER_LABEL] = vpn_container
                if forwarded:
                    labels[FORWARDED_LABEL] = "true"
                if ports_info.get("host_https_port") is not None:
                    labels[HOST_LABEL_HTTPS] = str(ports_info["host_https_port"])

                docker_ports = None
                if not vpn_container:
                    docker_ports = {
                        f"{ports_info['container_http_port']}/tcp": ports_info["host_http_port"],
                        f"{ports_info['container_api_port']}/tcp": ports_info["host_api_port"],
                    }
                    if ports_info.get("host_https_port") is not None:
                        docker_ports[f"{ports_info['container_https_port']}/tcp"] = ports_info["host_https_port"]

                return EngineSpec(
                    vpn_container_id=vpn_container,
                    container_name="", # Resolved later in schedule_new_engine
                    image="", # Resolved later
                    forwarded=forwarded,
                    p2p_port=p2p_port,
                    host_http_port=ports_info["host_http_port"],
                    container_http_port=ports_info["container_http_port"],
                    host_api_port=ports_info["host_api_port"],
                    container_api_port=ports_info["container_api_port"],
                    labels=labels,
                    ports=docker_ports,
                    volumes=dict(base_volumes or {}),
                    network_mode=_get_network_config(vpn_container).get("network_mode", "bridge"),
                )
        except Exception:
            if pending_incremented:
                _decrement_vpn_pending_counter(vpn_container)
            raise

    def _select_vpn_container_locked(self, *, require_forwarding_capable: bool = False) -> Optional[str]:
        from .state import state
        from .settings_persistence import SettingsPersistence

        vpn_settings = SettingsPersistence.load_vpn_config() or {}
        vpn_enabled = bool(vpn_settings.get("enabled", False))
        if not vpn_enabled:
            return None

        rejection_reasons: list[str] = []
        dynamic_ready_nodes: list[Dict[str, object]] = []
        for node in state.list_vpn_nodes():
            if not bool(node.get("managed_dynamic")):
                continue
            
            is_ready, reason = self._is_dynamic_node_ready_with_reason(node)
            if not is_ready:
                rejection_reasons.append(f"{node.get('container_name')}: {reason}")
                continue
                
            if state.is_vpn_node_draining(str(node.get("container_name") or "")):
                rejection_reasons.append(f"{node.get('container_name')}: draining")
                continue
                
            dynamic_ready_nodes.append(node)

        if not dynamic_ready_nodes:
            diag = "; ".join(rejection_reasons) if rejection_reasons else "none found"
            raise RuntimeError(f"No healthy active dynamic VPN nodes available (Found: {diag}) - cannot schedule AceStream engine")

        candidate_nodes = dynamic_ready_nodes
        if require_forwarding_capable:
            forwarding_capable_nodes = [
                node for node in dynamic_ready_nodes if self._node_supports_port_forwarding(node)
            ]
            if forwarding_capable_nodes:
                candidate_nodes = forwarding_capable_nodes
            else:
                logger.warning(
                    "P2P forwarding requested but no dynamic VPN nodes support port forwarding; "
                    "falling back to standard node routing"
                )

        selected = min(
            candidate_nodes,
            key=lambda node: len(state.get_engines_by_vpn(str(node.get("container_name") or "")))
            + _vpn_pending_engines.get(str(node.get("container_name") or ""), 0),
        )
        selected_name = str(selected.get("container_name") or "").strip()
        if not selected_name:
            raise RuntimeError("Selected dynamic VPN node has no container name")

        logger.info(f"Scheduling new engine on dynamic VPN '{selected_name}'")
        return selected_name

    @staticmethod
    def _node_supports_port_forwarding(node: Dict[str, object]) -> bool:
        supported_flag = node.get("port_forwarding_supported")
        if isinstance(supported_flag, bool):
            return supported_flag

        provider = str(node.get("provider") or "").strip().lower()
        if provider:
            return provider in PORT_FORWARDING_NATIVE_PROVIDERS
        return False

    def _is_dynamic_node_ready(self, node: Dict[str, object]) -> bool:
        is_ready, _ = self._is_dynamic_node_ready_with_reason(node)
        return is_ready

    def _is_dynamic_node_ready_with_reason(self, node: Dict[str, object]) -> tuple[bool, str]:
        container_name = str(node.get("container_name") or "").strip()
        if not container_name:
            return False, "no_name"

        condition = str(node.get("condition", "")).strip().lower()
        if condition:
            if condition != "ready":
                return False, f"condition_{condition}"
        elif not bool(node.get("healthy")):
            # Heuristic: If we already have healthy engines on this node, it IS ready
            # regardless of what the passive monitor thinks (it might be stale).
            from .state import state
            engines = state.get_engines_by_vpn(container_name)
            if any(getattr(e, "health_status", "") == "healthy" for e in engines):
                return True, "ready_via_heuristic"
            return False, "not_healthy"

        # Docker "running" can precede Gluetun control API readiness by a few seconds.
        # Require control API reachability and 'connected' VPN status before
        # scheduling engines on dynamic nodes.
        status = str(node.get("status") or "").strip().lower()
        if status == "running":
            is_reachable = ResourceScheduler._is_vpn_control_api_reachable(container_name, require_connected=True)
            if not is_reachable:
                # Secondary Heuristic: Even if API is not reachable, if engines are healthy, we are good.
                from .state import state
                engines = state.get_engines_by_vpn(container_name)
                if any(getattr(e, "health_status", "") == "healthy" for e in engines):
                    return True, "ready_via_heuristic_api_down"
                return False, "api_unreachable/not_connected"

        return True, "ready"

    @staticmethod
    def _is_vpn_control_api_reachable(vpn_container: str, require_connected: bool = False) -> bool:
        try:
            from .gluetun import _is_control_server_reachable_sync
            return _is_control_server_reachable_sync(vpn_container, require_connected=require_connected)
        except Exception as exc:
            logger.debug("Dynamic VPN '%s' control API not reachable yet: %s", vpn_container, exc)
            return False

    def _should_prefer_forwarding_capable_node_locked(self) -> bool:
        from .state import state
        from .settings_persistence import SettingsPersistence

        vpn_settings = SettingsPersistence.load_vpn_config() or {}
        if not bool(vpn_settings.get("enabled", False)):
            return False

        ready_dynamic_nodes = [
            node for node in state.list_vpn_nodes()
            if bool(node.get("managed_dynamic")) and self._is_dynamic_node_ready(node)
        ]
        forwarding_capable = [node for node in ready_dynamic_nodes if self._node_supports_port_forwarding(node)]
        if not forwarding_capable:
            if ready_dynamic_nodes:
                logger.warning(
                    "No forwarding-capable dynamic VPN nodes are available; forwarded engine election will degrade "
                    "to non-forwarded placement"
                )
            return False

        forwarding_capable_names = {
            str(node.get("container_name") or "").strip()
            for node in forwarding_capable
            if str(node.get("container_name") or "").strip()
        }
        if not forwarding_capable_names:
            return False

        for engine in state.list_engines():
            if engine.forwarded and engine.vpn_container in forwarding_capable_names:
                return False

        return True

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
        if not vpn_container:
            return False, None

        from .state import state
        from .gluetun import get_forwarded_port_sync

        is_forwarded = False
        p2p_port = None

        if vpn_container:
            if not ResourceScheduler._vpn_supports_port_forwarding(vpn_container):
                logger.warning(
                    "VPN '%s' does not support port forwarding; engine will be scheduled without forwarded P2P port",
                    vpn_container,
                )
                return False, None

            if not state.has_forwarded_engine_for_vpn(vpn_container):
                from .gluetun import wait_for_port_sync
                p2p_port = wait_for_port_sync(vpn_container)
                is_forwarded = p2p_port is not None and p2p_port > 0
                if is_forwarded:
                    logger.info(f"Scheduled forwarded engine for VPN '{vpn_container}' with P2P port {p2p_port}")
        else:
            if not state.has_forwarded_engine():
                from .gluetun import wait_for_port_sync
                p2p_port = wait_for_port_sync(vpn_container)
                is_forwarded = p2p_port is not None and p2p_port > 0
                if is_forwarded:
                    logger.info(f"Scheduled forwarded engine with P2P port {p2p_port}")

        return is_forwarded, p2p_port

    @staticmethod
    def _vpn_supports_port_forwarding(vpn_container: str) -> bool:
        from .state import state

        for node in state.list_vpn_nodes():
            node_name = str(node.get("container_name") or "").strip()
            if node_name != vpn_container:
                continue

            if bool(node.get("managed_dynamic")):
                return ResourceScheduler._node_supports_port_forwarding(node)

            provider = str(node.get("provider") or "").strip().lower()
            if provider:
                return provider in PORT_FORWARDING_NATIVE_PROVIDERS
            return True

        return True


resource_scheduler = ResourceScheduler()


def execute_engine_spec(spec: EngineSpec):
    """
    Dumb Executor: Blindly trusts the EngineSpec and fires the Docker run command.
    Returns the created container object.
    """
    cli = get_client()

    # 1. Map Network
    network_mode = spec.network_mode
    if spec.vpn_container_id:
        network_mode = f"container:{spec.vpn_container_id}"

    # 2. Setup Disk Cache
    container_name = spec.container_name
    final_volumes = spec.volumes.copy()
    try:
        from .engine_cache_manager import engine_cache_manager
        if engine_cache_manager.is_enabled():
            if engine_cache_manager.setup_cache(container_name):
                cache_mount = engine_cache_manager.get_mount_config(container_name)
                if cache_mount:
                    final_volumes.update(cache_mount)
                    logger.info(f"Mounted disk cache for {container_name}")
    except Exception as e:
        logger.warning(f"Failed to setup engine cache for {container_name}: {e}")

    # 3. Execute Fire-and-Forget
    try:
        logger.info(f"Executing creation intent for {container_name} on target {network_mode}")
        container = cli.containers.run(
            image=spec.image,
            name=container_name,
            command=spec.command,
            environment=spec.env_vars,
            labels=spec.labels,
            network_mode=network_mode,
            ports=spec.ports,
            mem_limit=spec.mem_limit,
            volumes=final_volumes,
            detach=True,
            remove=True,  # Auto-cleanup on exit
            restart_policy={"Name": "on-failure", "MaximumRetryCount": 3} if not spec.vpn_container_id else None
        )
        # Success at the API level (request submitted)
        _decrement_vpn_pending_counter(spec.vpn_container_id)
        return container
    except Exception as e:
        logger.error(f"Docker API failed to provision {container_name}: {e}")
        # Rollback atomic locks if creation totally failed
        ResourceScheduler.release_resources(spec)
        raise
    finally:
        try:
            cli.close()
        except:
            pass

def compute_current_engine_config_hash() -> str:
    """Compute a stable hash for the desired engine runtime configuration."""
    from .engine_config import get_config as get_engine_config

    engine_config = None
    cfg_obj = get_engine_config()
    if cfg_obj:
        engine_config = cfg_obj.model_dump(mode="json")

    payload = {
        "engine_config": engine_config,
        "engine_memory_limit": cfg.ENGINE_MEMORY_LIMIT,
        "ace_map_https": cfg.ACE_MAP_HTTPS,
        "docker_network": cfg.DOCKER_NETWORK,
        "dynamic_vpn_management": cfg.DYNAMIC_VPN_MANAGEMENT,
        "vpn_provider": cfg.VPN_PROVIDER,
        "vpn_protocol": cfg.VPN_PROTOCOL,
    }

    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

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

        # Do not poll for running status; lifecycle confirmation is informer-driven.
        attrs_name = str((cont.attrs or {}).get("Name", "")).lstrip("/") if getattr(cont, "attrs", None) else ""
        raw_name = attrs_name or getattr(cont, "name", "") or ""
        actual_container_name = str(raw_name).lstrip("/")

        duration = time.time() - start_time
        logger.info(
            f"Container create submitted: {actual_container_name} ({cont.id[:12]}, duration: {duration:.2f}s). "
            "Running state will be confirmed via Docker events."
        )

        # Check for slow provisioning (stress indicator)
        if duration > cfg.STARTUP_TIMEOUT_S * 0.5:
            logger.warning(f"Slow container provisioning detected: {duration:.2f}s (>{cfg.STARTUP_TIMEOUT_S * 0.5}s threshold) for {cont.id[:12]}")

        return {"container_id": cont.id, "container_name": actual_container_name}
    finally:
        try:
            cli.close()
        except Exception:
            pass

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
    
    # Release dynamic VPN-mapped ports only when the engine was bound to a VPN node.
    vpn_container = labels.get(VPN_CONTAINER_LABEL)
    if vpn_container:
        try:
            hp = labels.get(HOST_LABEL_HTTP)
            alloc.free_gluetun_port(int(hp) if hp else None, vpn_container)
        except Exception: pass
        try:
            ap = labels.get(HOST_LABEL_API)
            hp = labels.get(HOST_LABEL_HTTP)
            # Avoid double-free if API and HTTP share the same port.
            if ap and ap != hp:
                alloc.free_gluetun_port(int(ap), vpn_container)
        except Exception: pass

def stop_container(container_id: str, force: bool = False):
    """
    Dumb Terminator: Sends the stop signal and returns.
    All state removal and port cleanup is handled by the DockerEventWatcher.
    """
    cli = get_client()
    try:
        container = cli.containers.get(container_id)
        logger.info(f"Executing {'forced ' if force else ''}termination intent for {container_id[:12]}")
        
        if force:
            # Immediate SIGKILL for forced cleanup
            container.kill()
        else:
            # Graceful stop with 5s timeout before SIGKILL
            container.stop(timeout=5)
        
        # Note: container.remove() is handled by remove=True in the execute_engine_spec run() call.
        # If it's still there, we could force rm it, but normally stop is enough.
    except NotFound:
        logger.debug(f"Container {container_id[:12]} already dead or gone")
    except Exception as e:
        logger.warning(f"Container {container_id[:12]} could not be stopped (may already be dead): {e}")
    finally:
        try:
            cli.close()
        except:
            pass



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
        from .docker_client import get_client
        from docker.errors import NotFound
        
        target_container = container_name
        if not target_container:
            return False
        
        cli = get_client()
        try:
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
        finally:
            try:
                cli.close()
            except Exception:
                pass
            
    except NotFound:
        return False
    except Exception:
        return False


def get_variant_config(variant: str = "global") -> dict:
    """Backward-compatible adapter for legacy tests and tooling."""
    from .engine_config import (
        EngineConfig,
        build_engine_customization_args,
        detect_platform,
        get_config as get_engine_config,
        resolve_engine_image,
    )

    engine_config = get_engine_config() or EngineConfig()
    platform_arch = detect_platform()
    return {
        "image": resolve_engine_image(platform_arch),
        "config_type": "cmd",
        "is_custom": True,
        "requested_variant": variant,
        "base_cmd": ["python", "main.py", *build_engine_customization_args(engine_config)],
    }


_get_variant_config = get_variant_config

def start_acestream(req: AceProvisionRequest) -> AceProvisionResponse:
    """
    Thin wrapper around the new intent-based logic for backward compatibility with API.
    """
    spec = resource_scheduler.schedule_new_engine(req)
    container = execute_engine_spec(spec)
    
    return AceProvisionResponse(
        container_id=container.id,
        container_name=spec.container_name,
        host_http_port=spec.host_http_port,
        container_http_port=spec.container_http_port,
        container_https_port=spec.container_https_port,
        host_api_port=spec.host_api_port,
        container_api_port=spec.container_api_port
    )
