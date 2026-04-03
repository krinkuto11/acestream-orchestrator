import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.provisioner import AceProvisionRequest, ResourceScheduler, _vpn_pending_engines
from app.services.state import state
from app.services.vpn_controller import VPNController


def test_scheduler_prefers_least_loaded_dynamic_vpn_node():
    state.set_target_engine_config("dynamic-test-hash")
    scheduler = ResourceScheduler()
    _vpn_pending_engines.clear()

    with patch("app.services.provisioner.cfg.CONTAINER_LABEL", "orchestrator.managed=true"), \
         patch("app.services.provisioner.cfg.ACE_MAP_HTTPS", True), \
         patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={"enabled": True, "dynamic_vpn_management": True}), \
         patch("app.services.state.state.list_vpn_nodes", return_value=[
             {"container_name": "gluetun-dyn-a", "healthy": True, "managed_dynamic": True},
             {"container_name": "gluetun-dyn-b", "healthy": True, "managed_dynamic": True},
         ]), \
         patch("app.services.state.state.get_engines_by_vpn", side_effect=lambda vpn: [object(), object()] if vpn == "gluetun-dyn-a" else []), \
         patch("app.services.gluetun.gluetun_monitor.is_healthy", return_value=True), \
         patch("app.services.provisioner.alloc.allocate_engine_ports", return_value={
             "host_http_port": 30001,
             "container_http_port": 6878,
             "container_https_port": 6879,
             "host_api_port": 30002,
             "container_api_port": 62062,
             "host_https_port": 30003,
         }), \
         patch.object(ResourceScheduler, "_elect_forwarded_engine_locked", return_value=(False, None)):
        spec = scheduler.schedule(AceProvisionRequest(labels={}, env={}), engine_variant_name="AceServe-amd64")

    assert spec.vpn_container == "gluetun-dyn-b"
    assert spec.ports is None
    assert spec.network_config["network_mode"] == "container:gluetun-dyn-b"


def test_vpn_controller_caps_desired_vpns_by_credentials():
    controller = VPNController()

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={
        "enabled": True,
        "dynamic_vpn_management": True,
        "preferred_engines_per_vpn": 2,
    }), \
         patch("app.services.vpn_controller.credential_manager.summary", new=AsyncMock(return_value={"total_credentials": 2})), \
         patch("app.services.vpn_controller.vpn_provisioner.list_managed_nodes", new=AsyncMock(side_effect=[[], []])), \
         patch("app.services.state.state.list_engines", return_value=[object(), object(), object(), object(), object()]), \
         patch.object(controller, "_sync_dynamic_nodes_to_state"), \
         patch.object(controller, "_heal_notready_nodes", new=AsyncMock()), \
         patch.object(controller, "_scale_down_idle_nodes", new=AsyncMock()), \
         patch.object(controller, "_provision_one", new=AsyncMock()) as provision_mock:
        asyncio.run(controller._reconcile_once())

    assert state.get_desired_vpn_node_count() == 2
    assert provision_mock.await_count == 2


def test_vpn_controller_heals_before_provisioning_replacement():
    controller = VPNController()
    call_order = []

    async def _record_heal():
        call_order.append("heal")

    async def _record_provision(_settings):
        call_order.append("provision")

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={
        "enabled": True,
        "dynamic_vpn_management": True,
        "preferred_engines_per_vpn": 1,
    }), \
         patch("app.services.vpn_controller.credential_manager.summary", new=AsyncMock(return_value={"total_credentials": 1})), \
         patch("app.services.vpn_controller.vpn_provisioner.list_managed_nodes", new=AsyncMock(side_effect=[[], []])), \
         patch("app.services.state.state.list_engines", return_value=[object()]), \
         patch.object(controller, "_sync_dynamic_nodes_to_state"), \
         patch.object(controller, "_heal_notready_nodes", side_effect=_record_heal), \
         patch.object(controller, "_scale_down_idle_nodes", new=AsyncMock()), \
         patch.object(controller, "_provision_one", side_effect=_record_provision):
        asyncio.run(controller._reconcile_once())

    assert call_order == ["heal", "provision"]


