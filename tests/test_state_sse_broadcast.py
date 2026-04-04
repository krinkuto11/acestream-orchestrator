from datetime import datetime, timezone
from unittest.mock import patch

from app.models.schemas import StreamStatSnapshot
from app.services.state import state


def setup_function():
    state.clear_state()


def teardown_function():
    state.clear_state()


def test_apply_engine_docker_event_broadcasts_change():
    events = []
    unsubscribe = state.subscribe_state_changes(lambda event: events.append(event))

    with patch.object(state, "_enqueue_db_task", return_value=None):
        state.apply_engine_docker_event(
            container_id="engine-1",
            container_name="acestream-engine-1",
            action="start",
            labels={
                "host.http_port": "19000",
                "host.api_port": "62062",
            },
        )

    unsubscribe()

    assert any(
        event.get("change_type") == "engine_docker_event"
        and (event.get("metadata") or {}).get("container_id") == "engine-1"
        and (event.get("metadata") or {}).get("action") == "start"
        for event in events
    )


def test_append_stat_broadcasts_throttled_every_two_seconds():
    events = []
    unsubscribe = state.subscribe_state_changes(lambda event: events.append(event))

    snapshot = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=10,
        speed_down=1000,
        speed_up=100,
        downloaded=1024,
        uploaded=128,
        status="running",
    )

    with patch.object(state, "_enqueue_db_task", return_value=None), \
         patch("app.services.state.time.monotonic", side_effect=[1.0, 1.5, 3.2]):
        state.append_stat("stream-1", snapshot)
        state.append_stat("stream-1", snapshot)
        state.append_stat("stream-1", snapshot)

    unsubscribe()

    stats_events = [event for event in events if event.get("change_type") == "stream_stats_updated"]
    assert len(stats_events) == 2
