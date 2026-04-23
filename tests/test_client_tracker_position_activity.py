from app.services.client_tracker import ClientTrackingService


def test_update_client_position_refreshes_last_active(monkeypatch):
    tracker = ClientTrackingService()

    tracker.register_client(
        client_id="client-1",
        stream_id="stream-1",
        ip_address="127.0.0.1",
        user_agent="UA/1.0",
        protocol="TS",
        connected_at=1000.0,
        idle_timeout_s=5.0,
        worker_id="test-worker",
    )

    tracker.update_client_position(
        client_id="client-1",
        stream_id="stream-1",
        protocol="TS",
        seconds_behind=8.0,
        source="ts_starvation_decay",
        confidence=0.5,
        observed_at=1008.0,
        now=1008.0,
    )

    monkeypatch.setattr("app.services.client_tracker.time.time", lambda: 1011.0)
    removed = tracker.prune_stale_clients(timeout_s=5.0)

    assert removed == 0
    assert tracker.count_active_clients(stream_id="stream-1", protocol="TS") == 1

    monkeypatch.setattr("app.services.client_tracker.time.time", lambda: 1014.0)
    removed_late = tracker.prune_stale_clients(timeout_s=5.0)

    assert removed_late == 1
    assert tracker.count_active_clients(stream_id="stream-1", protocol="TS") == 0


def test_update_client_position_recreates_missing_tracker_row():
    tracker = ClientTrackingService()

    # Simulate missing row (e.g., transient unregister/prune race).
    tracker.update_client_position(
        client_id="client-2",
        stream_id="stream-2",
        protocol="TS",
        seconds_behind=6.5,
        source="ts_cursor_ema",
        confidence=0.7,
        observed_at=2000.0,
        now=2000.0,
    )

    rows = tracker.get_stream_clients("stream-2", protocol="TS")
    assert len(rows) == 1
    assert rows[0]["client_id"] == "client-2"
    assert rows[0]["client_runway_seconds"] == 6.5
    assert rows[0]["buffer_seconds_behind"] == 6.5
