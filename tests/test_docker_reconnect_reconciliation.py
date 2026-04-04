from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.schemas import EngineState
from app.services.docker_client import DockerEventWatcher
from app.services.reindex import run_reindex
from app.services.state import state


class _MockContainer:
    def __init__(self, container_id: str, status: str = "running"):
        self.id = container_id
        self.status = status
        self.labels = {}
        self.attrs = {}


class _MockEventStream:
    def __iter__(self):
        return iter(())

    def close(self):
        return None


def _engine(container_id: str, vpn_container: str | None = None, labels: dict | None = None) -> EngineState:
    now = datetime.now(timezone.utc)
    return EngineState(
        container_id=container_id,
        container_name=container_id,
        host=vpn_container or "127.0.0.1",
        port=6878,
        labels=labels or {},
        forwarded=False,
        first_seen=now,
        last_seen=now,
        streams=[],
        health_status="unknown",
        vpn_container=vpn_container,
    )


def setup_function():
    state.clear_state()


def teardown_function():
    state.clear_state()


def test_run_reindex_removes_state_engines_missing_from_docker():
    state.engines["engine-running"] = _engine("engine-running")
    state.engines["engine-lost"] = _engine("engine-lost")

    docker_containers = [_MockContainer("engine-running", status="running")]
    with patch("app.services.reindex.list_managed", return_value=docker_containers):
        run_reindex()

    assert "engine-running" in state.engines
    assert "engine-lost" not in state.engines


def test_run_reindex_removes_transition_engines_without_conflict():
    state.engines["engine-draining"] = _engine("engine-draining", vpn_container="gluetun-dyn-a")
    state.set_vpn_node_lifecycle("gluetun-dyn-a", "draining")
    state.engines["engine-provisioning"] = _engine(
        "engine-provisioning",
        labels={"acestream.provisioning": "true"},
    )

    with patch("app.services.reindex.list_managed", return_value=[]):
        run_reindex()

    assert "engine-draining" not in state.engines
    assert "engine-provisioning" not in state.engines


def test_event_watcher_reconnect_executes_full_reconciliation(caplog):
    watcher = DockerEventWatcher()

    client = MagicMock()
    client.ping.return_value = None
    client.events.side_effect = [_MockEventStream(), _MockEventStream()]
    client.close.return_value = None

    with patch("app.services.docker_client.docker.from_env", return_value=client), \
         patch("app.services.reindex.run_reindex") as run_reindex_mock, \
         patch.object(watcher, "_request_engine_reconcile") as reconcile_mock:
        watcher._consume_events_blocking()  # Initial connect: no forced reindex
        watcher._consume_events_blocking()  # Reconnect: must force full reconciliation

    run_reindex_mock.assert_called_once()
    reconcile_mock.assert_called_once_with(reason="docker_event_stream_reconnected")
    assert any(
        "Docker event stream reconnected. Executed full state reconciliation to catch missed events." in record.message
        for record in caplog.records
    )
