import json
from unittest.mock import Mock, patch

from app.proxy.server import ProxyServer


def _build_server_stub():
    server = ProxyServer.__new__(ProxyServer)
    server.stream_managers = {}
    server.stream_buffers = {}
    server.client_managers = {}
    server.shutdown_timers = {}
    server.worker_id = "test-worker"
    server.redis_client = Mock()
    server.redis_client.set.return_value = True
    server.am_i_owner = Mock(return_value=True)
    return server


def test_start_stream_cancels_pending_timer_for_active_manager():
    server = _build_server_stub()

    active_manager = Mock()
    active_manager.running = True
    server.stream_managers["content-a"] = active_manager

    pending_timer = Mock()
    server.shutdown_timers["content-a"] = pending_timer

    server._stop_stream = Mock()

    result = server.start_stream("content-a", "127.0.0.1", 6878)

    assert result is True
    pending_timer.cancel.assert_called_once()
    assert "content-a" not in server.shutdown_timers
    server._stop_stream.assert_not_called()


def test_start_stream_restarts_dead_manager_and_reinitializes():
    server = _build_server_stub()

    dead_manager = Mock()
    dead_manager.running = False
    server.stream_managers["content-b"] = dead_manager

    server._stop_stream = Mock()

    with patch("app.proxy.server.StreamBuffer") as buffer_cls, \
         patch("app.proxy.server.ClientManager") as client_cls, \
         patch("app.proxy.server.StreamManager") as manager_cls, \
         patch("app.proxy.server.threading.Thread") as thread_cls:
        manager_instance = Mock()
        manager_instance.run = Mock()
        manager_cls.return_value = manager_instance

        thread_instance = Mock()
        thread_cls.return_value = thread_instance

        result = server.start_stream("content-b", "127.0.0.1", 6878, engine_container_id="eng-new")

    assert result is True
    server._stop_stream.assert_called_once_with("content-b")
    buffer_cls.assert_called_once()
    client_cls.assert_called_once()
    manager_cls.assert_called_once()
    thread_instance.start.assert_called_once()


def test_handle_event_replaces_existing_shutdown_timer():
    server = _build_server_stub()

    old_timer = Mock()
    server.shutdown_timers["content-c"] = old_timer

    new_timer = Mock()
    with patch("app.proxy.server.threading.Timer", return_value=new_timer) as timer_cls:
        server._handle_event(
            {
                "data": json.dumps(
                    {
                        "event": "client_disconnected",
                        "content_id": "content-c",
                        "remaining_clients": 0,
                    }
                )
            }
        )

    old_timer.cancel.assert_called_once()
    timer_cls.assert_called_once()
    new_timer.start.assert_called_once()
    assert server.shutdown_timers["content-c"] is new_timer


def test_handle_client_disconnect_replaces_existing_shutdown_timer():
    server = _build_server_stub()

    old_timer = Mock()
    server.shutdown_timers["content-d"] = old_timer

    client_manager = Mock()
    client_manager.get_total_client_count.return_value = 0
    server.client_managers["content-d"] = client_manager

    new_timer = Mock()
    with patch("app.proxy.server.threading.Timer", return_value=new_timer):
        server.handle_client_disconnect("content-d")

    old_timer.cancel.assert_called_once()
    new_timer.start.assert_called_once()
    assert server.shutdown_timers["content-d"] is new_timer


def test_stop_stream_clears_shutdown_timer_registry():
    server = _build_server_stub()
    server.redis_client = None

    timer = Mock()
    server.shutdown_timers["content-e"] = timer

    server._stop_stream("content-e")

    timer.cancel.assert_called_once()
    assert "content-e" not in server.shutdown_timers
