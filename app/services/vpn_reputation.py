from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
from contextlib import suppress
from pathlib import Path
from typing import Dict, List, Optional

from . import docker_client
from ..proxy import manager

logger = logging.getLogger(__name__)


PROVIDER_FLAG_ALIASES = {
    "private internet access": "pia",
    "privateinternetaccess": "pia",
}

PROVIDER_STORAGE_ALIASES = {
    "pia": "private internet access",
    "privateinternetaccess": "private internet access",
    "private_internet_access": "private internet access",
}


class VPNReputationManager:
    """Manage VPN hostname reputation backed by Proxy Redis."""

    def __init__(self) -> None:
        self._cache_lock = threading.Lock()
        # Cache structure: { absolute_path: { "mtime": float, "data": Dict, "index": Dict[str, List] } }
        self._catalogs: Dict[Path, Dict[str, Any]] = {}

    def _get_proxy_redis_client(self):
        try:
            return manager.ProxyManager.get_instance().redis_client
        except Exception as e:
            logger.debug("Unable to acquire Proxy Redis client: %s", e)
            return None

    @staticmethod
    def _blacklist_key(hostname: str) -> str:
        return f"ace_proxy:blacklist:vpn:{hostname}"

    def blacklist_hostname(self, hostname: str, ttl_seconds: int = 86400):
        normalized = str(hostname or "").strip().lower()
        if not normalized:
            return

        redis_client = self._get_proxy_redis_client()
        if not redis_client:
            logger.warning("Cannot blacklist VPN hostname %s: Proxy Redis unavailable", normalized)
            return

        ttl = max(1, int(ttl_seconds or 86400))
        key = self._blacklist_key(normalized)
        redis_client.setex(key, ttl, "burned")
        logger.info("Blacklisted VPN hostname %s for %ss", normalized, ttl)

    def is_blacklisted(self, hostname: str) -> bool:
        normalized = str(hostname or "").strip().lower()
        if not normalized:
            return False

        redis_client = self._get_proxy_redis_client()
        if not redis_client:
            return False

        key = self._blacklist_key(normalized)
        try:
            return bool(redis_client.exists(key))
        except Exception as e:
            logger.warning("Failed to query blacklist status for hostname %s: %s", normalized, e)
            return False

    @staticmethod
    def _normalize_provider_flag(provider: str) -> str:
        normalized = str(provider or "").strip().lower()
        if not normalized:
            return ""
        if normalized in PROVIDER_FLAG_ALIASES:
            return PROVIDER_FLAG_ALIASES[normalized]
        compact = re.sub(r"[^a-z0-9]", "", normalized)
        return PROVIDER_FLAG_ALIASES.get(compact, compact)

    @staticmethod
    def _normalize_provider_storage(provider: str) -> str:
        normalized = str(provider or "").strip().lower()
        if not normalized:
            return ""
        if normalized in PROVIDER_STORAGE_ALIASES:
            return PROVIDER_STORAGE_ALIASES[normalized]
        compact = re.sub(r"[^a-z0-9]", "", normalized)
        return PROVIDER_STORAGE_ALIASES.get(compact, normalized)

    @staticmethod
    def _normalize_regions(regions: List[str]) -> List[str]:
        normalized: List[str] = []
        for region in regions or []:
            value = str(region or "").strip().lower()
            if not value:
                continue
            if ":" in value:
                _, suffix = value.split(":", 1)
                value = suffix.strip()
            if value:
                normalized.append(value)
        return normalized

    @staticmethod
    def _normalize_protocol(protocol: Optional[object]) -> Optional[str]:
        value = str(protocol or "").strip().lower()
        if value in {"wireguard", "openvpn"}:
            return value
        return None

    @staticmethod
    def _coerce_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "on"}

    @staticmethod
    def _extract_endpoint_hostname(endpoint: str) -> str:
        token = str(endpoint or "").strip().lower().strip("`")
        if not token:
            return ""

        if token.startswith("["):
            end_index = token.find("]")
            if end_index > 1:
                return token[1:end_index]

        if token.count(":") == 1:
            host_part, port_part = token.rsplit(":", 1)
            if port_part.isdigit():
                return host_part.strip()

        return token

    def _servers_json_path(self, filename: str = "servers.json") -> Path:
        configured = str(os.getenv("GLUETUN_SERVERS_JSON_PATH", "")).strip()
        if configured:
            base = Path(configured)
            if base.suffix == ".json":
                # If a specific file is forced via env, we respect it only for 'servers.json'
                if filename == "servers.json":
                    return base
                return base.parent / filename
            return base / filename

        return Path(__file__).resolve().parents[2] / filename

    def _load_servers_catalog(self, filename: str = "servers.json") -> Optional[Dict[str, Any]]:
        path = self._servers_json_path(filename)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            if filename != "servers.json":
                # Fallback to main servers.json if specific mode file is missing
                return self._load_servers_catalog("servers.json")
            logger.warning("VPN servers catalog not found at %s", path)
            return None

        with self._cache_lock:
            cached = self._catalogs.get(path)
            if cached and cached.get("mtime") == mtime:
                return cached["data"]

        try:
            with path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
        except Exception as exc:
            logger.warning("Failed to parse VPN servers catalog at %s: %s", path, exc)
            return None

        if not isinstance(loaded, dict):
            logger.warning("VPN servers catalog at %s is not a JSON object", path)
            return None

        # Build provider index
        index: Dict[str, List[Dict[str, Any]]] = {}
        for key, section in loaded.items():
            if key == "version" or not isinstance(section, dict):
                continue
            servers = section.get("servers")
            if isinstance(servers, list):
                index[key] = [s for s in servers if isinstance(s, dict)]

        with self._cache_lock:
            self._catalogs[path] = {
                "mtime": mtime,
                "data": loaded,
                "index": index,
            }

        # Log catalog metadata whenever the file is freshly (re-)loaded.
        provider_count = len(index)
        total_servers = sum(len(s) for s in index.values())
        logger.info(
            "Loaded VPN servers catalog: path=%s providers=%d total_servers=%d",
            path.name,
            provider_count,
            total_servers,
        )

        return loaded

    def _provider_servers_from_catalog(
        self, provider: str, catalog_filename: str = "servers.json"
    ) -> List[Dict[str, Any]]:
        # Ensure loaded/indexed
        catalog = self._load_servers_catalog(catalog_filename)
        if not catalog:
            return []

        provider_key = self._normalize_provider_storage(provider)
        if not provider_key:
            return []

        # Find the path for the effective catalog (might have fallen back to servers.json)
        path = self._servers_json_path(catalog_filename)
        if not path.exists() and catalog_filename != "servers.json":
            path = self._servers_json_path("servers.json")

        with self._cache_lock:
            cached = self._catalogs.get(path)
            if not cached:
                return []
            return cached["index"].get(provider_key) or []

    def _server_supports_port_forwarding(self, server: Dict[str, object]) -> bool:
        if "port_forward" not in server:
            return False
        return self._coerce_bool(server.get("port_forward"))

    def _server_matches_regions(self, server: Dict[str, object], requested_regions: List[str]) -> bool:
        if not requested_regions:
            return True

        country = str(server.get("country") or "").strip().lower()
        city = str(server.get("city") or "").strip().lower()
        region = str(server.get("region") or "").strip().lower()
        server_name = str(server.get("server_name") or "").strip().lower()
        hostname = str(server.get("hostname") or "").strip().lower()

        searchable = [country, city, region, server_name, hostname]

        for requested in requested_regions:
            if any(
                requested == value
                or requested in value
                for value in searchable
                if value
            ):
                return True
        return False

    def _candidate_hostnames_from_catalog(
        self,
        *,
        provider: str,
        regions: List[str],
        protocol: Optional[str],
        require_port_forwarding: bool,
        catalog_filename: str = "servers.json",
    ) -> List[str]:
        servers = self._provider_servers_from_catalog(provider, catalog_filename)
        if not servers:
            logger.warning(
                "Catalog filter: provider=%s — no servers found for this provider in catalog",
                provider,
            )
            return []

        normalized_protocol = self._normalize_protocol(protocol)
        requested_regions = self._normalize_regions(regions)

        total = len(servers)
        skipped_no_hostname = 0
        skipped_protocol = 0
        skipped_pf = 0
        skipped_region = 0
        candidates: List[str] = []

        for server in servers:
            hostname = str(server.get("hostname") or "").strip().lower()
            if not hostname:
                skipped_no_hostname += 1
                continue

            server_protocol = self._normalize_protocol(server.get("vpn"))
            if normalized_protocol and server_protocol and server_protocol != normalized_protocol:
                skipped_protocol += 1
                continue
            if normalized_protocol and not server_protocol:
                skipped_protocol += 1
                continue

            if require_port_forwarding and not self._server_supports_port_forwarding(server):
                skipped_pf += 1
                continue

            if not self._server_matches_regions(server, requested_regions):
                skipped_region += 1
                continue

            candidates.append(hostname)

        unique_candidates = list(dict.fromkeys(candidates))

        logger.info(
            "Catalog filter: provider=%s protocol=%s pf_required=%s regions=%s "
            "→ total=%d skipped(no_hostname=%d protocol=%d pf=%d region=%d) candidates=%d",
            provider,
            normalized_protocol or "any",
            require_port_forwarding,
            requested_regions or ["any"],
            total,
            skipped_no_hostname,
            skipped_protocol,
            skipped_pf,
            skipped_region,
            len(unique_candidates),
        )

        return unique_candidates

    def hostnames_support_port_forwarding(
        self,
        *,
        provider: str,
        protocol: Optional[str],
        hostnames: List[str],
        require_port_forwarding: bool,
        catalog_filename: str = "servers.json",
    ) -> Optional[bool]:
        """
        Return whether all explicit hostnames are compatible with the requested filters.

        Returns None when catalog data is unavailable for the provider/protocol.
        """
        normalized_hosts = {
            self._extract_endpoint_hostname(hostname)
            for hostname in hostnames
            if str(hostname or "").strip()
        }
        normalized_hosts = {hostname for hostname in normalized_hosts if hostname}
        if not normalized_hosts:
            return True

        servers = self._provider_servers_from_catalog(provider, catalog_filename)
        if not servers:
            return None

        normalized_protocol = self._normalize_protocol(protocol)
        protocol_hosts: set[str] = set()
        compatible_hosts: set[str] = set()

        for server in servers:
            hostname = str(server.get("hostname") or "").strip().lower()
            if not hostname:
                continue

            server_protocol = self._normalize_protocol(server.get("vpn"))
            if normalized_protocol and server_protocol and server_protocol != normalized_protocol:
                continue
            if normalized_protocol and not server_protocol:
                continue

            protocol_hosts.add(hostname)

            if require_port_forwarding and not self._server_supports_port_forwarding(server):
                continue

            compatible_hosts.add(hostname)

        if not protocol_hosts:
            return None

        return normalized_hosts.issubset(compatible_hosts)

    @staticmethod
    def _is_markdown_separator(cells: List[str]) -> bool:
        if not cells:
            return False
        for cell in cells:
            token = str(cell or "").strip()
            if not token:
                continue
            if not re.fullmatch(r":?-{3,}:?", token):
                return False
        return True

    @classmethod
    def _parse_markdown_table(cls, content: str) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        header: Optional[List[str]] = None

        for line in str(content or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue

            cells = [part.strip() for part in stripped.strip("|").split("|")]
            if not any(cells):
                continue

            if header is None:
                lowered = [cell.lower() for cell in cells]
                if {"country", "city", "hostname"}.issubset(set(lowered)):
                    header = lowered
                continue

            if cls._is_markdown_separator(cells):
                continue

            if len(cells) < len(header):
                continue

            row: Dict[str, str] = {}
            for index, name in enumerate(header):
                row[name] = cells[index].strip()
            rows.append(row)

        return rows

    def _run_format_servers(self, provider: str) -> str:
        provider_flag = self._normalize_provider_flag(provider)
        if not provider_flag:
            return ""

        cli = docker_client.get_client(timeout=45)
        try:
            output = cli.containers.run(
                "qmcgaw/gluetun",
                command=f"format-servers -{provider_flag}",
                remove=True,
            )
            if isinstance(output, bytes):
                return output.decode("utf-8", errors="ignore")
            return str(output or "")
        except Exception as e:
            logger.warning("Failed to execute gluetun format-servers for provider %s: %s", provider, e)
            return ""
        finally:
            with suppress(Exception):
                cli.close()

    def get_safe_hostname(
        self,
        provider: str,
        regions: List[str],
        protocol: Optional[str] = None,
        require_port_forwarding: bool = False,
        catalog_filename: str = "servers.json",
    ) -> Optional[str]:
        catalog_candidates = self._candidate_hostnames_from_catalog(
            provider=provider,
            regions=regions,
            protocol=protocol,
            require_port_forwarding=require_port_forwarding,
            catalog_filename=catalog_filename,
        )
        if catalog_candidates:
            safe_catalog_hostnames = [
                hostname
                for hostname in catalog_candidates
                if not self.is_blacklisted(hostname)
            ]
            if safe_catalog_hostnames:
                return random.choice(safe_catalog_hostnames)
            logger.warning("All catalog hostnames are blacklisted for provider %s", provider)
            return None

        if require_port_forwarding:
            logger.warning(
                "No forwarding-capable hostnames found in servers catalog '%s' for provider %s, "
                "protocol=%s, regions=%s — check catalog path and that servers have "
                "port_forward=true for the requested region/protocol combination",
                catalog_filename,
                provider,
                protocol,
                self._normalize_regions(regions),
            )
            return None

        output = self._run_format_servers(provider)
        if not output:
            return None

        parsed_rows = self._parse_markdown_table(output)
        if not parsed_rows:
            logger.warning("No parseable rows found in gluetun format-servers output for provider %s", provider)
            return None

        requested_regions = self._normalize_regions(regions)

        candidate_hostnames: List[str] = []
        for row in parsed_rows:
            country = str(row.get("country") or "").strip().lower()
            city = str(row.get("city") or "").strip().lower()
            hostname = str(row.get("hostname") or "").strip().lower()

            if not hostname:
                continue

            if requested_regions:
                region_match = any(
                    region == country
                    or region == city
                    or region in country
                    or region in city
                    for region in requested_regions
                )
                if not region_match:
                    continue

            candidate_hostnames.append(hostname)

        if not candidate_hostnames:
            logger.warning(
                "No candidate VPN hostnames found for provider %s with regions=%s",
                provider,
                requested_regions,
            )
            return None

        safe_hostnames = [
            hostname for hostname in dict.fromkeys(candidate_hostnames)
            if not self.is_blacklisted(hostname)
        ]
        if not safe_hostnames:
            logger.warning("All candidate VPN hostnames are blacklisted for provider %s", provider)
            return None

        return random.choice(safe_hostnames)


vpn_reputation_manager = VPNReputationManager()