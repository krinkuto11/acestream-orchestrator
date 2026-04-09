from __future__ import annotations

import logging
import random
import re
from contextlib import suppress
from typing import Dict, List, Optional

from . import docker_client
from ..proxy import manager

logger = logging.getLogger(__name__)


PROVIDER_FLAG_ALIASES = {
    "private internet access": "pia",
    "privateinternetaccess": "pia",
}


class VPNReputationManager:
    """Manage VPN hostname reputation backed by Proxy Redis."""

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

    def get_safe_hostname(self, provider: str, regions: List[str]) -> Optional[str]:
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