import asyncio
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
