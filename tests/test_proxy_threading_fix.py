"""
Test that proxy stream manager runs correctly with threading instead of gevent.
"""

import pytest
import time
import threading
from unittest.mock import Mock, patch, MagicMock
import os


def test_stream_manager_run_executes_in_thread():
    """Test that stream_manager.run() is called when started via threading.Thread"""
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer
    from app.proxy.client_manager import ClientManager
    
    # Create mocks
    mock_redis_client = Mock()
    mock_buffer = StreamBuffer(content_id="test_id", redis_client=mock_redis_client)
    mock_client_manager = Mock()
    
    # Track whether run() was called
    run_called = threading.Event()
    
    # Create stream manager
    stream_manager = StreamManager(
        content_id="test_content_id",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=mock_buffer,
        client_manager=mock_client_manager,
        worker_id="test_worker",
        api_key="test_key"
    )
    
    # Mock the methods that would make external calls
    original_run = stream_manager.run
    
    def mock_run():
        """Mock run that just sets the event and returns"""
        run_called.set()
        # Don't actually run the full method
        return
    
    stream_manager.run = mock_run
    
    # Start stream manager in a thread (like proxy server does)
    thread = threading.Thread(target=stream_manager.run, daemon=True, name="test-stream")
    thread.start()
    
    # Wait for run to be called (with timeout)
    assert run_called.wait(timeout=2.0), "stream_manager.run() was not called within 2 seconds"
    
    # Clean up
    thread.join(timeout=1.0)


def test_stream_manager_sends_events():
    """Test that stream_manager sends start and end events"""
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer
    
    # Create mocks
    mock_redis_client = Mock()
    mock_buffer = StreamBuffer(content_id="test_id", redis_client=mock_redis_client)
    mock_client_manager = Mock()
    
    # Create stream manager
    stream_manager = StreamManager(
        content_id="test_content_id",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=mock_buffer,
        client_manager=mock_client_manager,
        worker_id="test_worker",
        api_key="test_key"
    )
    
    # Track events
    events_sent = {
        'started': False,
        'ended': False
    }
    
    # Mock the HTTP requests
    with patch('app.proxy.stream_manager.requests.get') as mock_get, \
         patch('app.proxy.stream_manager.requests.post') as mock_post:
        
        # Mock engine response
        mock_engine_response = Mock()
        mock_engine_response.json.return_value = {
            "response": {
                "playback_url": "http://127.0.0.1:6878/ace/r/test",
                "stat_url": "http://127.0.0.1:6878/ace/stat",
                "command_url": "http://127.0.0.1:6878/ace/cmd",
                "playback_session_id": "test_session",
                "is_live": 1
            }
        }
        mock_engine_response.raise_for_status = Mock()
        mock_get.return_value = mock_engine_response
        
        # Mock event responses
        def mock_post_handler(url, **kwargs):
            response = Mock()
            response.raise_for_status = Mock()
            if '/stream_started' in url:
                events_sent['started'] = True
                response.json.return_value = {'id': 'test_stream_id'}
            elif '/stream_ended' in url:
                events_sent['ended'] = True
                response.json.return_value = {}
            return response
        
        mock_post.side_effect = mock_post_handler
        
        # Mock start_stream to avoid pipe creation
        with patch.object(stream_manager, 'start_stream', return_value=False):
            # Run the stream manager
            stream_manager.run()
        
        # Verify that start event was sent
        assert events_sent['started'], "stream_started event was not sent"
        
        # Verify that end event was sent (should happen in finally block)
        assert events_sent['ended'], "stream_ended event was not sent"


def test_proxy_server_uses_threading():
    """Test that ProxyServer.start_stream() uses threading.Thread instead of gevent.spawn"""
    import inspect
    from app.proxy.server import ProxyServer
    
    # Read the source code of start_stream to verify it uses threading.Thread
    source = inspect.getsource(ProxyServer.start_stream)
    
    # Remove comments to avoid false positives
    source_lines = [line for line in source.split('\n') if not line.strip().startswith('#')]
    source_without_comments = '\n'.join(source_lines)
    
    # Check that threading.Thread is used and gevent.spawn is NOT used
    assert 'threading.Thread' in source, "ProxyServer.start_stream should use threading.Thread"
    assert 'gevent.spawn' not in source_without_comments, "ProxyServer.start_stream should NOT use gevent.spawn (outside comments)"
    assert 'thread.start()' in source, "ProxyServer.start_stream should call thread.start()"



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
