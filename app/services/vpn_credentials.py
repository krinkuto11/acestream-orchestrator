from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CredentialManager:
    """Manages a finite VPN credential pool with strict lease semantics."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._credentials_by_id: Dict[str, Dict[str, Any]] = {}
        self._available_credential_ids: List[str] = []
        self._leases_by_container: Dict[str, str] = {}
        self._lease_timestamps: Dict[str, datetime] = {}

        self._dynamic_vpn_management: bool = False
        self._providers: List[str] = []
        self._protocol: Optional[str] = None
        self._regions: List[str] = []

    @staticmethod
    def _normalize_protocol(protocol: Optional[str]) -> Optional[str]:
        if protocol is None:
            return None
        value = str(protocol).strip().lower()
        if value in {"wireguard", "openvpn"}:
            return value
        return value or None

    @staticmethod
    def _build_credential_id(index: int, credential: Dict[str, Any]) -> str:
        explicit_id = credential.get("id")
        if explicit_id:
            return str(explicit_id)

        payload = json.dumps(credential, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return f"cred-{index}-{digest}"

    @classmethod
    def _normalize_credentials(cls, credentials: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
        normalized: Dict[str, Dict[str, Any]] = {}
        for index, raw in enumerate(credentials or []):
            if not isinstance(raw, dict):
                continue
            credential = dict(raw)
            cred_id = cls._build_credential_id(index, credential)
            credential["id"] = cred_id
            normalized[cred_id] = credential
        return normalized

    async def configure(
        self,
        *,
        dynamic_vpn_management: Optional[bool] = None,
        providers: Optional[List[str]] = None,
        protocol: Optional[str] = None,
        regions: Optional[List[str]] = None,
        credentials: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Replace or update manager configuration and credential pool atomically."""
        async with self._lock:
            if dynamic_vpn_management is not None:
                self._dynamic_vpn_management = bool(dynamic_vpn_management)
            if providers is not None:
                self._providers = [str(p).strip() for p in providers if str(p).strip()]
            if protocol is not None:
                self._protocol = self._normalize_protocol(protocol)
            if regions is not None:
                self._regions = [str(r).strip() for r in regions if str(r).strip()]

            if credentials is not None:
                new_credentials = self._normalize_credentials(credentials)
                self._credentials_by_id = new_credentials

                active_credential_ids = {
                    cred_id
                    for cred_id in self._leases_by_container.values()
                    if cred_id in self._credentials_by_id
                }

                stale_containers = [
                    container_id
                    for container_id, cred_id in self._leases_by_container.items()
                    if cred_id not in self._credentials_by_id
                ]
                for container_id in stale_containers:
                    self._leases_by_container.pop(container_id, None)
                    self._lease_timestamps.pop(container_id, None)

                self._available_credential_ids = [
                    cred_id
                    for cred_id in self._credentials_by_id.keys()
                    if cred_id not in active_credential_ids
                ]

            return self._snapshot_locked()

    async def acquire_lease(self, container_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Atomically reserve one credential for the provided container id."""
        async with self._lock:
            if not self._available_credential_ids:
                return None

            lease_container_id = container_id or f"pending-{uuid.uuid4()}"
            existing_cred_id = self._leases_by_container.get(lease_container_id)
            if existing_cred_id:
                return {
                    "container_id": lease_container_id,
                    "credential_id": existing_cred_id,
                    "credential": dict(self._credentials_by_id[existing_cred_id]),
                    "leased_at": self._lease_timestamps.get(lease_container_id),
                }

            credential_id = self._available_credential_ids.pop(0)
            self._leases_by_container[lease_container_id] = credential_id
            now = datetime.now(timezone.utc)
            self._lease_timestamps[lease_container_id] = now

            return {
                "container_id": lease_container_id,
                "credential_id": credential_id,
                "credential": dict(self._credentials_by_id[credential_id]),
                "leased_at": now,
            }

    async def release_lease(self, container_id: str) -> bool:
        """Release a credential lease associated with a container id."""
        async with self._lock:
            credential_id = self._leases_by_container.pop(container_id, None)
            self._lease_timestamps.pop(container_id, None)
            if not credential_id:
                return False

            if credential_id in self._credentials_by_id and credential_id not in self._available_credential_ids:
                self._available_credential_ids.append(credential_id)
            return True

    async def get_lease(self, container_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            credential_id = self._leases_by_container.get(container_id)
            if not credential_id or credential_id not in self._credentials_by_id:
                return None
            return {
                "container_id": container_id,
                "credential_id": credential_id,
                "credential": dict(self._credentials_by_id[credential_id]),
                "leased_at": self._lease_timestamps.get(container_id),
            }

    async def restore_leases(self, active_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Rebuild lease state from currently running/managed dynamic VPN nodes.

        This allows the orchestrator to re-adopt leases after restart so
        credentials already attached to live containers are not re-used.
        """
        async with self._lock:
            restored_leases: Dict[str, str] = {}
            restored_timestamps: Dict[str, datetime] = {}
            used_credential_ids: set[str] = set()
            unknown_credential_ids: set[str] = set()

            now = datetime.now(timezone.utc)
            for node in active_nodes or []:
                if not isinstance(node, dict):
                    continue

                credential_id = str(node.get("credential_id") or "").strip()
                if not credential_id:
                    continue
                if credential_id not in self._credentials_by_id:
                    unknown_credential_ids.add(credential_id)
                    continue
                if credential_id in used_credential_ids:
                    logger.warning("Skipping duplicate credential lease adoption for credential_id=%s", credential_id)
                    continue

                container_key = str(node.get("container_name") or node.get("container_id") or "").strip()
                if not container_key:
                    continue

                used_credential_ids.add(credential_id)
                restored_leases[container_key] = credential_id
                restored_timestamps[container_key] = self._lease_timestamps.get(container_key, now)

            self._leases_by_container = restored_leases
            self._lease_timestamps = restored_timestamps
            self._available_credential_ids = [
                cred_id
                for cred_id in self._credentials_by_id.keys()
                if cred_id not in used_credential_ids
            ]

            if unknown_credential_ids:
                logger.warning(
                    "Detected active VPN nodes with unknown credential ids: %s",
                    sorted(unknown_credential_ids),
                )

            return self._snapshot_locked()

    async def summary(self) -> Dict[str, Any]:
        async with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> Dict[str, Any]:
        total_credentials = len(self._credentials_by_id)
        leased_count = len(self._leases_by_container)
        return {
            "dynamic_vpn_management": self._dynamic_vpn_management,
            "providers": list(self._providers),
            "protocol": self._protocol,
            "regions": list(self._regions),
            "total_credentials": total_credentials,
            "max_vpn_capacity": total_credentials,
            "leased": leased_count,
            "available": max(total_credentials - leased_count, 0),
            "leased_container_ids": sorted(self._leases_by_container.keys()),
        }


credential_manager = CredentialManager()
