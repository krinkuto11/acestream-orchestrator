import time
import sys
from types import ModuleType

import pytest

if "m3u8" not in sys.modules:
    fake_m3u8 = ModuleType("m3u8")
    setattr(fake_m3u8, "M3U8", object)
    setattr(fake_m3u8, "loads", lambda _text: None)
    sys.modules["m3u8"] = fake_m3u8

from app.proxy.hls_proxy import ClientManager


@pytest.fixture(autouse=True)
def _stub_metrics(monkeypatch):
    monkeypatch.setattr("app.services.metrics.observe_proxy_client_connect", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.metrics.observe_proxy_client_disconnect", lambda *_args, **_kwargs: None)


def test_hls_client_manager_accumulates_transfer_stats():
    manager = ClientManager()

    manager.record_activity(
        client_ip="10.0.0.1",
        client_id="10.0.0.1:ua1",
        user_agent="UA/1.0",
        request_kind="manifest",
        bytes_sent=220,
        now=1000.0,
    )
    manager.record_activity(
        client_ip="10.0.0.1",
        client_id="10.0.0.1:ua1",
        user_agent="UA/1.0",
        request_kind="segment",
        bytes_sent=1200,
        chunks_sent=1,
        now=1001.0,
    )
    manager.record_activity(
        client_ip="10.0.0.1",
        client_id="10.0.0.1:ua1",
        user_agent="UA/1.0",
        request_kind="segment",
        bytes_sent=800,
        chunks_sent=1,
        now=1002.0,
    )

    clients = manager.list_clients()

    assert len(clients) == 1
    assert clients[0]["client_id"] == "10.0.0.1:ua1"
    assert clients[0]["requests_total"] == 3
    assert clients[0]["bytes_sent"] == 2220.0
    assert clients[0]["chunks_sent"] == 2
    assert clients[0]["last_request_kind"] == "segment"
    assert clients[0]["stats_updated_at"] == 1002.0
    assert manager.last_activity["10.0.0.1"] == 1002.0


def test_hls_client_manager_cleanup_inactive_uses_client_last_active():
    manager = ClientManager()
    now = time.time()

    manager.record_activity(
        client_ip="10.0.0.1",
        client_id="10.0.0.1:old",
        user_agent="UA/old",
        request_kind="segment",
        bytes_sent=100,
        chunks_sent=1,
        now=now - 120.0,
    )
    manager.record_activity(
        client_ip="10.0.0.1",
        client_id="10.0.0.1:new",
        user_agent="UA/new",
        request_kind="segment",
        bytes_sent=300,
        chunks_sent=1,
        now=now - 5.0,
    )

    all_inactive = manager.cleanup_inactive(timeout=60.0)

    assert all_inactive is False
    assert manager.count_active_clients() == 1
    clients = manager.list_clients()
    assert len(clients) == 1
    assert clients[0]["client_id"] == "10.0.0.1:new"
    assert manager.last_activity["10.0.0.1"] == pytest.approx(now - 5.0, abs=0.01)


def test_hls_client_manager_tracks_buffer_seconds_behind():
    from app.services.client_tracker import client_tracking_service

    stream_id = "test-hls-buffer-seconds"
    client_tracking_service.unregister_stream(stream_id=stream_id, protocol="HLS")
    manager = ClientManager(stream_id=stream_id)

    manager.record_activity(
        client_ip="10.0.0.2",
        client_id="10.0.0.2:ua2",
        user_agent="UA/2.0",
        request_kind="manifest",
        bytes_sent=256,
        buffer_seconds_behind=6.75,
        now=2000.0,
    )

    clients = manager.list_clients()
    matching = [c for c in clients if c.get("client_id") == "10.0.0.2:ua2"]

    assert len(matching) == 1
    assert matching[0]["buffer_seconds_behind"] == pytest.approx(6.75, abs=0.001)
    assert matching[0]["stream_buffer_window_seconds"] == pytest.approx(6.75, abs=0.001)


def test_hls_client_manager_preserves_window_and_updates_client_runway_on_segment():
    from app.services.client_tracker import client_tracking_service

    stream_id = "test-hls-window-runway"
    client_tracking_service.unregister_stream(stream_id=stream_id, protocol="HLS")
    manager = ClientManager(stream_id=stream_id)

    manager.record_activity(
        client_ip="10.0.0.3",
        client_id="10.0.0.3:ua3",
        user_agent="UA/3.0",
        request_kind="manifest",
        bytes_sent=320,
        stream_buffer_window_seconds=11.0,
        now=3000.0,
    )

    manager.record_activity(
        client_ip="10.0.0.3",
        client_id="10.0.0.3:ua3",
        user_agent="UA/3.0",
        request_kind="segment",
        bytes_sent=1024,
        chunks_sent=1,
        client_runway_seconds=4.25,
        stream_buffer_window_seconds=11.0,
        now=3001.0,
    )

    clients = manager.list_clients()
    matching = [c for c in clients if c.get("client_id") == "10.0.0.3:ua3"]

    assert len(matching) == 1
    assert matching[0]["client_runway_seconds"] == pytest.approx(4.25, abs=0.001)
    assert matching[0]["buffer_seconds_behind"] == pytest.approx(4.25, abs=0.001)
    assert matching[0]["stream_buffer_window_seconds"] == pytest.approx(11.0, abs=0.001)
    assert matching[0]["position_source"] == "hls_segment_delta"
