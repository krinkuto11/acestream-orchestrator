"""Regression tests for StreamManager EOF/failover behavior."""

from unittest.mock import Mock


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

    monkeypatch.setattr(manager, "_cleanup_for_retry", _fake_cleanup_for_retry)
    monkeypatch.setattr(manager, "_send_stream_ended_event", lambda reason="normal": None)
    monkeypatch.setattr(manager, "_cleanup", lambda: None)

    manager.run()

    assert cleanup_for_retry_calls["count"] == 0


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

    monkeypatch.setattr(manager, "_cleanup_for_retry", _fake_cleanup_for_retry)
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

        def preflight(self, content_id, tier="light"):
            calls["tier"] = tier
            return {"available": True, "infohash": "resolved-hash"}

        def start_stream(self, infohash, mode="infohash"):
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
