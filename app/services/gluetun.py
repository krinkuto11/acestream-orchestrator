"""Passive Gluetun helpers for event-driven VPN orchestration.

This module intentionally avoids background polling loops. VPN node readiness and
health transitions are driven by DockerEventWatcher events stored in state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Set, Tuple

import httpx
from docker.errors import NotFound

from ..core.config import cfg
from .docker_client import get_client

logger = logging.getLogger(__name__)


# Forwarded-port cache: container_name -> (port, cached_at)
_FORWARDED_PORT_CACHE: Dict[str, Tuple[int, datetime]] = {}

# Host public-IP cache to avoid repeated external lookups during frequent status polling.
_HOST_PUBLIC_IP_CACHE: Tuple[Optional[str], datetime] | None = None


def _cache_ttl_seconds() -> int:
    try:
        return max(1, int(getattr(cfg, "GLUETUN_PORT_CACHE_TTL_S", 60)))
    except Exception:
        return 60


def _host_public_ip_cache_ttl_seconds() -> int:
    # Keep this much longer than VPN status cache since it changes infrequently.
    return 120


def _get_cached_host_public_ip() -> Optional[str]:
    global _HOST_PUBLIC_IP_CACHE

    if not _HOST_PUBLIC_IP_CACHE:
        return None

    public_ip, cached_at = _HOST_PUBLIC_IP_CACHE
    age = (datetime.now(timezone.utc) - cached_at).total_seconds()
    if age > _host_public_ip_cache_ttl_seconds():
        _HOST_PUBLIC_IP_CACHE = None
        return None
    return public_ip


def _set_cached_host_public_ip(public_ip: Optional[str]) -> Optional[str]:
    global _HOST_PUBLIC_IP_CACHE

    normalized = str(public_ip or "").strip() or None
    _HOST_PUBLIC_IP_CACHE = (normalized, datetime.now(timezone.utc))
    return normalized


def _state_node(container_name: str) -> Optional[Dict[str, object]]:
    try:
        from .state import state

        for node in state.list_vpn_nodes():
            if str(node.get("container_name") or "").strip() == container_name:
                return node
    except Exception:
        return None
    return None


def _discover_vpn_names() -> Set[str]:
    names: Set[str] = set()
    try:
        from .state import state

        for node in state.list_vpn_nodes():
            name = str(node.get("container_name") or "").strip()
            if name:
                names.add(name)
    except Exception:
        return names
    return names


def _resolve_target_container(container_name: Optional[str] = None) -> Optional[str]:
    explicit = str(container_name or "").strip()
    if explicit:
        return explicit

    discovered = _discover_vpn_names()
    if not discovered:
        return None
    return sorted(discovered)[0]


def _check_container_health_sync(container_name: str) -> Optional[bool]:
    """Best-effort Docker health probe used as fallback when state is missing."""
    try:
        cli = get_client(timeout=20)
        container = cli.containers.get(container_name)
        container.reload()

        if container.status != "running":
            return False

        health = (container.attrs or {}).get("State", {}).get("Health", {})
        if health:
            status = str(health.get("Status") or "").strip().lower()
            if status == "healthy":
                return True
            if status == "unhealthy":
                return False
        return True
    except NotFound:
        return False
    except Exception as exc:
        logger.debug("Docker health probe failed for '%s': %s", container_name, exc)
        return None


def _is_control_server_reachable_sync(container_name: str, timeout: float = 3.0, require_connected: bool = False) -> bool:
    try:
        url = f"http://{container_name}:{cfg.GLUETUN_API_PORT}/v1/vpn/status"
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
            if response.status_code == 401:
                # Control server is reachable but auth is required.
                return True
            
            if response.status_code != 200:
                logger.debug(
                    "Gluetun control API at %s returned unexpected status %s",
                    url,
                    response.status_code,
                )
                return False

            data = response.json()
            if require_connected:
                status = str(data.get("status") or "").strip().lower()
                if status not in ("connected", "running"):
                    logger.debug(
                        "Gluetun control API at %s responded but VPN status is '%s' (expected 'connected' or 'running')",
                        url,
                        status,
                    )
                    return False
            return True
    except httpx.ConnectTimeout:
        logger.debug("Gluetun control API at %s: ConnectTimeout after %ss", container_name, timeout)
        return False
    except httpx.ReadTimeout:
        logger.debug("Gluetun control API at %s: ReadTimeout after %ss", container_name, timeout)
        return False
    except httpx.ConnectError as e:
        logger.debug("Gluetun control API at %s: ConnectError: %s", container_name, e)
        return False
    except Exception as exc:
        logger.debug("Control server not reachable yet for '%s': %s", container_name, exc)
        return False


def _get_cached_port(container_name: str) -> Optional[int]:
    item = _FORWARDED_PORT_CACHE.get(container_name)
    if not item:
        return None

    port, cached_at = item
    age = (datetime.now(timezone.utc) - cached_at).total_seconds()
    if age > _cache_ttl_seconds():
        _FORWARDED_PORT_CACHE.pop(container_name, None)
        return None
    return port


def _set_cached_port(container_name: str, port: int):
    _FORWARDED_PORT_CACHE[container_name] = (int(port), datetime.now(timezone.utc))


class PassiveVpnMonitor:
    """Compatibility wrapper for legacy callers expecting per-VPN monitor objects."""

    def __init__(self, container_name: str, manager: "GluetunMonitor"):
        self.container_name = container_name
        self._manager = manager

    def is_healthy(self) -> Optional[bool]:
        return self._manager.is_healthy(self.container_name)

    def is_in_recovery_stabilization_period(self) -> bool:
        # Event-driven architecture does not keep local stabilization state.
        return False

    def _is_in_restart_grace_period(self) -> bool:
        return False


class GluetunMonitor:
    """Passive compatibility facade. No background tasks, no polling loop."""

    def __init__(self):
        self._callbacks = []

    async def start(self):
        logger.info("Passive Gluetun monitor initialized (event-driven mode, no polling loop)")

    async def stop(self):
        logger.info("Passive Gluetun monitor stopped")

    def add_health_transition_callback(self, callback):
        self._callbacks.append(callback)

    def get_vpn_monitor(self, container_name: str) -> PassiveVpnMonitor:
        return PassiveVpnMonitor(container_name, self)

    def get_all_vpn_monitors(self) -> Dict[str, PassiveVpnMonitor]:
        names = _discover_vpn_names()
        return {name: PassiveVpnMonitor(name, self) for name in names}

    def is_healthy(self, container_name: Optional[str] = None) -> Optional[bool]:
        target = _resolve_target_container(container_name)
        if not target:
            return None

        node = _state_node(target)
        if node is not None:
            return bool(node.get("healthy"))

        return _check_container_health_sync(target)

    async def wait_for_healthy(self, timeout: float = 30.0, container_name: Optional[str] = None) -> bool:
        target = _resolve_target_container(container_name)
        if not target:
            return True

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self.is_healthy(target) is True:
                return True
            await asyncio.sleep(1)
        return False

    async def get_forwarded_port(self, container_name: Optional[str] = None) -> Optional[int]:
        return await fetch_forwarded_port(container_name)

    def get_cached_forwarded_port(self, container_name: Optional[str] = None) -> Optional[int]:
        target = _resolve_target_container(container_name)
        if not target:
            return None
        return _get_cached_port(target)

    def invalidate_port_cache(self, container_name: Optional[str] = None):
        if container_name:
            _FORWARDED_PORT_CACHE.pop(container_name, None)
            return
        _FORWARDED_PORT_CACHE.clear()


async def fetch_forwarded_port(container_name: Optional[str] = None) -> Optional[int]:
    target = _resolve_target_container(container_name)
    if not target:
        return None

    cached = _get_cached_port(target)
    if cached is not None:
        return cached

    if not _is_control_server_reachable_sync(target):
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"http://{target}:{cfg.GLUETUN_API_PORT}/v1/portforward")
            response.raise_for_status()
            data = response.json()
            port = data.get("port")
            if port is None or str(port) == "0":
                return None
            port_int = int(port)
            _set_cached_port(target, port_int)
            return port_int
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            logger.info("Port forwarding not supported by VPN config for '%s'", target)
            return None
        logger.warning("Failed to fetch forwarded port for '%s': %s", target, exc)
        return None
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        logger.debug("Control server not ready for forwarded-port query on '%s': %s", target, exc)
        return None
    except Exception as exc:
        logger.warning("Failed to fetch forwarded port for '%s': %s", target, exc)
        return None


def get_forwarded_port_sync(container_name: Optional[str] = None) -> Optional[int]:
    target = _resolve_target_container(container_name)
    if not target:
        return None

    cached = _get_cached_port(target)
    if cached is not None:
        return cached

    if not _is_control_server_reachable_sync(target):
        return None

    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"http://{target}:{cfg.GLUETUN_API_PORT}/v1/portforward")
            response.raise_for_status()
            data = response.json()
            port = data.get("port")
            if port is None or str(port) == "0":
                return None
            port_int = int(port)
            _set_cached_port(target, port_int)
            return port_int
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            logger.info("Port forwarding not supported by VPN config for '%s'", target)
            return None
        logger.warning("Failed to fetch forwarded port for '%s': %s", target, exc)
        return None
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        logger.debug("Control server not ready for forwarded-port query on '%s': %s", target, exc)
        return None
    except Exception as exc:
        logger.warning("Failed to fetch forwarded port for '%s': %s", target, exc)
        return None


def wait_for_port_sync(container_name: Optional[str] = None, timeout: float = 30.0) -> Optional[int]:
    """Block until a valid forwarded port (>0) is available, or timeout."""
    import time
    target = _resolve_target_container(container_name)
    if not target:
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        port = get_forwarded_port_sync(target)
        if port and port > 0:
            return port
        time.sleep(1.5)
    
    logger.warning("Timed out waiting for forwarded port from VPN node '%s'", target)
    return None


def normalize_provider_name(provider: str) -> str:
    provider_map = {
        "airvpn": "AirVPN",
        "cyberghost": "Cyberghost",
        "expressvpn": "ExpressVPN",
        "fastestvpn": "FastestVPN",
        "giganews": "Giganews",
        "hidemyass": "HideMyAss",
        "ipvanish": "IPVanish",
        "ivpn": "IVPN",
        "mullvad": "Mullvad",
        "nordvpn": "NordVPN",
        "perfect privacy": "Perfect Privacy",
        "perfectprivacy": "Perfect Privacy",
        "privado": "Privado",
        "private internet access": "Private Internet Access",
        "pia": "Private Internet Access",
        "privatevpn": "PrivateVPN",
        "protonvpn": "ProtonVPN",
        "purevpn": "PureVPN",
        "slickvpn": "SlickVPN",
        "surfshark": "Surfshark",
        "torguard": "TorGuard",
        "vpnsecure.me": "VPNSecure.me",
        "vpnsecure": "VPNSecure.me",
        "vpnunlimited": "VPNUnlimited",
        "vyprvpn": "Vyprvpn",
        "wevpn": "WeVPN",
        "windscribe": "Windscribe",
    }
    provider_lower = str(provider or "").lower().strip()
    return provider_map.get(provider_lower, str(provider or "").title())


def get_vpn_provider(container_name: Optional[str] = None) -> Optional[str]:
    target = _resolve_target_container(container_name)
    if not target:
        return None

    try:
        cli = get_client(timeout=20)
        container = cli.containers.get(target)
        container.reload()
        env_vars = (container.attrs or {}).get("Config", {}).get("Env", [])

        for env_var in env_vars:
            if str(env_var).startswith("VPN_SERVICE_PROVIDER="):
                provider = env_var.split("=", 1)[1]
                return normalize_provider_name(provider)
        return None
    except NotFound:
        return None
    except Exception as exc:
        logger.debug("Failed to get VPN provider from '%s': %s", target, exc)
        return None


def get_vpn_public_ip_info(container_name: Optional[str] = None) -> Optional[Dict[str, str]]:
    target = _resolve_target_container(container_name)
    if not target:
        return None

    if gluetun_monitor.is_healthy(target) is False:
        return None

    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"http://{target}:{cfg.GLUETUN_API_PORT}/v1/publicip/ip")
            response.raise_for_status()
            data = response.json()
            if data.get("public_ip"):
                return data
            return None
    except Exception as exc:
        logger.debug("Failed to fetch VPN public IP info from '%s': %s", target, exc)
        return None


def get_vpn_public_ip(container_name: Optional[str] = None) -> Optional[str]:
    info = get_vpn_public_ip_info(container_name)
    return info.get("public_ip") if info else None


def get_host_public_ip() -> Optional[str]:
    cached = _get_cached_host_public_ip()
    if cached:
        return cached

    endpoints = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/ip",
    ]

    for endpoint in endpoints:
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(endpoint)
                response.raise_for_status()
                if endpoint.endswith("format=json"):
                    data = response.json() or {}
                    candidate = str(data.get("ip") or data.get("public_ip") or "").strip()
                else:
                    candidate = str(response.text or "").strip()

                if candidate:
                    return _set_cached_host_public_ip(candidate)
        except Exception as exc:
            logger.debug("Failed to fetch host public IP from '%s': %s", endpoint, exc)

    # Cache miss/failure to avoid hammering external services on repeated polls.
    return _set_cached_host_public_ip(None)


def get_effective_public_ip(container_name: Optional[str] = None) -> Optional[str]:
    vpn_ip = get_vpn_public_ip(container_name)
    if vpn_ip:
        return vpn_ip
    return get_host_public_ip()


def _single_vpn_status(container_name: str) -> Dict[str, object]:
    node = _state_node(container_name)
    state_health = bool(node.get("healthy")) if node is not None else None
    health_bool = state_health if state_health is not None else _check_container_health_sync(container_name)
    control_ready = _is_control_server_reachable_sync(container_name)

    connected = bool(health_bool) and control_ready
    health = "healthy" if connected else "unhealthy"

    assigned_hostname = None
    if node and "metadata" in node:
        assigned_hostname = node["metadata"].get("assigned_hostname")

    forwarded_port = get_forwarded_port_sync(container_name) if connected else None
    provider = get_vpn_provider(container_name) if connected else None
    ip_info = get_vpn_public_ip_info(container_name) if connected else None

    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "enabled": True,
        "status": "running" if connected else "not_ready",
        "container_name": container_name,
        "container": container_name,
        "health": health,
        "connected": connected,
        "forwarded_port": forwarded_port,
        "public_ip": (ip_info or {}).get("public_ip"),
        "provider": provider,
        "country": (ip_info or {}).get("country"),
        "city": (ip_info or {}).get("city"),
        "region": (ip_info or {}).get("region"),
        "assigned_hostname": assigned_hostname,
        "last_check": now_iso,
        "last_check_at": now_iso,
    }


def get_vpn_status() -> Dict[str, object]:
    discovered = sorted(_discover_vpn_names())
    primary = discovered[0] if discovered else None

    if not primary:
        return {
            "mode": "disabled",
            "enabled": False,
            "status": "disabled",
            "container_name": None,
            "container": None,
            "health": "unknown",
            "connected": False,
            "forwarded_port": None,
            "public_ip": get_host_public_ip(),
            "last_check": None,
            "last_check_at": None,
            "vpn1": {},
            "vpn2": {},
            "emergency_mode": {
                "active": False,
                "failed_vpn": None,
                "healthy_vpn": None,
                "duration_seconds": 0,
            },
        }

    vpn1_status = _single_vpn_status(primary)
    secondary = next((name for name in discovered if name and name != primary), None)
    vpn2_status = _single_vpn_status(secondary) if secondary else {}
    any_healthy = bool(vpn1_status.get("connected")) or bool(vpn2_status.get("connected"))

    return {
        "mode": "multi" if secondary else "single",
        "enabled": True,
        "status": "running" if any_healthy else "unhealthy",
        "container_name": primary,
        "container": primary,
        "health": "healthy" if any_healthy else "unhealthy",
        "connected": any_healthy,
        "forwarded_port": vpn1_status.get("forwarded_port"),
        "public_ip": vpn1_status.get("public_ip") or vpn2_status.get("public_ip"),
        "last_check": datetime.now(timezone.utc).isoformat(),
        "last_check_at": datetime.now(timezone.utc).isoformat(),
        "vpn1": vpn1_status,
        "vpn2": vpn2_status,
        "emergency_mode": {
            "active": False,
            "failed_vpn": None,
            "healthy_vpn": None,
            "duration_seconds": 0,
        },
    }


gluetun_monitor = GluetunMonitor()