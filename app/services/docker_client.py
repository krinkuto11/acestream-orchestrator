import docker
from docker.errors import APIError, DockerException
import time
import logging
import socket
import os
import threading
from contextlib import suppress
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Cache for detected orchestrator network
_orchestrator_network = None

_DOCKER_EVENT_FILTERS = {
    "type": "container",
    "event": [
        "start",
        "die",
        "destroy",
        "health_status: healthy",
        "health_status: unhealthy",
    ],
}


def _resolve_docker_socket_path() -> Optional[str]:
    docker_host = os.getenv("DOCKER_HOST", "").strip()
    if docker_host.startswith("unix://"):
        return docker_host.replace("unix://", "", 1)
    if docker_host:
        # Non-unix transports (tcp/ssh/etc.) should keep retry behavior.
        return None
    return "/var/run/docker.sock"


def _normalize_docker_event_action(event: dict) -> str:
    action = (
        event.get("Action")
        or event.get("status")
        or event.get("action")
        or ""
    )
    return str(action).strip().lower()


class DockerEventWatcher:
    """Watches Docker container lifecycle events and updates orchestrator state in real-time."""

    def __init__(self, reconnect_delay_s: float = 2.0):
        self._thread: Optional[threading.Thread] = None
        self._stop_sync = threading.Event()
        self._reconnect_delay_s = reconnect_delay_s
        self._event_stream = None
        self._event_stream_lock = threading.RLock()
        self._subscribers: list[Callable[[dict], None]] = []
        self._has_connected_once = False

    def subscribe(self, callback: Callable[[dict], None]):
        """Register a callback invoked after each processed Docker event."""
        self._subscribers.append(callback)

    async def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop_sync.clear()
        self._bootstrap_vpn_nodes_snapshot()
        self._thread = threading.Thread(target=self._run_sync, name="docker-event-watcher", daemon=True)
        self._thread.start()
        logger.info("Docker event watcher started")

    async def stop(self):
        self._stop_sync.set()
        self._close_event_stream()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning("Docker event watcher thread did not stop cleanly within timeout")

        self._thread = None
        logger.info("Docker event watcher stopped")

    def _run_sync(self):
        while not self._stop_sync.is_set():
            try:
                self._consume_events_blocking()
            except Exception as e:
                if self._stop_sync.is_set():
                    break
                logger.warning(f"Docker event stream disconnected: {e}")

            if self._stop_sync.is_set():
                break

            self._stop_sync.wait(timeout=self._reconnect_delay_s)

    def _consume_events_blocking(self):
        # Use a single fast connection attempt so shutdown remains responsive
        # when Docker is unavailable (tests/local offline scenarios).
        cli = docker.from_env(timeout=10)
        cli.ping()
        stream = None

        try:
            stream = cli.events(decode=True, filters=_DOCKER_EVENT_FILTERS)
            with self._event_stream_lock:
                self._event_stream = stream

            if self._has_connected_once:
                try:
                    from .reindex import run_reindex

                    run_reindex()
                    logger.warning("Docker event stream reconnected. Executed full state reconciliation to catch missed events.")
                    self._request_engine_reconcile(reason="docker_event_stream_reconnected")
                except Exception as e:
                    logger.warning(f"Docker event stream reconnected but reconciliation failed: {e}")
            else:
                self._has_connected_once = True

            for event in stream:
                if self._stop_sync.is_set():
                    break
                if not isinstance(event, dict):
                    continue

                action = _normalize_docker_event_action(event)
                if not action:
                    continue

                self._handle_event(event, action)
        finally:
            with self._event_stream_lock:
                if self._event_stream is stream:
                    self._event_stream = None

            if stream is not None:
                with suppress(Exception):
                    stream.close()
            with suppress(Exception):
                cli.close()

    def _close_event_stream(self):
        with self._event_stream_lock:
            stream = self._event_stream
            self._event_stream = None

        if stream is not None:
            with suppress(Exception):
                stream.close()

    def _handle_event(self, event: dict, action: str):
        actor = event.get("Actor") or {}
        attrs = actor.get("Attributes") or {}
        container_id = event.get("id") or actor.get("ID")
        container_name = attrs.get("name")

        if not container_id:
            return

        try:
            self._apply_state_update(container_id=container_id, container_name=container_name, action=action, attrs=attrs)
        except Exception as e:
            logger.warning(f"Failed to apply Docker event to state ({container_id[:12]} {action}): {e}")

        for subscriber in self._subscribers:
            try:
                subscriber(event)
            except Exception as e:
                logger.warning(f"Docker event subscriber failed: {e}")

    @staticmethod
    def _is_managed_engine(attrs: dict) -> bool:
        from ..core.config import cfg

        label_key, label_value = cfg.CONTAINER_LABEL.split("=", 1)
        return attrs.get(label_key) == label_value

    @staticmethod
    def _is_managed_vpn_node(attrs: dict) -> bool:
        return (
            attrs.get("acestream-orchestrator.managed") == "true"
            and attrs.get("role") == "vpn_node"
        )

    @staticmethod
    def _is_dynamic_vpn_name(container_name: str) -> bool:
        return str(container_name or "").strip().lower().startswith("gluetun-dyn-")

    @classmethod
    def _match_vpn_name(cls, container_name: Optional[str], attrs: dict) -> Optional[str]:
        if container_name and (cls._is_managed_vpn_node(attrs) or cls._is_dynamic_vpn_name(container_name)):
            return container_name
        return None

    def _bootstrap_vpn_nodes_snapshot(self):
        """Seed VPN node state from currently existing containers before streaming events."""
        from .state import state

        cli = None
        try:
            cli = get_client(timeout=20)
            containers = cli.containers.list(all=True)
            for container in containers:
                container_name = str(getattr(container, "name", "") or "").strip()
                if not container_name:
                    continue

                labels = dict(getattr(container, "labels", {}) or {})
                is_dynamic = self._is_managed_vpn_node(labels) or self._is_dynamic_vpn_name(container_name)
                if not is_dynamic:
                    continue

                health = str((container.attrs or {}).get("State", {}).get("Health", {}).get("Status") or "").strip().lower()
                status = str(getattr(container, "status", "") or "").strip().lower()
                if status == "running":
                    if health == "healthy":
                        node_status = "healthy"
                    elif health == "unhealthy":
                        node_status = "unhealthy"
                    else:
                        node_status = "running"
                else:
                    node_status = "down"

                state.update_vpn_node_status(
                    container_name,
                    node_status,
                    metadata={
                        "managed_dynamic": bool(is_dynamic),
                        "provider": labels.get("acestream.vpn.provider"),
                        "protocol": labels.get("acestream.vpn.protocol"),
                        "credential_id": labels.get("acestream.vpn.credential_id"),
                        "port_forwarding_supported": str(
                            labels.get("acestream.vpn.port_forwarding_supported", "false")
                        ).strip().lower() == "true",
                    },
                )
        except Exception as e:
            logger.debug("Failed to bootstrap VPN node snapshot from Docker: %s", e)
        finally:
            if cli is not None:
                with suppress(Exception):
                    cli.close()

    def _apply_state_update(self, container_id: str, container_name: Optional[str], action: str, attrs: dict):
        from .state import state

        vpn_name = self._match_vpn_name(container_name, attrs)
        if vpn_name:
            managed_dynamic = bool(self._is_managed_vpn_node(attrs))
            if not managed_dynamic and container_name:
                managed_dynamic = self._is_dynamic_vpn_name(container_name)

            vpn_metadata = {
                "managed_dynamic": managed_dynamic,
                "provider": attrs.get("acestream.vpn.provider"),
                "protocol": attrs.get("acestream.vpn.protocol"),
                "credential_id": attrs.get("acestream.vpn.credential_id"),
                "port_forwarding_supported": str(
                    attrs.get("acestream.vpn.port_forwarding_supported", "false")
                ).strip().lower() == "true",
            }
            if action in ("die", "destroy"):
                state.update_vpn_node_status(vpn_name, "down", metadata=vpn_metadata)
                self._emit_vpn_evictions(vpn_name, reason="node_down")
            elif action == "start":
                state.update_vpn_node_status(vpn_name, "running", metadata=vpn_metadata)
            elif action == "health_status: healthy":
                state.update_vpn_node_status(vpn_name, "healthy", metadata=vpn_metadata)
                self._request_engine_reconcile(reason=f"vpn_ready:{vpn_name}")
            elif action == "health_status: unhealthy":
                state.update_vpn_node_status(vpn_name, "unhealthy", metadata=vpn_metadata)
                self._emit_vpn_evictions(vpn_name, reason="node_unhealthy")

        if not self._is_managed_engine(attrs):
            return

        labels = {k: str(v) for k, v in attrs.items() if k != "name" and v is not None}
        state.apply_engine_docker_event(
            container_id=container_id,
            container_name=container_name,
            action=action,
            labels=labels,
        )

    @staticmethod
    def _emit_vpn_evictions(vpn_container: str, reason: str):
        from .state import state

        engines = state.get_engines_by_vpn(vpn_container)
        if not engines:
            return

        pending = state.list_pending_scaling_intents(intent_type="terminate_request", limit=2000)
        pending_ids = {
            str((intent.get("details") or {}).get("container_id") or "")
            for intent in pending
        }

        emitted = 0
        for engine in engines:
            if engine.container_id in pending_ids:
                continue

            state.emit_scaling_intent(
                intent_type="terminate_request",
                details={
                    "requested_by": "docker_event_watcher",
                    "eviction_reason": "vpn_not_ready",
                    "vpn_container": vpn_container,
                    "node_reason": reason,
                    "container_id": engine.container_id,
                    "force": True,
                },
            )
            emitted += 1

        if emitted > 0:
            logger.warning(
                f"VPN node '{vpn_container}' marked NotReady ({reason}); requested eviction for {emitted} engine(s)"
            )
            DockerEventWatcher._request_engine_reconcile(reason=f"vpn_not_ready:{vpn_container}")

    @staticmethod
    def _request_engine_reconcile(reason: str):
        try:
            from .autoscaler import engine_controller

            engine_controller.request_reconcile(reason=reason)
        except Exception as e:
            logger.debug(f"Failed to request engine reconcile: {e}")

