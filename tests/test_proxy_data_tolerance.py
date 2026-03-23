"""
Test that proxy data tolerance settings are configurable.
"""

import pytest
import os
import time
from unittest.mock import Mock, patch, MagicMock


def test_no_data_timeout_is_configurable():
    """Test that NO_DATA_TIMEOUT_CHECKS is read from environment"""
    with patch.dict(os.environ, {'PROXY_NO_DATA_TIMEOUT_CHECKS': '50'}):
        # Reload config to pick up new env var
        import importlib
        from app.proxy import config_helper
        importlib.reload(config_helper)
        from app.proxy.config_helper import ConfigHelper
        
        # Should use the env var value
        assert ConfigHelper.no_data_timeout_checks() == 50


def test_no_data_check_interval_is_configurable():
    """Test that NO_DATA_CHECK_INTERVAL is read from environment"""
    with patch.dict(os.environ, {'PROXY_NO_DATA_CHECK_INTERVAL': '0.5'}):
        # Reload config to pick up new env var
        import importlib
        from app.proxy import config_helper
        importlib.reload(config_helper)
        from app.proxy.config_helper import ConfigHelper
        
        # Should use the env var value
        assert ConfigHelper.no_data_check_interval() == 0.5


def test_initial_data_wait_timeout_is_configurable():
    """Test that INITIAL_DATA_WAIT_TIMEOUT is read from environment"""
    with patch.dict(os.environ, {'PROXY_INITIAL_DATA_WAIT_TIMEOUT': '20'}):
        # Reload config to pick up new env var
        import importlib
        from app.proxy import config_helper
        importlib.reload(config_helper)
        from app.proxy.config_helper import ConfigHelper
        
        # Should use the env var value
        assert ConfigHelper.initial_data_wait_timeout() == 20


def test_initial_data_check_interval_is_configurable():
    """Test that INITIAL_DATA_CHECK_INTERVAL is read from environment"""
    with patch.dict(os.environ, {'PROXY_INITIAL_DATA_CHECK_INTERVAL': '0.5'}):
        # Reload config to pick up new env var
        import importlib
        from app.proxy import config_helper
        importlib.reload(config_helper)
        from app.proxy.config_helper import ConfigHelper
        
        # Should use the env var value
        assert ConfigHelper.initial_data_check_interval() == 0.5


def test_stream_generator_uses_configurable_no_data_timeout():
    """Test that StreamGenerator uses ConfigHelper for no data timeout"""
    from app.proxy.stream_generator import StreamGenerator
    from app.proxy.stream_buffer import StreamBuffer
    from app.proxy.config_helper import ConfigHelper
    
    # Mock dependencies
    mock_redis_client = Mock()
    mock_buffer = StreamBuffer(content_id="test_id", redis_client=mock_redis_client)
    mock_buffer.index = 1
    
    mock_client_manager = Mock()
    mock_client_manager.add_client = Mock()
    mock_client_manager.refresh_client_ttl = Mock()
    mock_client_manager.remove_client = Mock()
    
    # Create stream generator
    stream_generator = StreamGenerator(
        content_id="test_content_id",
        client_id="test_client_id",
        client_ip="127.0.0.1",
        client_user_agent="test_agent",
        stream_initializing=False
    )
    
    # Mock the setup to inject our mocks
    with patch('app.proxy.server.ProxyServer') as mock_proxy_server:
        mock_instance = Mock()
        mock_instance.stream_buffers = {"test_content_id": mock_buffer}
        mock_instance.client_managers = {"test_content_id": mock_client_manager}
        mock_proxy_server.get_instance.return_value = mock_instance
        
        # First setup wait requires fresh data beyond baseline index.
        # Simulate one initial chunk, then no more data so timeout loop is exercised.
        call_count = [0]
        first_call = [True]

        def counting_get_chunks(_index):
            call_count[0] += 1
            if first_call[0]:
                first_call[0] = False
                return [b"\x47" * 188]
            return None

        mock_buffer.get_chunks = counting_get_chunks

        def fake_wait_for_initial_data(min_index=None):
            mock_buffer.index = (min_index or 0) + 1
            return True

        stream_generator._wait_for_initial_data = fake_wait_for_initial_data
        
        # Run generator (should stop after no_data_timeout_checks)
        chunks_received = list(stream_generator.generate())
        
        # Should have called get_chunks more than no_data_timeout_checks times
        # (because it keeps checking until consecutive_empty > no_data_max_checks)
        no_data_max_checks = ConfigHelper.no_data_timeout_checks()
        assert call_count[0] > no_data_max_checks
        
        # One bootstrap chunk may be yielded, then stream should end on no-data timeout.
        assert len(chunks_received) <= 1


