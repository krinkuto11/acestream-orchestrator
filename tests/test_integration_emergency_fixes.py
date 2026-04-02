import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.schemas import EngineState
from app.services.autoscaler import EngineController
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


def test_notready_vpn_eviction_flow_removes_affected_engines():
    state.engines["e1"] = _engine("e1", "gluetun")
    state.engines["e2"] = _engine("e2", "gluetun")
    state.engines["e3"] = _engine("e3", "gluetun2")

    watcher = DockerEventWatcher()
    watcher._emit_vpn_evictions("gluetun", reason="node_unhealthy")

    controller = EngineController()
    with patch("app.services.autoscaler.stop_container"):
        asyncio.run(controller._process_pending_termination_intents())

    remaining_vpn1 = state.get_engines_by_vpn("gluetun")
    remaining_vpn2 = state.get_engines_by_vpn("gluetun2")
    assert len(remaining_vpn1) == 0
    assert len(remaining_vpn2) == 1
    assert remaining_vpn2[0].container_id == "e3"
