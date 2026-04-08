"""Regression tests for StreamManager EOF/failover behavior."""

from unittest.mock import Mock

import pytest

import app.proxy.stream_manager as stream_manager_module


def _build_manager():
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer

    mock_redis_client = Mock()
    mock_buffer = StreamBuffer(content_id="test_id", redis_client=mock_redis_client)
    mock_client_manager = Mock()

    manager = StreamManager(
        content_id="test_content_id",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=mock_buffer,
        client_manager=mock_client_manager,
        worker_id="test_worker",
        api_key="test_key",
    )
    return manager


def test_get_max_client_buffer_seconds_uses_conservative_freshness(monkeypatch):
    manager = _build_manager()

    now = {"value": 1000.0}
    monkeypatch.setattr(stream_manager_module.time, "time", lambda: now["value"])

    class _FakeRedis:
        def smembers(self, _key):
            return {b"client-a", b"client-b"}

        def hmget(self, key, _fields):
            if "client-a" in key:
                # High runway but older sample.
                return [b"20.0", b"20.0", b"1.0", b"998.0", b"998.0"]
            # Lower runway newer sample; conservative p10 should pick this lane.
            return [b"5.0", b"5.0", b"0.9", b"999.0", b"999.0"]

    manager.client_manager.redis_client = _FakeRedis()
    manager.client_manager.client_set_key = "stream:clients"

    runway = manager._get_max_client_buffer_seconds()

    # client-a effective: (20 - 2) * 1.0 = 18.0
    # client-b effective: (5 - 1) * 0.95 = 3.8
    # conservative p10 with 2 samples -> lower sample
    assert runway == pytest.approx(3.8, abs=0.001)


def test_get_max_client_buffer_seconds_decays_last_estimate_when_samples_disappear(monkeypatch):
    manager = _build_manager()

    now = {"value": 1000.0}
    monkeypatch.setattr(stream_manager_module.time, "time", lambda: now["value"])

    class _FakeRedis:
        def __init__(self):
            self.phase = "fresh"

        def smembers(self, _key):
            return {b"client-a"}

        def hmget(self, _key, _fields):
            if self.phase == "fresh":
                return [b"12.0", b"12.0", b"1.0", b"1000.0", b"1000.0"]
            # Stale > 8s, so ignored.
            return [b"12.0", b"12.0", b"1.0", b"980.0", b"980.0"]

    fake_redis = _FakeRedis()
    manager.client_manager.redis_client = fake_redis
    manager.client_manager.client_set_key = "stream:clients"

    first = manager._get_max_client_buffer_seconds()
    assert first == pytest.approx(12.0, abs=0.001)

    fake_redis.phase = "stale"
    now["value"] = 1002.0

    second = manager._get_max_client_buffer_seconds()
    # No fresh samples -> decay last estimate by elapsed wall-clock time.
    assert second == pytest.approx(10.0, abs=0.001)


def test_dynamic_tolerance_uses_max_during_startup_grace(monkeypatch):
    manager = _build_manager()

    now = {"value": 1000.0}
    monkeypatch.setattr(stream_manager_module.time, "time", lambda: now["value"])
    monkeypatch.setattr(manager, "_get_max_client_buffer_seconds", lambda: 12.0)

    manager._start_time = 990.0

    threshold, runway, max_tolerance = manager._get_dynamic_tolerance()

    assert runway == pytest.approx(12.0, abs=0.001)
    assert threshold == pytest.approx(max_tolerance, abs=0.001)


def test_dynamic_tolerance_uses_max_when_no_runway_even_after_grace(monkeypatch):
    manager = _build_manager()

    now = {"value": 2000.0}
    monkeypatch.setattr(stream_manager_module.time, "time", lambda: now["value"])
    monkeypatch.setattr(manager, "_get_max_client_buffer_seconds", lambda: 0.0)

    manager._start_time = 1900.0

    threshold, runway, max_tolerance = manager._get_dynamic_tolerance()

    assert runway == pytest.approx(0.0, abs=0.001)
    assert threshold == pytest.approx(max_tolerance, abs=0.001)


def test_dynamic_tolerance_returns_to_runway_formula_after_grace(monkeypatch):
    manager = _build_manager()

    now = {"value": 3000.0}
    monkeypatch.setattr(stream_manager_module.time, "time", lambda: now["value"])
    monkeypatch.setattr(manager, "_get_max_client_buffer_seconds", lambda: 20.0)

    manager._start_time = 2900.0

    threshold, runway, max_tolerance = manager._get_dynamic_tolerance()

    assert runway == pytest.approx(20.0, abs=0.001)
    assert threshold == pytest.approx(min(max_tolerance, 18.0), abs=0.001)