def test_stream_generator_respects_custom_no_data_timeout():
    """Test that StreamGenerator uses custom no data timeout from environment"""
    # Set a very short timeout for testing
    with patch.dict(os.environ, {
        'PROXY_NO_DATA_TIMEOUT_CHECKS': '5',
        'PROXY_NO_DATA_CHECK_INTERVAL': '0.01'
    }):
        # Reload config to pick up new env vars
        import importlib
        from app.proxy import config_helper
        importlib.reload(config_helper)
        
        from app.proxy.stream_generator import StreamGenerator
        from app.proxy.stream_buffer import StreamBuffer
        from app.proxy.config_helper import ConfigHelper
        
        # Verify config was updated
        assert ConfigHelper.no_data_timeout_checks() == 5
        assert ConfigHelper.no_data_check_interval() == 0.01
        
        # Mock dependencies
        mock_redis_client = Mock()
        mock_buffer = StreamBuffer(content_id="test_id", redis_client=mock_redis_client)
        mock_buffer.index = 1
        
        mock_client_manager = Mock()
        mock_client_manager.add_client = Mock()
        mock_client_manager.refresh_client_ttl = Mock()
        mock_client_manager.remove_client = Mock()
        
        # Create stream generator
        stream_generator = StreamGenerator(
            content_id="test_content_id",
            client_id="test_client_id",
            client_ip="127.0.0.1",
            client_user_agent="test_agent",
            stream_initializing=False
        )
        
        # Mock the setup to inject our mocks
        with patch('app.proxy.server.ProxyServer') as mock_proxy_server:
            mock_instance = Mock()
            mock_instance.stream_buffers = {"test_content_id": mock_buffer}
            mock_instance.client_managers = {"test_content_id": mock_client_manager}
            mock_proxy_server.get_instance.return_value = mock_instance
            
            # First setup wait requires fresh data beyond baseline index.
            # Simulate one initial chunk, then no data to trigger timeout behavior.
            first_call = [True]

            def get_chunks(_index):
                if first_call[0]:
                    first_call[0] = False
                    return [b"\x47" * 188]
                return None

            mock_buffer.get_chunks = get_chunks

            def fake_wait_for_initial_data(min_index=None):
                mock_buffer.index = (min_index or 0) + 1
                return True

            stream_generator._wait_for_initial_data = fake_wait_for_initial_data

            # Measure how long it takes to timeout
            start_time = time.time()
            chunks_received = list(stream_generator.generate())
            elapsed = time.time() - start_time
            
            # Should timeout in roughly 5 * 0.01 = 0.05 seconds
            # Allow some overhead for processing
            expected_timeout = 5 * 0.01
            assert elapsed < expected_timeout + 0.5  # Give 0.5s overhead
            assert len(chunks_received) <= 1


def test_stream_generator_waits_for_fresh_data_not_stale(monkeypatch):
    """Initial readiness should require buffer advancement past baseline index."""
    from app.proxy.stream_generator import StreamGenerator

    stream_generator = StreamGenerator(
        content_id="test_content_id",
        client_id="test_client_id",
        client_ip="127.0.0.1",
        client_user_agent="test_agent",
        stream_initializing=False,
    )

    class DummyBuffer:
        def __init__(self):
            self.index = 36

    stream_generator.buffer = DummyBuffer()

    monkeypatch.setattr("app.proxy.stream_generator.ConfigHelper.initial_data_wait_timeout", lambda: 0.2)
    monkeypatch.setattr("app.proxy.stream_generator.ConfigHelper.initial_data_check_interval", lambda: 0.01)

    sleeps = {"count": 0}

    def _sleep_and_advance(_interval):
        sleeps["count"] += 1
        if sleeps["count"] == 2:
            # Simulate first fresh chunk arriving after startup.
            stream_generator.buffer.index = 37

    monkeypatch.setattr("app.proxy.stream_generator.time.sleep", _sleep_and_advance)

    assert stream_generator._wait_for_initial_data(min_index=36) is True