def get_client(timeout: int = 30):
    """
    Get Docker client with retry logic for connection.
    
    Args:
        timeout: Socket timeout in seconds (default: 30s, increased from 15s for better 
                resilience during VPN container lifecycle events)
    """
    max_retries = 10
    retry_delay = 2

    # Fast-fail when Docker uses a unix socket that does not exist.
    # This avoids multi-minute retry delays in test/dev environments without Docker.
    socket_path = _resolve_docker_socket_path()
    if socket_path and not os.path.exists(socket_path):
        raise DockerException(f"Docker socket not found: {socket_path}")
    
    for attempt in range(max_retries):
        try:
            client = docker.from_env(timeout=timeout)
            # Test the connection
            client.ping()
            return client
        except (DockerException, Exception) as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to connect to Docker after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Docker connection attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 10)  # Exponential backoff, max 10s

    raise RuntimeError("Failed to obtain Docker client")

def safe(container_call, *args, **kwargs):
    try:
        return container_call(*args, **kwargs)
    except APIError as e:
        raise RuntimeError(str(e)) from e

def get_orchestrator_network() -> str | None:
    """Detect the Docker network the orchestrator is running on."""
    global _orchestrator_network
    if _orchestrator_network:
        return _orchestrator_network
        
    # Check if we are running in Docker
    if not os.path.exists('/.dockerenv'):
        # Not in Docker, skip detection
        return None
        
    client = None
    try:
        client = get_client()
        # Default Docker behavior: hostname matches container ID or name
        hostname = socket.gethostname()
        container = client.containers.get(hostname)
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        if networks:
            network_names = list(networks.keys())
            # If in multiple networks and "bridge" is one of them, prioritize others
            # as they are likely user-defined bridge networks from docker-compose.
            if len(network_names) > 1 and "bridge" in network_names:
                _orchestrator_network = [n for n in network_names if n != "bridge"][0]
            else:
                _orchestrator_network = network_names[0]
                
            logger.info(f"Detected orchestrator network: '{_orchestrator_network}'. Engines will be provisioned in this network by default.")
            return _orchestrator_network
    except Exception as e:
        logger.debug(f"Failed to detect orchestrator Docker network: {e}")
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()
        
    return None


docker_event_watcher = DockerEventWatcher()