def test_eof_with_no_clients_skips_failover(monkeypatch):
    manager = _build_manager()

    manager.running = True
    manager.connected = False

    # Avoid spinning health thread logic in this unit test.
    monkeypatch.setattr(manager, "_monitor_health", lambda: None)

    # Keep run loop deterministic and in-process.
    monkeypatch.setattr(manager, "request_stream_from_engine", lambda: True)
    monkeypatch.setattr(manager, "_send_stream_started_event", lambda: None)

    def _fake_start_stream():
        manager.connected = True
        return True

    monkeypatch.setattr(manager, "start_stream", _fake_start_stream)

    def _fake_process_stream_data():
        manager._stream_exit_reason = "eof"

    monkeypatch.setattr(manager, "_process_stream_data", _fake_process_stream_data)
    monkeypatch.setattr(manager.client_manager, "get_total_client_count", lambda: 0)

    cleanup_for_retry_calls = {"count": 0}

    def _fake_cleanup_for_retry():
        cleanup_for_retry_calls["count"] += 1

    monkeypatch.setattr(manager, "_cleanup_for_retry", _fake_cleanup_for_retry, raising=False)
    monkeypatch.setattr(manager, "_send_stream_ended_event", lambda reason="normal": None)
    monkeypatch.setattr(manager, "_cleanup", lambda: None)

    manager.run()

    assert cleanup_for_retry_calls["count"] == 0


def test_eof_with_client_buffer_runway_retries_local_reconnect(monkeypatch):
    manager = _build_manager()

    manager.running = True
    manager.connected = False

    monkeypatch.setattr(manager, "_monitor_health", lambda: None)
    monkeypatch.setattr(manager, "request_stream_from_engine", lambda: True)
    monkeypatch.setattr(manager, "_send_stream_started_event", lambda: None)
    monkeypatch.setattr(stream_manager_module.time, "sleep", lambda _seconds: None)

    start_calls = {"count": 0}
    stop_calls = {"count": 0}
    close_calls = {"count": 0}

    class _FakeReader:
        def stop(self):
            stop_calls["count"] += 1

    class _FakeSocket:
        def close(self):
            close_calls["count"] += 1

    def _fake_start_stream():
        start_calls["count"] += 1
        manager.http_reader = _FakeReader()
        manager.socket = _FakeSocket()
        manager.connected = True
        return True

    monkeypatch.setattr(manager, "start_stream", _fake_start_stream)

    process_calls = {"count": 0}

    def _fake_process_stream_data():
        process_calls["count"] += 1
        if process_calls["count"] == 1:
            manager._stream_exit_reason = "eof"
            return
        manager._stream_exit_reason = None
        manager.running = False

    monkeypatch.setattr(manager, "_process_stream_data", _fake_process_stream_data)
    monkeypatch.setattr(manager.client_manager, "get_total_client_count", lambda: 2)

    class _FakeRedis:
        def smembers(self, _key):
            return {b"client-1", b"client-2"}

        def hget(self, _key, _field):
            return b"10.0"

    manager.client_manager.redis_client = _FakeRedis()
    manager.client_manager.client_set_key = "clients:test"

    failover_calls = {"count": 0}

    def _fake_handle_stream_data_plane_failed(_event):
        failover_calls["count"] += 1

    monkeypatch.setattr(
        "app.services.internal_events.handle_stream_data_plane_failed",
        _fake_handle_stream_data_plane_failed,
    )
    monkeypatch.setattr(manager, "_send_stream_ended_event", lambda reason="normal": None)
    monkeypatch.setattr(manager, "_cleanup", lambda: None)

    manager.run()

    assert failover_calls["count"] == 0
    assert start_calls["count"] == 2
    assert stop_calls["count"] == 1
    assert close_calls["count"] == 1
    assert manager.consecutive_eof_retries == 1


