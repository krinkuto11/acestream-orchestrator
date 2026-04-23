import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.autoscaler import EngineController
from app.services.provisioner import ResourceScheduler


def test_dynamic_node_running_requires_control_api_reachable():
    node = {
        "container_name": "gluetun-dyn-test",
        "status": "running",
        "healthy": True,
    }

    with patch.object(ResourceScheduler, "_is_vpn_control_api_reachable", return_value=False):
        assert ResourceScheduler._is_dynamic_node_ready(node) is False

    with patch.object(ResourceScheduler, "_is_vpn_control_api_reachable", return_value=True):
        assert ResourceScheduler._is_dynamic_node_ready(node) is True


def test_autoscaler_transient_vpn_not_ready_is_blocked_not_failed():
    controller = EngineController()
    intent = {"id": "intent-1"}

    with patch("app.services.autoscaler.circuit_breaker_manager.can_provision", return_value=True), \
         patch("app.services.autoscaler.start_acestream", side_effect=RuntimeError("No healthy dynamic VPN nodes available - cannot schedule AceStream engine")), \
         patch("app.services.autoscaler.circuit_breaker_manager.record_provisioning_failure") as record_failure, \
         patch("app.services.autoscaler.state.resolve_scaling_intent") as resolve_intent:
        asyncio.run(controller._execute_create_intent(intent))

    record_failure.assert_not_called()
    resolve_intent.assert_called_once()
    args = resolve_intent.call_args.args
    assert args[0] == "intent-1"
    assert args[1] == "blocked"
    assert resolve_intent.call_args.args[2]["reason"] == "vpn_not_ready"


def test_autoscaler_counts_only_non_draining_for_create_deficit():
    controller = EngineController()
    now = datetime.now()
    serving = SimpleNamespace(container_id="engine-serving", forwarded=False, last_seen=now)
    draining = SimpleNamespace(container_id="engine-draining", forwarded=False, last_seen=now)

    intents = [{"id": "create-1"}]

    def emit_intent(intent_type, details):
        assert intent_type == "create_request"
        return intents.pop(0)

    with patch("app.services.settings_persistence.SettingsPersistence.load_engine_settings", return_value={}), \
         patch.object(controller, "_enqueue_outdated_engine_termination_intents", new=AsyncMock()), \
         patch.object(controller, "_process_pending_termination_intents", new=AsyncMock()), \
         patch("app.services.autoscaler.state.get_desired_replica_count", return_value=2), \
         patch("app.services.autoscaler.state.list_engines", return_value=[serving, draining]), \
         patch("app.services.autoscaler.state.is_engine_draining", side_effect=lambda cid: cid == "engine-draining"), \
         patch("app.services.autoscaler.state.emit_scaling_intent", side_effect=emit_intent), \
         patch.object(controller, "_execute_create_intent", new=AsyncMock()) as create_mock, \
         patch.object(controller, "_execute_terminate_intent", new=AsyncMock()) as terminate_mock:
        asyncio.run(controller._reconcile_once())

    create_mock.assert_awaited_once()
    terminate_mock.assert_not_awaited()


def test_autoscaler_cleans_up_draining_when_stoppable():
    controller = EngineController()
    now = datetime.now()
    serving = SimpleNamespace(container_id="engine-serving", forwarded=False, last_seen=now)
    draining = SimpleNamespace(container_id="engine-draining", forwarded=False, last_seen=now)

    intents = [{"id": "term-1"}]

    def emit_intent(intent_type, details):
        assert intent_type == "terminate_request"
        assert details.get("eviction_reason") == "draining_cleanup"
        return intents.pop(0)

    with patch("app.services.settings_persistence.SettingsPersistence.load_engine_settings", return_value={}), \
         patch.object(controller, "_enqueue_outdated_engine_termination_intents", new=AsyncMock()), \
         patch.object(controller, "_process_pending_termination_intents", new=AsyncMock()), \
         patch("app.services.autoscaler.state.get_desired_replica_count", return_value=1), \
         patch("app.services.autoscaler.state.list_engines", return_value=[serving, draining]), \
         patch("app.services.autoscaler.state.is_engine_draining", side_effect=lambda cid: cid == "engine-draining"), \
         patch("app.services.autoscaler.can_stop_engine", side_effect=lambda cid, bypass_grace_period=False: cid == "engine-draining"), \
         patch("app.services.autoscaler.state.emit_scaling_intent", side_effect=emit_intent), \
         patch.object(controller, "_execute_create_intent", new=AsyncMock()) as create_mock, \
         patch.object(controller, "_execute_terminate_intent", new=AsyncMock()) as terminate_mock:
        asyncio.run(controller._reconcile_once())

    create_mock.assert_not_awaited()
    terminate_mock.assert_awaited_once()
    args = terminate_mock.await_args.args
    assert args[0]["id"] == "term-1"
    assert args[1] == "engine-draining"
