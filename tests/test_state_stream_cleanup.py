from app.models.schemas import EngineAddress, SessionInfo, StreamEndedEvent, StreamKey, StreamStartedEvent
from app.services.state import State


def _started_event(*, playback_session_id: str) -> StreamStartedEvent:
    return StreamStartedEvent(
        container_id="engine-1",
        engine=EngineAddress(host="127.0.0.1", port=19000),
        stream=StreamKey(key_type="content_id", key="stream-key-1", file_indexes="0", seekback=0, live_delay=0),
        session=SessionInfo(
            playback_session_id=playback_session_id,
            stat_url="",
            command_url="",
            is_live=1,
        ),
        labels={
            "proxy.control_mode": "api",
            "host.api_port": "62062",
        },
    )


def test_on_stream_ended_cleans_duplicate_active_rows_for_same_key():
    local_state = State()
    try:
        first = local_state.on_stream_started(_started_event(playback_session_id="fallback-1"))
        second = local_state.on_stream_started(_started_event(playback_session_id="fallback-2"))

        active_before = [s for s in local_state.list_streams(status="started") if s.key == "stream-key-1"]
        assert len(active_before) == 2

        local_state.on_stream_ended(
            StreamEndedEvent(container_id="engine-1", stream_id=second.id, reason="stopped")
        )

        active_after = [s for s in local_state.list_streams(status="started") if s.key == "stream-key-1"]
        assert active_after == []

        # Ensure the originally started IDs are no longer present in in-memory API state.
        remaining_ids = {s.id for s in local_state.list_streams()}
        assert first.id not in remaining_ids
        assert second.id not in remaining_ids
    finally:
        local_state._stop_db_worker.set()
        local_state._db_queue.put(None)
        local_state._db_worker.join(timeout=1.0)