def test_eof_local_reconnect_retry_cap_falls_through_to_failover(monkeypatch):
    manager = _build_manager()

    manager.running = True
    manager.connected = False
    manager.stream_id = "stream-1"
    manager.consecutive_eof_retries = 5
    manager.control_plane_wait_event = Mock()
    manager.control_plane_wait_event.wait.return_value = True

    monkeypatch.setattr(manager, "_monitor_health", lambda: None)
    monkeypatch.setattr(manager, "request_stream_from_engine", lambda: True)
    monkeypatch.setattr(manager, "_send_stream_started_event", lambda: None)

    start_calls = {"count": 0}

    def _fake_start_stream():
        start_calls["count"] += 1
        manager.connected = True
        return True

    monkeypatch.setattr(manager, "start_stream", _fake_start_stream)

    process_calls = {"count": 0}

    def _fake_process_stream_data():
        process_calls["count"] += 1
        if process_calls["count"] == 1:
            manager._stream_exit_reason = "eof"
            return
        manager._stream_exit_reason = None
        manager.running = False

    monkeypatch.setattr(manager, "_process_stream_data", _fake_process_stream_data)
    monkeypatch.setattr(manager.client_manager, "get_total_client_count", lambda: 2)

    class _FakeRedis:
        def smembers(self, _key):
            return {b"client-1"}

        def hget(self, _key, _field):
            return b"10.0"

    manager.client_manager.redis_client = _FakeRedis()
    manager.client_manager.client_set_key = "clients:test"

    failover_calls = {"count": 0}

    def _fake_handle_stream_data_plane_failed(_event):
        failover_calls["count"] += 1

    monkeypatch.setattr(
        "app.services.internal_events.handle_stream_data_plane_failed",
        _fake_handle_stream_data_plane_failed,
    )
    monkeypatch.setattr(manager, "_send_stream_ended_event", lambda reason="normal": None)
    monkeypatch.setattr(manager, "_cleanup", lambda: None)

    manager.run()

    assert failover_calls["count"] == 1
    assert start_calls["count"] == 1
    assert manager.consecutive_eof_retries == 5


def test_send_stream_ended_event_idempotent_for_same_stream(monkeypatch):
    manager = _build_manager()
    manager.stream_id = "stream-1"

    calls = {"count": 0}

    def _fake_handle_stream_ended(_event):
        calls["count"] += 1
        return None

    monkeypatch.setattr("app.services.internal_events.handle_stream_ended", _fake_handle_stream_ended)

    manager._send_stream_ended_event(reason="stopped")
    manager._send_stream_ended_event(reason="failover")

    assert calls["count"] == 1


def test_send_stream_started_event_skips_duplicate_when_stream_id_is_stable(monkeypatch):
    manager = _build_manager()
    manager.stream_id = "stable-stream-id"
    manager._ended_event_sent = False

    calls = {"count": 0}

    def _fake_handle_stream_started(_event):
        calls["count"] += 1
        return None

    monkeypatch.setattr("app.services.internal_events.handle_stream_started", _fake_handle_stream_started)

    manager._send_stream_started_event()

    assert calls["count"] == 0
    assert manager.stream_id == "stable-stream-id"


def test_preflight_failure_aborts_without_retry(monkeypatch):
    manager = _build_manager()

    manager.running = True
    manager.connected = False

    monkeypatch.setattr(manager, "_monitor_health", lambda: None)

    def _fake_request_stream_from_engine():
        manager._last_request_failure_type = "preflight_failed"
        return False

    monkeypatch.setattr(manager, "request_stream_from_engine", _fake_request_stream_from_engine)
    monkeypatch.setattr(manager, "_send_stream_started_event", lambda: None)

    cleanup_for_retry_calls = {"count": 0}

    def _fake_cleanup_for_retry():
        cleanup_for_retry_calls["count"] += 1

    monkeypatch.setattr(manager, "_cleanup_for_retry", _fake_cleanup_for_retry, raising=False)
    monkeypatch.setattr(manager, "_send_stream_ended_event", lambda reason="normal": None)
    monkeypatch.setattr(manager, "_cleanup", lambda: None)

    manager.run()

    assert cleanup_for_retry_calls["count"] == 0
    assert manager.retry_count == 1


def test_legacy_api_proxy_playback_forces_light_preflight(monkeypatch):
    manager = _build_manager()
    manager.control_mode = "LEGACY_API"
    manager.engine_host = "gluetun"
    manager.engine_api_port = 19001

    calls = {"tier": None}

    class _FakeClient:
        def connect(self):
            return None

        def authenticate(self):
            return None

        def preflight(self, content_id, tier="light", file_indexes="0"):
            calls["tier"] = tier
            return {"available": True, "infohash": "resolved-hash"}

        def start_stream(self, infohash, mode="infohash", stream_type="output_format=http", file_indexes="0", seekback=None):
            return {
                "url": "http%3A//127.0.0.1%3A19000/content/resolved-hash/0.1",
                "playback_session_id": "legacy-1",
                "stream": "1",
            }

        def collect_status_samples(self, samples=1, interval_s=0.0, per_sample_timeout_s=1.0):
            return {"status_text": "dl", "peers": 1}

    monkeypatch.setattr("app.proxy.stream_manager.ConfigHelper.legacy_api_preflight_tier", lambda: "deep")
    monkeypatch.setattr("app.proxy.stream_manager.AceLegacyApiClient", lambda *args, **kwargs: _FakeClient())

    assert manager._request_stream_legacy_api() is True
    assert calls["tier"] == "light"