def test_stream_generator_initialization_fails_fast_on_preflight_rejection(monkeypatch):
    from app.proxy.stream_generator import StreamGenerator

    stream_generator = StreamGenerator(
        content_id="test_content_id",
        client_id="test_client_id",
        client_ip="127.0.0.1",
        client_user_agent="test_agent",
        stream_initializing=True,
    )

    manager = Mock()
    manager._last_request_failure_type = "preflight_failed"
    manager.connected = False
    manager.playback_url = None

    mock_proxy_instance = Mock()
    mock_proxy_instance.stream_managers = {"test_content_id": manager}

    monkeypatch.setattr("app.proxy.stream_generator.ConfigHelper.channel_init_grace_period", lambda: 10)
    monkeypatch.setattr("app.proxy.server.ProxyServer.get_instance", lambda: mock_proxy_instance)

    sleep_calls = {"count": 0}

    def _sleep(_interval):
        sleep_calls["count"] += 1

    monkeypatch.setattr("app.proxy.stream_generator.time.sleep", _sleep)

    assert stream_generator._wait_for_initialization() is False
    assert sleep_calls["count"] == 0


def test_api_key_is_passed_to_stream_manager():
    """Test that API key from environment is passed to StreamManager"""
    from app.proxy.server import ProxyServer
    
    # Set API key in environment
    with patch.dict(os.environ, {'API_KEY': 'test_api_key_123'}):
        # Mock Redis
        with patch('app.proxy.server.redis.Redis') as mock_redis_class:
            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis.set.return_value = True
            mock_redis_class.return_value = mock_redis
            
            # Mock StreamManager to capture the api_key argument
            with patch('app.proxy.server.StreamManager') as mock_stream_manager_class:
                mock_stream_manager = Mock()
                mock_stream_manager.run = Mock()
                mock_stream_manager_class.return_value = mock_stream_manager
                
                # Patch thread creation during server init and stream startup to avoid
                # spawning background listener threads in this unit test.
                with patch('app.proxy.server.threading.Thread'):
                    # Create ProxyServer instance
                    proxy_server = ProxyServer()

                    # Start a stream
                    proxy_server.start_stream(
                        content_id="test_content_id",
                        engine_host="127.0.0.1",
                        engine_port=6878,
                        engine_container_id="test_container"
                    )
                
                # Verify StreamManager was created with the API key
                mock_stream_manager_class.assert_called_once()
                call_kwargs = mock_stream_manager_class.call_args[1]
                assert 'api_key' in call_kwargs
                assert call_kwargs['api_key'] == 'test_api_key_123'


def test_stream_events_use_internal_handlers_without_http_posts():
    """Test that stream events are dispatched via internal handlers (no HTTP posts)."""
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer
    
    # Mock dependencies
    mock_redis_client = Mock()
    mock_buffer = StreamBuffer(content_id="test_id", redis_client=mock_redis_client)
    mock_client_manager = Mock()
    
    # Create stream manager with API key
    stream_manager = StreamManager(
        content_id="test_content_id",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=mock_buffer,
        client_manager=mock_client_manager,
        worker_id="test_worker",
        api_key="test_key_123"
    )
    
    # Set required fields usually populated during request_stream_from_engine.
    stream_manager.playback_session_id = "session_123"
    stream_manager.stat_url = "http://127.0.0.1:6878/stat"
    stream_manager.command_url = "http://127.0.0.1:6878/cmd"
    stream_manager.is_live = True

    with patch('app.proxy.stream_manager.requests.post') as mock_post:
        with patch('app.services.internal_events.handle_stream_started') as mock_started:
            mock_started.return_value = Mock(id="stream_123")
            with patch('app.proxy.stream_manager.threading.Thread') as mock_thread:
                def run_target_immediately(*, target=None, name=None, daemon=None):
                    class _ImmediateThread:
                        def start(self_inner):
                            if target:
                                target()
                    return _ImmediateThread()

                mock_thread.side_effect = run_target_immediately

                stream_manager._send_stream_started_event()

    assert mock_started.called
    assert stream_manager.stream_id == "stream_123"
    assert not mock_post.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
