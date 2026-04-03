import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.schemas import EngineState
from app.services.docker_client import DockerEventWatcher
from app.services.state import state


def _engine(container_id: str, vpn_container: str) -> EngineState:
    now = datetime.now(timezone.utc)
    return EngineState(
        container_id=container_id,
        container_name=container_id,
        host=vpn_container,
        port=6878,
        labels={"acestream.vpn_container": vpn_container},
        forwarded=False,
        first_seen=now,
        last_seen=now,
        streams=[],
        health_status="healthy",
        vpn_container=vpn_container,
    )


def setup_function():
    state.clear_state()


def teardown_function():
    state.clear_state()


def test_vpn_notready_emits_forced_eviction_intents_and_reconcile():
    watcher = DockerEventWatcher()

    with patch.object(state, "get_engines_by_vpn", return_value=[_engine("e1", "gluetun")]), \
         patch.object(state, "list_pending_scaling_intents", return_value=[]), \
         patch.object(state, "emit_scaling_intent") as emit_intent, \
         patch("app.services.autoscaler.engine_controller.request_reconcile") as request_reconcile:
        watcher._emit_vpn_evictions("gluetun", reason="node_unhealthy")

    assert emit_intent.call_count == 1
    details = emit_intent.call_args.kwargs["details"]
    assert details["eviction_reason"] == "vpn_not_ready"
    assert details["node_reason"] == "node_unhealthy"
    assert details["force"] is True
    request_reconcile.assert_called_once()


def test_apply_state_update_marks_node_status_and_triggers_eviction_on_unhealthy():
    watcher = DockerEventWatcher()

    with patch.object(state, "update_vpn_node_status") as update_status, \
         patch.object(DockerEventWatcher, "_emit_vpn_evictions") as emit_evictions:
        watcher._apply_state_update(
            container_id="vpn-1",
            container_name="gluetun",
            action="health_status: unhealthy",
            attrs={},
        )

    update_status.assert_called_once()
    assert update_status.call_args.args[:2] == ("gluetun", "unhealthy")
    assert "metadata" in update_status.call_args.kwargs
    emit_evictions.assert_called_once_with("gluetun", reason="node_unhealthy")


def test_apply_state_update_marks_ready_without_eviction_on_healthy():
    watcher = DockerEventWatcher()

    with patch.object(state, "update_vpn_node_status") as update_status, \
         patch.object(DockerEventWatcher, "_emit_vpn_evictions") as emit_evictions, \
         patch.object(DockerEventWatcher, "_request_engine_reconcile") as request_reconcile:
        watcher._apply_state_update(
            container_id="vpn-1",
            container_name="gluetun",
            action="health_status: healthy",
            attrs={},
        )

    update_status.assert_called_once()
    assert update_status.call_args.args[:2] == ("gluetun", "healthy")
    assert "metadata" in update_status.call_args.kwargs
    emit_evictions.assert_not_called()
    request_reconcile.assert_called_once_with(reason="vpn_ready:gluetun")