def test_stream_manager_seek_stream_schedules_switch():
    manager = _build_manager()
    manager.control_mode = "LEGACY_API"
    manager.running = True

    class _FakeLegacyClient:
        def seek_stream(self, target_timestamp):
            assert target_timestamp == 1700002000
            return True

    manager.ace_api_client = _FakeLegacyClient()

    result = manager.seek_stream(1700002000)

    assert result["status"] == "seek_issued"
    assert result["target_timestamp"] == 1700002000
    assert manager._pending_seek_start_info is None


def test_stream_manager_pause_resume_updates_runtime_probe_state():
    manager = _build_manager()
    manager.control_mode = "LEGACY_API"
    manager.running = True
    manager.legacy_status_probe = {"status": "dl", "status_text": "dl"}
    manager._legacy_probe_cache = {"status": "dl", "status_text": "dl"}

    class _FakeLegacyClient:
        def pause_stream(self):
            return True

        def resume_stream(self):
            return True

    manager.ace_api_client = _FakeLegacyClient()

    paused = manager.pause_stream()
    resumed = manager.resume_stream()

    assert paused["status"] == "paused"
    assert resumed["status"] == "resumed"
    assert manager.legacy_status_probe["paused"] is False
    assert manager.legacy_status_probe["status"] == "dl"
    assert manager._legacy_probe_cache["paused"] is False


def test_stream_manager_save_stream_uses_resolved_infohash():
    manager = _build_manager()
    manager.control_mode = "LEGACY_API"
    manager.running = True
    manager.resolved_infohash = "resolved-hash"

    calls = {}

    class _FakeLegacyClient:
        def save_stream(self, infohash, index=0, path=""):
            calls["infohash"] = infohash
            calls["index"] = index
            calls["path"] = path
            return True

    manager.ace_api_client = _FakeLegacyClient()

    result = manager.save_stream(index=3, path="/downloads")

    assert result["status"] == "save_issued"
    assert result["infohash"] == "resolved-hash"
    assert result["index"] == 3
    assert result["path"] == "/downloads"
    assert calls == {
        "infohash": "resolved-hash",
        "index": 3,
        "path": "/downloads",
    }


def test_failover_to_new_engine_resets_cached_session(monkeypatch):
    manager = _build_manager()
    manager.engine_host = "172.19.0.5"
    manager.engine_port = 19000
    manager.engine_api_port = 62062
    manager.engine_container_id = "dead-engine-123"
    manager.playback_url = "http://172.19.0.5:19000/content/old"
    manager.playback_session_id = "old-session"
    manager.stat_url = "http://172.19.0.5:19000/stat"
    manager.command_url = "http://172.19.0.5:19000/cmd"
    manager.existing_session = {"session": {"playback_url": manager.playback_url}}

    replacement = Mock()
    replacement.host = "172.19.0.88"
    replacement.port = 19000
    replacement.api_port = 62062
    replacement.container_id = "new-engine-456"

    monkeypatch.setattr(
        "app.proxy.stream_manager.select_best_engine",
        lambda additional_load_by_engine=None: (replacement, 0),
    )

    assert manager._failover_to_new_engine() is True
    assert manager.engine_container_id == "new-engine-456"
    assert manager.engine_host == "172.19.0.88"
    assert manager.playback_url is None
    assert manager.playback_session_id is None
    assert manager.stat_url == ""
    assert manager.command_url == ""
    assert manager.existing_session == {}


