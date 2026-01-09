"""
Test that proxy data tolerance settings are configurable.
"""

import pytest
import os
import time
from unittest.mock import Mock, patch, MagicMock
import gevent


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
    mock_buffer.get_chunks = Mock(return_value=None)  # Always return no data
    mock_buffer.index = 1  # Pretend buffer has data so initial wait passes
    
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
    with patch('app.proxy.stream_generator.ProxyServer') as mock_proxy_server:
        mock_instance = Mock()
        mock_instance.stream_buffers = {"test_content_id": mock_buffer}
        mock_instance.client_managers = {"test_content_id": mock_client_manager}
        mock_proxy_server.get_instance.return_value = mock_instance
        
        # Track how many times get_chunks is called
        call_count = [0]
        original_get_chunks = mock_buffer.get_chunks
        
        def counting_get_chunks(index):
            call_count[0] += 1
            # Return None to simulate no data
            return original_get_chunks(index)
        
        mock_buffer.get_chunks = counting_get_chunks
        
        # Run generator (should stop after no_data_timeout_checks)
        chunks_received = list(stream_generator.generate())
        
        # Should have called get_chunks more than no_data_timeout_checks times
        # (because it keeps checking until consecutive_empty > no_data_max_checks)
        no_data_max_checks = ConfigHelper.no_data_timeout_checks()
        assert call_count[0] > no_data_max_checks
        
        # Should not have yielded any chunks since buffer was always empty
        assert len(chunks_received) == 0


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
        mock_buffer.get_chunks = Mock(return_value=None)  # Always return no data
        mock_buffer.index = 1  # Pretend buffer has data so initial wait passes
        
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
        with patch('app.proxy.stream_generator.ProxyServer') as mock_proxy_server:
            mock_instance = Mock()
            mock_instance.stream_buffers = {"test_content_id": mock_buffer}
            mock_instance.client_managers = {"test_content_id": mock_client_manager}
            mock_proxy_server.get_instance.return_value = mock_instance
            
            # Measure how long it takes to timeout
            start_time = time.time()
            chunks_received = list(stream_generator.generate())
            elapsed = time.time() - start_time
            
            # Should timeout in roughly 5 * 0.01 = 0.05 seconds
            # Allow some overhead for processing
            expected_timeout = 5 * 0.01
            assert elapsed < expected_timeout + 0.5  # Give 0.5s overhead
            assert len(chunks_received) == 0


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
                
                # Create ProxyServer instance
                proxy_server = ProxyServer()
                
                # Start a stream
                with patch('app.proxy.server.threading.Thread'):
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


def test_stream_events_use_bearer_token():
    """Test that stream started/ended events use Authorization: Bearer header"""
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
    
    # Mock HTTP requests
    with patch('app.proxy.stream_manager.requests.post') as mock_post:
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {'id': 'stream_123'}
        mock_post.return_value = mock_response
        
        # Mock get request for engine
        with patch('app.proxy.stream_manager.requests.get') as mock_get:
            mock_engine_response = Mock()
            mock_engine_response.json.return_value = {
                "response": {
                    "playback_url": "http://127.0.0.1:6878/test",
                    "stat_url": "http://127.0.0.1:6878/stat",
                    "command_url": "http://127.0.0.1:6878/cmd",
                    "playback_session_id": "session_123",
                    "is_live": 1
                }
            }
            mock_engine_response.raise_for_status = Mock()
            mock_get.return_value = mock_engine_response
            
            # Request stream from engine (this triggers _send_stream_started_event)
            stream_manager.request_stream_from_engine()
            stream_manager._send_stream_started_event()
        
        # Verify the POST request was made with Authorization: Bearer header
        assert mock_post.called
        call_args = mock_post.call_args
        headers = call_args[1]['headers']
        
        # Should use Authorization: Bearer, not X-API-KEY
        assert 'Authorization' in headers
        assert headers['Authorization'] == 'Bearer test_key_123'
        assert 'X-API-KEY' not in headers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