def test_scheduler_no_vpn_enabled_ignores_dynamic_config_when_disabled():
    state.set_target_engine_config("dynamic-test-hash-no-vpn")
    scheduler = ResourceScheduler()
    _vpn_pending_engines.clear()

    with patch("app.services.provisioner.cfg.CONTAINER_LABEL", "orchestrator.managed=true"), \
         patch("app.services.provisioner.cfg.ACE_MAP_HTTPS", True), \
         patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={
             "enabled": False,
             "dynamic_vpn_management": True,
         }), \
         patch("app.services.provisioner.alloc.allocate_engine_ports", return_value={
             "host_http_port": 30011,
             "container_http_port": 6878,
             "container_https_port": 6879,
             "host_api_port": 30012,
             "container_api_port": 62062,
             "host_https_port": 30013,
         }), \
         patch.object(ResourceScheduler, "_elect_forwarded_engine_locked", return_value=(False, None)):
        spec = scheduler.schedule(AceProvisionRequest(labels={}, env={}), engine_variant_name="AceServe-amd64")

    assert spec.vpn_container is None
    assert spec.ports is not None
    assert "network" in spec.network_config


def test_vpn_controller_skips_when_vpn_disabled_even_if_dynamic_flag_set():
    controller = VPNController()

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={
        "enabled": False,
        "dynamic_vpn_management": True,
    }), \
         patch("app.services.vpn_controller.vpn_provisioner.list_managed_nodes", new=AsyncMock()) as list_nodes_mock, \
         patch.object(controller, "_heal_notready_nodes", new=AsyncMock()) as heal_mock, \
         patch.object(controller, "_provision_one", new=AsyncMock()) as provision_mock:
        asyncio.run(controller._reconcile_once())

    assert state.get_desired_vpn_node_count() == 0
    assert list_nodes_mock.await_count == 0
    assert heal_mock.await_count == 0
    assert provision_mock.await_count == 0


def test_vpn_controller_does_not_restore_leases_during_reconcile_tick():
    controller = VPNController()
    current_nodes = [
        {
            "container_id": "abc123",
            "container_name": "gluetun-dyn-1",
            "status": "running",
            "provider": "protonvpn",
            "protocol": "wireguard",
            "credential_id": "cred-1",
        }
    ]

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={
        "enabled": True,
        "dynamic_vpn_management": True,
        "preferred_engines_per_vpn": 1,
    }), \
         patch("app.services.vpn_controller.vpn_provisioner.list_managed_nodes", new=AsyncMock(side_effect=[current_nodes, current_nodes])), \
         patch("app.services.vpn_controller.credential_manager.restore_leases", new=AsyncMock()) as restore_mock, \
         patch("app.services.vpn_controller.credential_manager.summary", new=AsyncMock(return_value={"total_credentials": 1})), \
         patch("app.services.state.state.list_engines", return_value=[]), \
         patch.object(controller, "_sync_dynamic_nodes_to_state"), \
         patch.object(controller, "_heal_notready_nodes", new=AsyncMock()), \
         patch.object(controller, "_scale_down_idle_nodes", new=AsyncMock()), \
         patch.object(controller, "_provision_one", new=AsyncMock()):
        asyncio.run(controller._reconcile_once())

    restore_mock.assert_not_called()


def test_vpn_controller_restores_leases_once_at_run_startup():
    controller = VPNController()
    startup_nodes = [
        {
            "container_id": "abc123",
            "container_name": "gluetun-dyn-1",
            "status": "running",
            "provider": "protonvpn",
            "protocol": "wireguard",
            "credential_id": "cred-1",
        }
    ]

    async def _single_tick_then_stop():
        controller._stop.set()

    with patch("app.services.vpn_controller.vpn_provisioner.list_managed_nodes", new=AsyncMock(return_value=startup_nodes)) as list_nodes_mock, \
         patch("app.services.vpn_controller.credential_manager.restore_leases", new=AsyncMock()) as restore_mock, \
         patch.object(controller, "_reconcile_once", new=AsyncMock(side_effect=_single_tick_then_stop)), \
         patch.object(controller, "request_reconcile"):
        asyncio.run(controller._run())

    list_nodes_mock.assert_awaited_once_with(include_stopped=True)
    restore_mock.assert_awaited_once_with(startup_nodes)


def test_vpn_controller_bootstraps_vpn_nodes_from_desired_replicas_when_no_engines():
    controller = VPNController()
    state.set_desired_replica_count(2)

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={
        "enabled": True,
        "dynamic_vpn_management": True,
        "preferred_engines_per_vpn": 10,
    }), \
         patch("app.services.vpn_controller.credential_manager.summary", new=AsyncMock(return_value={"total_credentials": 2})), \
         patch("app.services.vpn_controller.vpn_provisioner.list_managed_nodes", new=AsyncMock(side_effect=[[], []])), \
         patch("app.services.state.state.list_engines", return_value=[]), \
         patch.object(controller, "_sync_dynamic_nodes_to_state"), \
         patch.object(controller, "_heal_notready_nodes", new=AsyncMock()), \
         patch.object(controller, "_scale_down_idle_nodes", new=AsyncMock()), \
         patch.object(controller, "_provision_one", new=AsyncMock()) as provision_mock:
        asyncio.run(controller._reconcile_once())

    assert state.get_desired_vpn_node_count() == 1
    assert provision_mock.await_count == 1


