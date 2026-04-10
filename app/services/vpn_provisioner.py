from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import uuid
from contextlib import suppress
from typing import Any, Dict, List, Optional

from docker.errors import APIError, NotFound

from ..core.config import cfg
from .docker_client import get_client, get_orchestrator_network
from . import gluetun_servers_volume
from .state import state
from .vpn_credentials import credential_manager
from .vpn_reputation import vpn_reputation_manager

logger = logging.getLogger(__name__)


PROVIDER_ALIASES = {
    "pia": "private internet access",
    "privateinternetaccess": "private internet access",
    "private_internet_access": "private internet access",
}

PORT_FORWARDING_NATIVE_PROVIDERS = {
    "private internet access",
    "perfect privacy",
    "privatevpn",
    "protonvpn",
}

REGION_DEFAULTS_BY_PROVIDER = {
    "private internet access": "SERVER_REGIONS",
    "giganews": "SERVER_REGIONS",
    "windscribe": "SERVER_REGIONS",
    "vyprvpn": "SERVER_REGIONS",
}


class VPNProvisioner:
    """Provision and lifecycle-manage dynamic Gluetun VPN containers."""

    def __init__(self, default_image: str = "qmcgaw/gluetun"):
        self._default_image = default_image

    async def provision_node(
        self,
        vpn_settings: Dict[str, Any],
        *,
        requested_provider: Optional[str] = None,
        requested_regions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Lease credentials, translate settings to Gluetun env, and start a VPN node."""
        settings = dict(vpn_settings or {})
        container_name = self._generate_container_name()

        lease = await credential_manager.acquire_lease(container_id=container_name)
        if not lease:
            raise RuntimeError("No available VPN credential lease; max VPN capacity reached")

        credential = dict(lease.get("credential") or {})

        provider = self._resolve_provider(
            requested_provider=requested_provider,
            settings=settings,
            credential=credential,
        )
        protocol = self._resolve_protocol(settings=settings, credential=credential)
        regions = self._resolve_regions(requested_regions=requested_regions, settings=settings, credential=credential)
        provider_supports_port_forwarding = self.provider_supports_port_forwarding(provider)
        credential_supports_port_forwarding = self.credential_supports_port_forwarding(credential)
        port_forwarding_supported = provider_supports_port_forwarding and credential_supports_port_forwarding

        if provider_supports_port_forwarding and not credential_supports_port_forwarding:
            logger.info(
                "Credential '%s' disabled port forwarding support for provider '%s'",
                str(lease.get("credential_id") or "unknown"),
                provider,
            )

        env = self._build_gluetun_env(
            provider=provider,
            protocol=protocol,
            settings=settings,
            credential=credential,
            regions=regions,
            port_forwarding_supported=port_forwarding_supported,
        )
        safe_hostname: Optional[str] = None
        has_explicit_server_pin = bool(
            str(env.get("SERVER_HOSTNAMES") or "").strip()
            or str(env.get("WIREGUARD_ENDPOINTS") or "").strip()
        )
        require_port_forwarding = str(env.get("VPN_PORT_FORWARDING") or "").strip().lower() == "on"
        if not has_explicit_server_pin:
            safe_hostname = await asyncio.to_thread(
                vpn_reputation_manager.get_safe_hostname,
                provider,
                regions,
                protocol,
                require_port_forwarding,
            )
        if safe_hostname:
            env["SERVER_HOSTNAMES"] = safe_hostname

        assigned_hostname = str(env.get("SERVER_HOSTNAMES") or safe_hostname or "").split(",", 1)[0].strip().lower()

        labels = self._build_labels(
            provider=provider,
            protocol=protocol,
            credential_id=str(lease.get("credential_id") or ""),
            port_forwarding_supported=port_forwarding_supported,
        )

        image = str(settings.get("image") or self._default_image)
        network = cfg.DOCKER_NETWORK or get_orchestrator_network()
        cap_add, devices, volumes = self._build_runtime_privileges(protocol=protocol, settings=settings, credential=credential)

        try:
            container = await asyncio.to_thread(
                self._run_container_sync,
                image,
                container_name,
                env,
                labels,
                network,
                cap_add,
                devices,
                volumes,
            )
        except Exception:
            await credential_manager.release_lease(container_name)
            raise

        state.update_vpn_node_status(
            container_name,
            "running",
            metadata={
                "managed_dynamic": True,
                "provider": provider,
                "protocol": protocol,
                "credential_id": lease.get("credential_id"),
                "port_forwarding_supported": port_forwarding_supported,
                "assigned_hostname": assigned_hostname or None,
            },
        )

        return {
            "container_id": container.id,
            "container_name": container_name,
            "provider": provider,
            "protocol": protocol,
            "lease": {
                "credential_id": lease.get("credential_id"),
                "leased_at": lease.get("leased_at"),
            },
            "control_server_url": f"http://{container_name}:{cfg.GLUETUN_API_PORT}",
            "network": network,
            "labels": labels,
            "environment": env,
            "port_forwarding_supported": port_forwarding_supported,
        }

    async def destroy_node(self, container_ref: str, *, release_credential: bool = True, force: bool = True) -> Dict[str, Any]:
        """Stop/remove a dynamic VPN node and release its credential lease."""
        resolved_name = await asyncio.to_thread(self._resolve_container_name_sync, container_ref)

        removed = await asyncio.to_thread(self._destroy_container_sync, container_ref, force)
        lease_released = False
        if release_credential:
            lease_released = await credential_manager.release_lease(container_ref)
            if not lease_released and resolved_name:
                lease_released = await credential_manager.release_lease(resolved_name)

        if resolved_name:
            state.update_vpn_node_status(resolved_name, "down")

        return {
            "removed": removed,
            "lease_released": lease_released,
            "container_name": resolved_name,
        }

    async def list_managed_nodes(self, include_stopped: bool = False) -> List[Dict[str, Any]]:
        """List currently managed dynamic VPN nodes."""
        return await asyncio.to_thread(self._list_managed_nodes_sync, include_stopped)

    def _generate_container_name(self) -> str:
        return f"gluetun-dyn-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def provider_supports_port_forwarding(provider: Optional[str]) -> bool:
        normalized = str(provider or "").strip().lower()
        return normalized in PORT_FORWARDING_NATIVE_PROVIDERS

    @classmethod
    def credential_supports_port_forwarding(cls, credential: Dict[str, Any]) -> bool:
        """
        Determine whether a credential is eligible for forwarded-port workflows.

        Credentials default to supporting forwarding for backward compatibility.
        """
        value = credential.get("port_forwarding")
        if value is None:
            return True
        return cls._coerce_bool(value)

    def _resolve_provider(
        self,
        *,
        requested_provider: Optional[str],
        settings: Dict[str, Any],
        credential: Dict[str, Any],
    ) -> str:
        provider = (
            requested_provider
            or credential.get("provider")
            or credential.get("vpn_service_provider")
            or settings.get("provider")
            or (settings.get("providers") or [None])[0]
            or "protonvpn"
        )
        normalized = str(provider).strip().lower()
        return PROVIDER_ALIASES.get(normalized, normalized)

    @staticmethod
    def _resolve_protocol(*, settings: Dict[str, Any], credential: Dict[str, Any]) -> str:
        protocol = credential.get("protocol") or credential.get("vpn_type") or settings.get("protocol") or "wireguard"
        normalized = str(protocol).strip().lower()
        if normalized not in {"wireguard", "openvpn"}:
            raise ValueError("VPN protocol must be wireguard or openvpn")
        return normalized

    @staticmethod
    def _resolve_regions(
        *,
        requested_regions: Optional[List[str]],
        settings: Dict[str, Any],
        credential: Dict[str, Any],
    ) -> List[str]:
        if requested_regions is not None:
            source: List[Any] = list(requested_regions)
        elif isinstance(credential.get("regions"), list):
            source = list(credential.get("regions") or [])
        else:
            source = list(settings.get("regions") or [])
        return [str(item).strip() for item in source if str(item).strip()]

    def _build_gluetun_env(
        self,
        *,
        provider: str,
        protocol: str,
        settings: Dict[str, Any],
        credential: Dict[str, Any],
        regions: List[str],
        port_forwarding_supported: bool,
    ) -> Dict[str, str]:
        env: Dict[str, str] = {
            "VPN_SERVICE_PROVIDER": provider,
            "VPN_TYPE": protocol,
            "HTTP_CONTROL_SERVER_ADDRESS": f":{cfg.GLUETUN_API_PORT}",
        }

        auth_default_role = credential.get("http_control_server_auth_default_role")
        if auth_default_role is None:
            auth_default_role = settings.get("http_control_server_auth_default_role")
        if auth_default_role is None:
            # Keep control API reachable for internal orchestrator calls by default.
            env["HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE"] = '{"auth":"none"}'
        else:
            env["HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE"] = str(auth_default_role)

        tz = str(settings.get("tz") or os.getenv("TZ") or "UTC").strip()
        if tz:
            env["TZ"] = tz

        if settings.get("disable_dot") is True:
            env["DOT"] = "off"

        allow_ipv6_wireguard = self._coerce_bool(
            credential.get("wireguard_allow_ipv6")
            if credential.get("wireguard_allow_ipv6") is not None
            else settings.get("wireguard_allow_ipv6")
        )

        self._apply_credential_env(
            env=env,
            protocol=protocol,
            credential=credential,
            allow_ipv6_wireguard=allow_ipv6_wireguard,
        )
        self._apply_region_env(env=env, provider=provider, regions=regions, credential=credential)
        self._apply_port_forwarding_env(
            env=env,
            provider=provider,
            settings=settings,
            credential=credential,
            port_forwarding_supported=port_forwarding_supported,
        )
        self._apply_optional_credential_env(env=env, protocol=protocol, credential=credential)

        for key, value in (settings.get("extra_env") or {}).items():
            key_s = str(key).strip()
            if not key_s:
                continue
            env[key_s] = str(value)

        for key, value in (credential.get("extra_env") or {}).items():
            key_s = str(key).strip()
            if not key_s:
                continue
            env[key_s] = str(value)

        self._apply_port_forwarding_filter_guard(
            env=env,
            provider=provider,
            protocol=protocol,
        )

        return env

    @staticmethod
    def _normalize_wireguard_addresses(raw_addresses: Any) -> List[str]:
        if raw_addresses is None:
            return []

        tokens: List[str] = []

        def _extend_from_value(value: Any):
            if value is None:
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _extend_from_value(item)
                return

            text = str(value).strip()
            if not text:
                return

            # Be resilient to stored Python-list-like strings such as
            # "['10.2.0.2/32', '2a07:.../128']".
            text = text.replace("[", "").replace("]", "").replace("\"", "").replace("'", "")
            for part in text.split(","):
                item = part.strip()
                if item:
                    tokens.append(item)

        _extend_from_value(raw_addresses)

        # Preserve order while removing duplicates.
        return list(dict.fromkeys(tokens))

    @staticmethod
    def _is_ipv4_interface(value: str) -> bool:
        try:
            return ipaddress.ip_interface(value).version == 4
        except ValueError:
            return False

    @classmethod
    def _apply_credential_env(
        cls,
        *,
        env: Dict[str, str],
        protocol: str,
        credential: Dict[str, Any],
        allow_ipv6_wireguard: bool = False,
    ):
        if protocol == "wireguard":
            private_key = (
                credential.get("wireguard_private_key")
                or credential.get("private_key")
                or credential.get("wg_private_key")
                or credential.get("PrivateKey")
            )
            if not private_key:
                raise ValueError("wireguard credential is missing private_key/WIREGUARD_PRIVATE_KEY")
            env["WIREGUARD_PRIVATE_KEY"] = str(private_key)

            addresses = (
                credential.get("wireguard_addresses")
                or credential.get("addresses")
                or credential.get("Address")
            )
            if addresses:
                normalized_addresses = cls._normalize_wireguard_addresses(addresses)
                if not normalized_addresses:
                    raise ValueError("wireguard credential has empty addresses")

                if allow_ipv6_wireguard:
                    env["WIREGUARD_ADDRESSES"] = ",".join(normalized_addresses)
                else:
                    ipv4_addresses = [value for value in normalized_addresses if cls._is_ipv4_interface(value)]
                    if not ipv4_addresses:
                        raise ValueError(
                            "wireguard credential addresses are IPv6-only; provide an IPv4 address or enable wireguard_allow_ipv6"
                        )
                    env["WIREGUARD_ADDRESSES"] = ",".join(ipv4_addresses)
                    if len(ipv4_addresses) < len(normalized_addresses):
                        logger.info("Filtered IPv6 WireGuard interface addresses for IPv4-only Gluetun runtime")

            endpoints = (
                credential.get("wireguard_endpoints")
                or credential.get("endpoints")
                or credential.get("endpoint")
                or credential.get("Endpoint")
            )
            if endpoints:
                # Gluetun expects pluralized endpoint variable.
                env["WIREGUARD_ENDPOINTS"] = str(endpoints)
        else:
            username = credential.get("openvpn_user") or credential.get("username") or credential.get("user")
            password = credential.get("openvpn_password") or credential.get("password") or credential.get("pass")
            if not username or not password:
                raise ValueError("openvpn credential is missing username/password")
            env["OPENVPN_USER"] = str(username)
            env["OPENVPN_PASSWORD"] = str(password)

    def _apply_region_env(
        self,
        *,
        env: Dict[str, str],
        provider: str,
        regions: List[str],
        credential: Dict[str, Any],
    ):
        countries: List[str] = []
        cities: List[str] = []
        server_regions: List[str] = []
        hostnames: List[str] = []

        countries.extend(self._normalize_list(credential.get("server_countries")))
        cities.extend(self._normalize_list(credential.get("server_cities")))
        server_regions.extend(self._normalize_list(credential.get("server_regions")))
        hostnames.extend(self._normalize_list(credential.get("server_hostnames")))

        raw_regions = list(regions or [])
        unqualified: List[str] = []

        for region in raw_regions:
            if ":" not in region:
                unqualified.append(region)
                continue
            prefix, value = region.split(":", 1)
            value = value.strip()
            if not value:
                continue

            tag = prefix.strip().lower()
            if tag in {"country", "countries"}:
                countries.append(value)
            elif tag in {"city", "cities"}:
                cities.append(value)
            elif tag in {"region", "regions"}:
                server_regions.append(value)
            elif tag in {"hostname", "hostnames", "server"}:
                hostnames.append(value)
            else:
                unqualified.append(region)

        if unqualified:
            preferred_key = REGION_DEFAULTS_BY_PROVIDER.get(provider, "SERVER_COUNTRIES")
            if preferred_key == "SERVER_REGIONS":
                server_regions.extend(unqualified)
            else:
                countries.extend(unqualified)

        if countries:
            env["SERVER_COUNTRIES"] = ",".join(dict.fromkeys(countries))
        if cities:
            env["SERVER_CITIES"] = ",".join(dict.fromkeys(cities))
        if server_regions:
            env["SERVER_REGIONS"] = ",".join(dict.fromkeys(server_regions))
        if hostnames:
            env["SERVER_HOSTNAMES"] = ",".join(dict.fromkeys(hostnames))

    def _apply_port_forwarding_env(
        self,
        *,
        env: Dict[str, str],
        provider: str,
        settings: Dict[str, Any],
        credential: Dict[str, Any],
        port_forwarding_supported: bool,
    ):
        enabled = self._coerce_bool(
            credential.get("vpn_port_forwarding")
            if credential.get("vpn_port_forwarding") is not None
            else settings.get("vpn_port_forwarding")
        )

        p2p_enabled = self._coerce_bool(
            credential.get("p2p_forwarding_enabled")
            if credential.get("p2p_forwarding_enabled") is not None
            else settings.get("p2p_forwarding_enabled")
        )

        explicit_pref = credential.get("vpn_port_forwarding")
        if explicit_pref is None:
            explicit_pref = settings.get("vpn_port_forwarding")

        normalized_provider = str(provider or "").strip().lower()
        credential_supported = self.credential_supports_port_forwarding(credential)
        provider_supported = self.provider_supports_port_forwarding(normalized_provider)
        normalized_supported = bool(
            port_forwarding_supported and credential_supported and provider_supported
        )

        if explicit_pref is not None:
            requested = self._coerce_bool(explicit_pref)
        else:
            requested = bool(enabled or p2p_enabled or normalized_supported)

        should_enable = bool(requested and normalized_supported)
        env["VPN_PORT_FORWARDING"] = "on" if should_enable else "off"

        if not should_enable:
            if requested and not normalized_supported:
                if not credential_supported:
                    logger.info(
                        "Port forwarding disabled for credential '%s' because credential-level support is off",
                        str(credential.get("id") or "unknown"),
                    )
                elif not provider_supported:
                    logger.info(
                        "Port forwarding disabled for provider '%s' because native support is unavailable",
                        normalized_provider or provider,
                    )
            return

        env.setdefault("VPN_PORT_FORWARDING_PROVIDER", normalized_provider)

        custom_pf_provider = credential.get("vpn_port_forwarding_provider") or settings.get("vpn_port_forwarding_provider")
        if custom_pf_provider:
            env["VPN_PORT_FORWARDING_PROVIDER"] = str(custom_pf_provider).strip().lower()

        if normalized_provider == "private internet access":
            username = credential.get("vpn_port_forwarding_username") or credential.get("openvpn_user") or credential.get("username")
            password = credential.get("vpn_port_forwarding_password") or credential.get("openvpn_password") or credential.get("password")
            if username:
                env["VPN_PORT_FORWARDING_USERNAME"] = str(username)
            if password:
                env["VPN_PORT_FORWARDING_PASSWORD"] = str(password)
            
            # If no explicit server pin is used, let Gluetun filter for PF-capable servers.
            # If a pinning is present, we omit this to prevent validation errors against
            # Gluetun's internal (potentially stale) catalog.
            if not env.get("SERVER_HOSTNAMES") and not env.get("WIREGUARD_ENDPOINTS"):
                env.setdefault("PORT_FORWARD_ONLY", "true")

        if normalized_provider == "protonvpn":
            if not env.get("SERVER_HOSTNAMES") and not env.get("WIREGUARD_ENDPOINTS"):
                env.setdefault("PORT_FORWARD_ONLY", "on")

    def _apply_port_forwarding_filter_guard(
        self,
        *,
        env: Dict[str, str],
        provider: str,
        protocol: str,
    ):
        """Drop explicit server pinning only when it conflicts with port-forward-only selection.

        Handles both hostname-pinned and IP-pinned endpoints (WireGuard .conf files
        typically contain raw IP:port endpoints). IP tokens are resolved to their
        canonical hostnames via the catalog's ``ips`` array before compatibility
        checking, so a credential pinned to 79.127.139.162:51820 correctly maps
        to node-es-12.protonvpn.net without being dropped.
        """
        if str(env.get("VPN_PORT_FORWARDING") or "").strip().lower() != "on":
            return

        if not self.provider_supports_port_forwarding(provider):
            return

        explicit_hostnames = self._extract_explicit_hostnames(env=env, protocol=protocol)
        if explicit_hostnames:
            servers = vpn_reputation_manager._provider_servers_from_catalog(provider)
            normalized_protocol = vpn_reputation_manager._normalize_protocol(protocol)

            compatible_hostnames: List[str] = []
            for raw_token in explicit_hostnames:
                # If the token looks like an IP address, resolve it to the catalog
                # hostname via the server's ips[] array so we can verify PF support.
                lookup_hostname = (
                    self._ip_to_catalog_hostname(raw_token, servers, normalized_protocol)
                    if self._looks_like_ip(raw_token)
                    else raw_token
                )

                for server in servers or []:
                    server_hostname = str(server.get("hostname") or "").strip().lower()
                    if not server_hostname or server_hostname != lookup_hostname:
                        continue
                    server_protocol = vpn_reputation_manager._normalize_protocol(server.get("vpn"))
                    if normalized_protocol and server_protocol and server_protocol != normalized_protocol:
                        continue
                    if normalized_protocol and not server_protocol:
                        continue
                    if not vpn_reputation_manager._server_supports_port_forwarding(server):
                        continue
                    # Pin by canonical hostname so Gluetun can resolve it for PF.
                    compatible_hostnames.append(server_hostname)
                    break

            if compatible_hostnames:
                env["SERVER_HOSTNAMES"] = ",".join(dict.fromkeys(compatible_hostnames))
                # Drop raw IP endpoint env vars — Gluetun will use SERVER_HOSTNAMES instead.
                if protocol == "wireguard":
                    for key in ("WIREGUARD_ENDPOINTS", "WIREGUARD_ENDPOINT_IP", "WIREGUARD_ENDPOINT_PORT"):
                        env.pop(key, None)
                else:
                    for key in ("OPENVPN_ENDPOINT_IP", "OPENVPN_ENDPOINT_PORT"):
                        env.pop(key, None)
                logger.info(
                    "Resolved explicit server pin for provider '%s' to PF-capable hostname(s): %s",
                    provider,
                    ",".join(dict.fromkeys(compatible_hostnames)),
                )
                return
            # If none remain, fall through to drop pinning

        # No explicit hostnames or none resolved to a PF-capable catalog entry.
        keys_to_clear = ["SERVER_HOSTNAMES"]
        if protocol == "wireguard":
            keys_to_clear.extend(["WIREGUARD_ENDPOINTS", "WIREGUARD_ENDPOINT_IP", "WIREGUARD_ENDPOINT_PORT"])
        else:
            keys_to_clear.extend(["OPENVPN_ENDPOINT_IP", "OPENVPN_ENDPOINT_PORT"])

        cleared_keys = [key for key in keys_to_clear if key in env]
        for key in cleared_keys:
            env.pop(key, None)

        if cleared_keys:
            logger.info(
                "Dropped explicit server pinning (%s) for provider '%s' because VPN_PORT_FORWARDING=on "
                "(no compatible forwarding-capable hostnames in catalog)",
                ",".join(cleared_keys),
                provider,
            )

    @staticmethod
    def _looks_like_ip(token: str) -> bool:
        """Return True when token is an IPv4 or IPv6 address (not a hostname)."""
        import re
        # IPv4
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", token):
            return True
        # IPv6 (contains two or more colons)
        if token.count(":") >= 2:
            return True
        return False

    @staticmethod
    def _ip_to_catalog_hostname(
        ip: str,
        servers: List[Dict[str, object]],
        normalized_protocol: Optional[str],
    ) -> str:
        """Resolve a raw IP to the canonical hostname from the catalog's ips[] field.

        Returns the original IP string unchanged when no match is found so the
        caller can still attempt a hostname comparison (which will simply fail).
        """
        for server in servers or []:
            server_protocol = vpn_reputation_manager._normalize_protocol(server.get("vpn"))
            if normalized_protocol and server_protocol and server_protocol != normalized_protocol:
                continue
            if normalized_protocol and not server_protocol:
                continue
            ips = server.get("ips")
            if not isinstance(ips, list):
                continue
            if ip in [str(i).strip().lower() for i in ips]:
                resolved = str(server.get("hostname") or "").strip().lower()
                if resolved:
                    logger.debug(
                        "Resolved endpoint IP %s to catalog hostname %s", ip, resolved
                    )
                    return resolved
        return ip

    def _extract_explicit_hostnames(self, *, env: Dict[str, str], protocol: str) -> List[str]:
        hostnames = self._normalize_list(env.get("SERVER_HOSTNAMES"))

        if protocol == "wireguard":
            endpoints = self._normalize_list(env.get("WIREGUARD_ENDPOINTS"))
            for endpoint in endpoints:
                token = str(endpoint or "").strip().lower()
                if not token:
                    continue

                if token.startswith("["):
                    closing = token.find("]")
                    if closing > 1:
                        hostnames.append(token[1:closing])
                        continue

                if token.count(":") == 1:
                    host_part, port_part = token.rsplit(":", 1)
                    if port_part.isdigit() and host_part.strip():
                        hostnames.append(host_part.strip())
                        continue

                hostnames.append(token)

        return list(dict.fromkeys([str(item).strip().lower() for item in hostnames if str(item).strip()]))

    @staticmethod
    def _apply_optional_credential_env(*, env: Dict[str, str], protocol: str, credential: Dict[str, Any]):
        if protocol == "wireguard":
            optional_map = {
                "wireguard_public_key": "WIREGUARD_PUBLIC_KEY",
                "wireguard_preshared_key": "WIREGUARD_PRESHARED_KEY",
                "wireguard_endpoint_ip": "WIREGUARD_ENDPOINT_IP",
                "endpoint_ip": "WIREGUARD_ENDPOINT_IP",
                "wireguard_endpoint_port": "WIREGUARD_ENDPOINT_PORT",
                "endpoint_port": "WIREGUARD_ENDPOINT_PORT",
                "wireguard_allowed_ips": "WIREGUARD_ALLOWED_IPS",
                "wireguard_implementation": "WIREGUARD_IMPLEMENTATION",
                "wireguard_mtu": "WIREGUARD_MTU",
                "wireguard_persistent_keepalive_interval": "WIREGUARD_PERSISTENT_KEEPALIVE_INTERVAL",
            }
        else:
            optional_map = {
                "openvpn_protocol": "OPENVPN_PROTOCOL",
                "openvpn_endpoint_ip": "OPENVPN_ENDPOINT_IP",
                "endpoint_ip": "OPENVPN_ENDPOINT_IP",
                "openvpn_endpoint_port": "OPENVPN_ENDPOINT_PORT",
                "endpoint_port": "OPENVPN_ENDPOINT_PORT",
                "openvpn_version": "OPENVPN_VERSION",
                "openvpn_ciphers": "OPENVPN_CIPHERS",
                "openvpn_auth": "OPENVPN_AUTH",
            }

        for source_key, env_key in optional_map.items():
            value = credential.get(source_key)
            if value is None or str(value).strip() == "":
                continue
            env[env_key] = str(value)

    @staticmethod
    def _build_labels(
        *,
        provider: str,
        protocol: str,
        credential_id: str,
        port_forwarding_supported: bool,
    ) -> Dict[str, str]:
        labels = {
            "acestream-orchestrator.managed": "true",
            "role": "vpn_node",
            "acestream.vpn.provider": provider,
            "acestream.vpn.protocol": protocol,
            "acestream.vpn.port_forwarding_supported": "true" if port_forwarding_supported else "false",
        }
        if credential_id:
            labels["acestream.vpn.credential_id"] = credential_id
        return labels

    @staticmethod
    def _build_runtime_privileges(
        *,
        protocol: str,
        settings: Dict[str, Any],
        credential: Dict[str, Any],
    ) -> tuple[List[str], List[str], Dict[str, Dict[str, str]]]:
        cap_add = ["NET_ADMIN"]
        devices = ["/dev/net/tun:/dev/net/tun"]
        volumes: Dict[str, Dict[str, str]] = {}

        require_wireguard_module = False
        if protocol == "wireguard":
            require_wireguard_module = bool(
                VPNProvisioner._coerce_bool(settings.get("wireguard_kernel_module"))
                or VPNProvisioner._coerce_bool(credential.get("wireguard_kernel_module"))
            )

        if require_wireguard_module:
            cap_add.append("SYS_MODULE")
            volumes["/lib/modules"] = {"bind": "/lib/modules", "mode": "ro"}

        # Share the orchestrator's refreshed servers.json catalog with Gluetun
        # via a named Docker volume (no host paths required).  Gluetun reads its
        # servers list from /gluetun/ on startup; mounting our volume there
        # ensures it validates SERVER_HOSTNAMES against our up-to-date data
        # rather than the potentially stale catalog bundled in its Docker image.
        # Use 'rw' mode because Gluetun attempts to write metadata to this
        # directory on startup; 'ro' results in "read-only file system" warnings.
        volumes[gluetun_servers_volume.VOLUME_NAME] = {
            "bind": "/gluetun",
            "mode": "rw",
        }

        return cap_add, devices, volumes

    @staticmethod
    def _run_container_sync(
        image: str,
        container_name: str,
        env: Dict[str, str],
        labels: Dict[str, str],
        network: Optional[str],
        cap_add: List[str],
        devices: List[str],
        volumes: Dict[str, Dict[str, str]],
    ):
        cli = get_client(timeout=30)
        try:
            kwargs: Dict[str, Any] = {
                "image": image,
                "detach": True,
                "name": container_name,
                "environment": env,
                "labels": labels,
                "cap_add": cap_add,
                "devices": devices,
                "restart_policy": {"Name": "unless-stopped"},
            }
            if network:
                kwargs["network"] = network
            if volumes:
                kwargs["volumes"] = volumes

            return cli.containers.run(**kwargs)
        finally:
            with suppress(Exception):
                cli.close()

    @staticmethod
    def _resolve_container_name_sync(container_ref: str) -> Optional[str]:
        cli = get_client(timeout=30)
        try:
            container = cli.containers.get(container_ref)
            return str(container.name or "").strip() or None
        except Exception:
            return None
        finally:
            with suppress(Exception):
                cli.close()

    @staticmethod
    def _destroy_container_sync(container_ref: str, force: bool) -> bool:
        cli = get_client(timeout=30)
        try:
            container = cli.containers.get(container_ref)
            container.remove(force=force)
            return True
        except NotFound:
            return False
        except APIError as e:
            logger.error("Failed to remove VPN container %s: %s", container_ref, e)
            raise
        finally:
            with suppress(Exception):
                cli.close()

    @staticmethod
    def _list_managed_nodes_sync(include_stopped: bool) -> List[Dict[str, Any]]:
        cli = get_client(timeout=30)
        try:
            containers = cli.containers.list(
                all=include_stopped,
                filters={"label": ["acestream-orchestrator.managed=true", "role=vpn_node"]},
            )

            nodes: List[Dict[str, Any]] = []
            for container in containers:
                labels = container.labels or {}
                nodes.append(
                    {
                        "container_id": container.id,
                        "container_name": container.name,
                        "status": container.status,
                        "provider": labels.get("acestream.vpn.provider"),
                        "protocol": labels.get("acestream.vpn.protocol"),
                        "credential_id": labels.get("acestream.vpn.credential_id"),
                        "port_forwarding_supported": str(
                            labels.get("acestream.vpn.port_forwarding_supported", "false")
                        ).strip().lower() == "true",
                    }
                )
            return nodes
        finally:
            with suppress(Exception):
                cli.close()

    @staticmethod
    def _normalize_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}


vpn_provisioner = VPNProvisioner()