def test_retry_attempt_triggers_engine_failover(monkeypatch):
    manager = _build_manager()
    manager.running = True
    manager.max_retries = 3

    call_state = {
        "failover_calls": 0,
        "process_calls": 0,
        "request_calls": 0,
    }

    monkeypatch.setattr(manager, "_monitor_health", lambda: None)
    monkeypatch.setattr(manager, "_send_stream_started_event", lambda: None)
    monkeypatch.setattr(manager, "_send_stream_ended_event", lambda reason="normal": None)
    monkeypatch.setattr(manager, "_cleanup", lambda: None)
    monkeypatch.setattr("app.proxy.stream_manager.time.sleep", lambda _seconds: None)

    def _fake_cleanup_for_retry():
        manager.connected = False

    monkeypatch.setattr(manager, "_cleanup_for_retry", _fake_cleanup_for_retry)

    def _fake_failover_to_new_engine():
        call_state["failover_calls"] += 1
        return True

    monkeypatch.setattr(manager, "_failover_to_new_engine", _fake_failover_to_new_engine)
    monkeypatch.setattr(manager, "_apply_existing_session", lambda: False)

    def _fake_request_stream_from_engine():
        call_state["request_calls"] += 1
        return True

    monkeypatch.setattr(manager, "request_stream_from_engine", _fake_request_stream_from_engine)

    def _fake_start_stream():
        manager.connected = True
        return True

    monkeypatch.setattr(manager, "start_stream", _fake_start_stream)

    def _fake_process_stream_data():
        call_state["process_calls"] += 1
        if call_state["process_calls"] == 1:
            manager._stream_exit_reason = "error"
            return
        manager.running = False

    monkeypatch.setattr(manager, "_process_stream_data", _fake_process_stream_data)

    manager.run()

    assert call_state["failover_calls"] == 1
    assert call_state["request_calls"] == 2


def test_cleanup_failed_engine_after_transition_stops_old_engine(monkeypatch):
    manager = _build_manager()
    manager.engine_container_id = "new-engine-123"

    old_engine = Mock()
    old_engine.vpn_container = "vpn-old"
    old_engine.health_status = "unhealthy"

    fake_state = Mock()
    fake_state.list_streams.return_value = []
    fake_state.get_engine.return_value = old_engine
    fake_state.is_vpn_node_draining.return_value = True
    fake_state.list_vpn_nodes.return_value = [
        {
            "container_name": "vpn-old",
            "healthy": False,
            "status": "running",
            "condition": "notready",
        }
    ]

    stop_calls = {"count": 0}
    remove_calls = {"count": 0}

    def _fake_stop_container(container_id, force=False):
        assert container_id == "old-engine-999"
        assert force is True
        stop_calls["count"] += 1

    def _fake_remove_engine(container_id):
        assert container_id == "old-engine-999"
        remove_calls["count"] += 1

    fake_state.remove_engine.side_effect = _fake_remove_engine

    monkeypatch.setattr("app.services.state.state", fake_state)
    monkeypatch.setattr("app.services.provisioner.stop_container", _fake_stop_container)

    manager._cleanup_failed_engine_after_transition("old-engine-999")

    assert stop_calls["count"] == 1
    assert remove_calls["count"] == 1


def test_cleanup_failed_engine_after_transition_skips_when_old_engine_has_active_streams(monkeypatch):
    manager = _build_manager()
    manager.engine_container_id = "new-engine-123"

    old_engine = Mock()
    old_engine.vpn_container = "vpn-old"
    old_engine.health_status = "unhealthy"

    fake_state = Mock()
    fake_state.list_streams.return_value = [Mock()]
    fake_state.get_engine.return_value = old_engine
    fake_state.is_vpn_node_draining.return_value = True
    fake_state.list_vpn_nodes.return_value = []

    stop_calls = {"count": 0}

    def _fake_stop_container(*_args, **_kwargs):
        stop_calls["count"] += 1

    monkeypatch.setattr("app.services.state.state", fake_state)
    monkeypatch.setattr("app.services.provisioner.stop_container", _fake_stop_container)

    manager._cleanup_failed_engine_after_transition("old-engine-999")

    assert stop_calls["count"] == 0


def test_monitor_health_force_kills_socket_on_stall(monkeypatch):
    manager = _build_manager()
    manager.running = True
    manager.connected = True
    manager.healthy = True
    manager.last_data_time = 90.0
    manager.http_reader = Mock()
    manager.socket = Mock()

    monkeypatch.setattr(stream_manager_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(stream_manager_module.ConfigHelper, "connection_timeout", lambda: 15.0)

    def _single_cycle_sleep(_seconds):
        manager.running = False

    monkeypatch.setattr(stream_manager_module.time, "sleep", _single_cycle_sleep)

    manager._monitor_health()

    manager.http_reader.stop.assert_called_once()
    manager.socket.close.assert_called_once()
    assert manager.healthy is False
    assert manager.connected is False