def test_vpn_controller_heal_notready_respects_grace_period():
    controller = VPNController()
    node = {
        "container_name": "gluetun-dyn-a",
        "condition": "notready",
        "managed_dynamic": True,
        "last_event_at": datetime.now(timezone.utc),
    }

    with patch("app.services.vpn_controller.state.list_notready_vpn_nodes", return_value=[node]), \
         patch.object(controller, "_drain_and_destroy_node", new=AsyncMock()) as destroy_mock:
        asyncio.run(controller._heal_notready_nodes())

    assert destroy_mock.await_count == 0


def test_vpn_controller_heal_notready_destroys_stale_nodes_after_grace():
    controller = VPNController()
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=controller._notready_heal_grace_s + 5)
    node = {
        "container_name": "gluetun-dyn-a",
        "condition": "notready",
        "managed_dynamic": True,
        "last_event_at": stale_at,
    }

    with patch("app.services.vpn_controller.state.list_notready_vpn_nodes", return_value=[node]), \
         patch.object(controller, "_drain_and_destroy_node", new=AsyncMock()) as destroy_mock:
        asyncio.run(controller._heal_notready_nodes())

    destroy_mock.assert_awaited_once_with("gluetun-dyn-a", reason="node_not_ready")


def test_vpn_controller_provision_requests_engine_reconcile_on_success():
    controller = VPNController()

    with patch("app.services.vpn_controller.state.emit_scaling_intent", return_value={"id": "intent-1"}), \
         patch("app.services.vpn_controller.state.resolve_scaling_intent") as resolve_mock, \
         patch("app.services.vpn_controller.vpn_provisioner.provision_node", new=AsyncMock(return_value={"container_name": "gluetun-dyn-test"})), \
         patch("app.services.autoscaler.engine_controller.request_reconcile") as request_reconcile:
        asyncio.run(controller._provision_one({}))

    resolve_mock.assert_called_once()
    request_reconcile.assert_called_once()
    assert "vpn_node_provisioned:gluetun-dyn-test" in request_reconcile.call_args.kwargs.get("reason", "")


def test_vpn_controller_drain_uses_gather_and_resolves_intents_per_engine():
    controller = VPNController()
    engines = [
        SimpleNamespace(container_id="engine-a"),
        SimpleNamespace(container_id="engine-b"),
        SimpleNamespace(container_id="engine-c"),
    ]

    def _emit_intent(intent_type, details=None):
        details = details or {}
        if intent_type == "terminate_request":
            return {"id": f"terminate:{details.get('container_id')}"}
        return {"id": f"destroy:{details.get('vpn_container')}"}

    def _stop(container_id, force):
        if container_id == "engine-b":
            raise RuntimeError("forced failure")
        return None

    gather_calls = []
    original_gather = asyncio.gather

    async def _gather_spy(*args, **kwargs):
        gather_calls.append((len(args), kwargs.get("return_exceptions")))
        return await original_gather(*args, **kwargs)

    with patch("app.services.vpn_controller.state.get_engines_by_vpn", return_value=engines), \
         patch("app.services.vpn_controller.state.emit_scaling_intent", side_effect=_emit_intent), \
         patch("app.services.vpn_controller.state.resolve_scaling_intent") as resolve_mock, \
         patch("app.services.vpn_controller.stop_container", side_effect=_stop), \
         patch("app.services.vpn_controller.asyncio.gather", side_effect=_gather_spy), \
         patch("app.services.vpn_controller.vpn_provisioner.destroy_node", new=AsyncMock(return_value={"removed": True, "lease_released": True, "container_name": "vpn-a"})):
        asyncio.run(controller._drain_and_destroy_node("vpn-a", reason="node_not_ready"))

    assert gather_calls == [(3, True)]

    terminate_results = {}
    for call in resolve_mock.call_args_list:
        intent_id = call.args[0]
        status = call.args[1]
        if intent_id.startswith("terminate:"):
            terminate_results[intent_id] = status

    assert terminate_results["terminate:engine-a"] == "completed"
    assert terminate_results["terminate:engine-b"] == "failed"
    assert terminate_results["terminate:engine-c"] == "completed"
