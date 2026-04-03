from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .provisioner import stop_container
from .settings_persistence import SettingsPersistence
from .state import state
from .vpn_credentials import credential_manager
from .vpn_provisioner import vpn_provisioner

logger = logging.getLogger(__name__)


class VPNController:
    """Reconciliation loop for dynamic VPN nodes."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._reconcile_signal = asyncio.Event()
        self._thread_signal = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._interval_s = max(2, int(os.getenv("VPN_CONTROLLER_INTERVAL_S", "5")))
        self._notready_heal_grace_s = max(0, int(os.getenv("VPN_NOTREADY_HEAL_GRACE_S", "45")))
        self._active_healings: set[str] = set()

    async def start(self):
        if self._task and not self._task.done():
            return

        self._stop.clear()
        self._reconcile_signal.clear()
        self._thread_signal.clear()
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())
        logger.info("VPN controller started (interval=%ss)", self._interval_s)

    async def stop(self):
        self._stop.set()
        self._reconcile_signal.set()
        self._thread_signal.set()
        if self._task:
            await self._task
        logger.info("VPN controller stopped")

    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    def request_reconcile(self, reason: str = "manual"):
        logger.debug("VPN controller reconcile requested: %s", reason)
        self._thread_signal.set()
        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._reconcile_signal.set)
            except RuntimeError:
                pass

    async def _run(self):
        logger.info("Adopting orphaned VPN leases from Docker on controller startup")
        try:
            startup_nodes = await vpn_provisioner.list_managed_nodes(include_stopped=True)
            await credential_manager.restore_leases(startup_nodes)
        except Exception as e:
            logger.error("Failed to adopt startup VPN leases: %s", e)

        self.request_reconcile(reason="startup")

        while not self._stop.is_set():
            if self._thread_signal.is_set():
                self._thread_signal.clear()

            await self._reconcile_once()

            stop_task = asyncio.create_task(self._stop.wait())
            signal_task = asyncio.create_task(self._reconcile_signal.wait())
            done, pending = await asyncio.wait(
                {stop_task, signal_task},
                timeout=self._interval_s,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()

            if stop_task in done:
                break

            self._reconcile_signal.clear()

    async def _reconcile_once(self):
        settings = SettingsPersistence.load_vpn_config() or {}
        vpn_enabled = bool(settings.get("enabled", False))
        if not vpn_enabled:
            state.set_desired_vpn_node_count(0)
            return

        current_nodes = await vpn_provisioner.list_managed_nodes(include_stopped=True)
        lease_summary = await credential_manager.summary()
        total_credentials = int(lease_summary.get("total_credentials") or 0)

        self._sync_dynamic_nodes_to_state(current_nodes)

        preferred_engines_per_vpn = self._get_preferred_engines_per_vpn(settings)
        total_engines = len(state.list_engines())
        desired_engines = max(0, int(state.get_desired_replica_count()))
        # Bootstrap dynamic VPN nodes from desired engine demand as well.
        # Using only existing engines causes a startup deadlock:
        # no engines -> no VPN nodes -> engine provisioning blocked.
        engine_demand = max(total_engines, desired_engines)
        required_vpns = 0
        if engine_demand > 0 and preferred_engines_per_vpn > 0:
            required_vpns = math.ceil(engine_demand / preferred_engines_per_vpn)

        desired_vpns = min(required_vpns, total_credentials)
        state.set_desired_vpn_node_count(desired_vpns)

        logger.debug(
            "VPN controller desired nodes computed "
            "(engines=%s, desired_engines=%s, demand=%s, preferred=%s, credentials=%s, desired_vpns=%s)",
            total_engines,
            desired_engines,
            engine_demand,
            preferred_engines_per_vpn,
            total_credentials,
            desired_vpns,
        )

        await self._heal_notready_nodes()

        running_nodes = await vpn_provisioner.list_managed_nodes(include_stopped=False)
        actual_vpns = len(running_nodes)

        if actual_vpns < desired_vpns:
            deficit = desired_vpns - actual_vpns
            for _ in range(deficit):
                await self._provision_one(settings)

        elif actual_vpns > desired_vpns:
            await self._scale_down_idle_nodes(running_nodes=running_nodes, desired_vpns=desired_vpns)

    def _sync_dynamic_nodes_to_state(self, nodes: List[Dict[str, object]]):
        observed_names: set[str] = set()
        for node in nodes:
            name = str(node.get("container_name") or "").strip()
            if not name:
                continue
            observed_names.add(name)
            state.update_vpn_node_status(
                name,
                str(node.get("status") or "running"),
                metadata={
                    "managed_dynamic": True,
                    "provider": node.get("provider"),
                    "protocol": node.get("protocol"),
                    "credential_id": node.get("credential_id"),
                    "port_forwarding_supported": bool(node.get("port_forwarding_supported", False)),
                },
            )

        known_dynamic = state.list_dynamic_vpn_nodes()
        for node in known_dynamic:
            name = str(node.get("container_name") or "").strip()
            if not name or name in observed_names:
                continue
            state.update_vpn_node_status(name, "down", metadata={"managed_dynamic": True})

    @staticmethod
    def _get_preferred_engines_per_vpn(settings: Dict[str, object]) -> int:
        raw_value = settings.get("preferred_engines_per_vpn")
        if raw_value is None:
            raw_value = os.getenv("PREFERRED_ENGINES_PER_VPN", "4")

        try:
            value = int(raw_value)
            return max(1, value)
        except (TypeError, ValueError):
            return 4

    async def _provision_one(self, settings: Dict[str, object]):
        logger.info("Provisioning dynamic VPN node")
        intent = state.emit_scaling_intent(
            intent_type="create_vpn_request",
            details={
                "requested_by": "vpn_controller",
                "dynamic_vpn_management": True,
            },
        )

        try:
            result = await vpn_provisioner.provision_node(settings)
            logger.info(
                "Dynamic VPN node provisioned: %s",
                str(result.get("container_name") or "unknown"),
            )
            state.resolve_scaling_intent(intent["id"], "completed", result={"container_name": result.get("container_name")})
            # Nudge engine controller immediately so blocked create intents can retry
            # without waiting for the next autoscaler interval.
            try:
                from .autoscaler import engine_controller

                engine_controller.request_reconcile(
                    reason=f"vpn_node_provisioned:{str(result.get('container_name') or 'unknown')}"
                )
            except Exception as e:
                logger.debug("Failed to request engine reconcile after VPN provision: %s", e)
        except Exception as e:
            logger.error("Failed to provision dynamic VPN node: %s", e)
            state.resolve_scaling_intent(intent["id"], "failed", result={"error": str(e)})

    async def _heal_notready_nodes(self):
        candidates = state.list_notready_vpn_nodes(dynamic_only=True)
        now = datetime.now(timezone.utc)
        for node in candidates:
            name = str(node.get("container_name") or "").strip()
            if not name or name in self._active_healings:
                continue

            last_event_at_raw = node.get("last_event_at")
            last_event_at: Optional[datetime] = None
            if isinstance(last_event_at_raw, datetime):
                last_event_at = last_event_at_raw
            elif isinstance(last_event_at_raw, str):
                try:
                    last_event_at = datetime.fromisoformat(last_event_at_raw)
                except ValueError:
                    last_event_at = None

            if last_event_at is not None:
                if last_event_at.tzinfo is None:
                    last_event_at = last_event_at.replace(tzinfo=timezone.utc)
                age_s = (now - last_event_at).total_seconds()
                if age_s < self._notready_heal_grace_s:
                    logger.debug(
                        "Skipping NotReady heal for '%s' during startup grace (age=%.1fs < grace=%ss)",
                        name,
                        age_s,
                        self._notready_heal_grace_s,
                    )
                    continue

            self._active_healings.add(name)
            try:
                await self._drain_and_destroy_node(name, reason="node_not_ready")
            finally:
                self._active_healings.discard(name)

    async def _scale_down_idle_nodes(self, *, running_nodes: List[Dict[str, object]], desired_vpns: int):
        active_names = [str(node.get("container_name") or "").strip() for node in running_nodes]
        active_names = [name for name in active_names if name]
        if len(active_names) <= desired_vpns:
            return

        # Only remove idle nodes to avoid unnecessary stream interruption.
        removable: List[str] = []
        for name in active_names:
            if len(state.get_engines_by_vpn(name)) == 0:
                removable.append(name)

        excess = len(active_names) - desired_vpns
        for name in removable[:excess]:
            await self._drain_and_destroy_node(name, reason="scale_down_idle")

    async def _drain_and_destroy_node(self, vpn_container: str, *, reason: str):
        engines = list(state.get_engines_by_vpn(vpn_container))

        eviction_jobs: List[Dict[str, Any]] = []
        for engine in engines:
            intent = state.emit_scaling_intent(
                intent_type="terminate_request",
                details={
                    "requested_by": "vpn_controller",
                    "eviction_reason": reason,
                    "vpn_container": vpn_container,
                    "container_id": engine.container_id,
                    "force": True,
                },
            )
            eviction_jobs.append({"container_id": engine.container_id, "intent_id": intent["id"]})

        if eviction_jobs:
            eviction_results = await asyncio.gather(
                *[asyncio.to_thread(stop_container, job["container_id"], True) for job in eviction_jobs],
                return_exceptions=True,
            )

            for job, result in zip(eviction_jobs, eviction_results):
                container_id = str(job["container_id"])
                intent_id = str(job["intent_id"])
                if isinstance(result, Exception):
                    state.resolve_scaling_intent(intent_id, "failed", result={"error": str(result)})
                    logger.warning("Failed evicting engine %s from VPN node %s: %s", container_id, vpn_container, result)
                    continue
                state.resolve_scaling_intent(intent_id, "completed", result={"stopped": container_id})

        # Release credential lease as part of node destruction before any replacement provisioning.
        destroy_intent = state.emit_scaling_intent(
            intent_type="destroy_vpn_request",
            details={
                "requested_by": "vpn_controller",
                "vpn_container": vpn_container,
                "reason": reason,
            },
        )

        try:
            destroy_result = await vpn_provisioner.destroy_node(vpn_container, release_credential=True, force=True)
            state.resolve_scaling_intent(destroy_intent["id"], "completed", result=destroy_result)
        except Exception as e:
            logger.error("Failed destroying dynamic VPN node %s: %s", vpn_container, e)
            state.resolve_scaling_intent(destroy_intent["id"], "failed", result={"error": str(e)})


vpn_controller = VPNController()
