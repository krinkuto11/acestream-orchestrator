"""
Test that stream requests include unique PID parameter to prevent conflicts.

This test validates the fix for the issue where trying to play two different
content IDs in the same AceStream engine causes errors due to missing PID parameter.

The PID (Process ID) parameter uniquely identifies each client session and prevents
conflicts when multiple streams access the same engine.

Reference: context/acexy.go lines 328-339
"""

import pytest
from unittest.mock import Mock, patch, call
import uuid


def test_stream_request_includes_pid_parameter():
    """Test that request_stream_from_engine includes a unique PID parameter"""
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer
    
    # Create mocks
    mock_redis_client = Mock()
    mock_buffer = StreamBuffer(content_id="test_id", redis_client=mock_redis_client)
    mock_client_manager = Mock()
    
    # Create stream manager
    stream_manager = StreamManager(
        content_id="test_content_id_1",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=mock_buffer,
        client_manager=mock_client_manager,
        worker_id="test_worker",
        api_key="test_key"
    )
    
    # Mock the requests.get call
    with patch('app.proxy.stream_manager.requests.get') as mock_get:
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "response": {
                "playback_url": "http://127.0.0.1:6878/ace/r/test",
                "stat_url": "http://127.0.0.1:6878/ace/stat",
                "command_url": "http://127.0.0.1:6878/ace/cmd",
                "playback_session_id": "test_session",
                "is_live": 1
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        # Call the method
        result = stream_manager.request_stream_from_engine()
        
        # Verify the request was made
        assert mock_get.called, "requests.get should have been called"
        
        # Get the call arguments
        call_args = mock_get.call_args
        
        # Check that params were passed
        assert 'params' in call_args.kwargs, "params should be passed to requests.get"
        params = call_args.kwargs['params']
        
        # Verify all required parameters are present
        assert 'id' in params, "id parameter should be present"
        assert 'format' in params, "format parameter should be present"
        assert 'pid' in params, "pid parameter should be present (fix for engine conflicts)"
        
        # Verify parameter values
        assert params['id'] == "test_content_id_1", "id should match content_id"
        assert params['format'] == "json", "format should be json"
        assert params['pid'], "pid should not be empty"
        
        # Verify PID looks like a UUID
        try:
            uuid.UUID(params['pid'])
        except ValueError:
            pytest.fail(f"pid parameter '{params['pid']}' should be a valid UUID")
        
        # Verify the method returned success
        assert result is True, "request_stream_from_engine should return True on success"


def test_multiple_streams_have_different_pids():
    """Test that multiple stream requests generate different PIDs"""
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer
    
    # Create mocks
    mock_redis_client = Mock()
    
    # Create two stream managers for different content
    stream_manager_1 = StreamManager(
        content_id="content_id_1",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=StreamBuffer(content_id="content_id_1", redis_client=mock_redis_client),
        client_manager=Mock(),
        worker_id="test_worker"
    )
    
    stream_manager_2 = StreamManager(
        content_id="content_id_2",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=StreamBuffer(content_id="content_id_2", redis_client=mock_redis_client),
        client_manager=Mock(),
        worker_id="test_worker"
    )
    
    collected_pids = []
    
    # Mock the requests.get call
    with patch('app.proxy.stream_manager.requests.get') as mock_get:
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "response": {
                "playback_url": "http://127.0.0.1:6878/ace/r/test",
                "stat_url": "http://127.0.0.1:6878/ace/stat",
                "command_url": "http://127.0.0.1:6878/ace/cmd",
                "playback_session_id": "test_session",
                "is_live": 1
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        # Request streams from both managers
        stream_manager_1.request_stream_from_engine()
        stream_manager_2.request_stream_from_engine()
        
        # Collect PIDs from all calls
        for call_item in mock_get.call_args_list:
            params = call_item.kwargs.get('params', {})
            if 'pid' in params:
                collected_pids.append(params['pid'])
    
    # Verify we collected PIDs
    assert len(collected_pids) == 2, "Should have collected 2 PIDs"
    
    # Verify PIDs are different
    assert collected_pids[0] != collected_pids[1], \
        "Different streams should have different PIDs to prevent conflicts"
    
    # Verify both are valid UUIDs
    for pid in collected_pids:
        try:
            uuid.UUID(pid)
        except ValueError:
            pytest.fail(f"pid '{pid}' should be a valid UUID")


def test_same_engine_different_content_uses_different_pids():
    """Test that requesting different content on the same engine uses different PIDs"""
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer
    
    # Create mocks
    mock_redis_client = Mock()
    
    # Simulate two different streams to the SAME engine
    # (This is the exact scenario that was causing errors)
    engine_host = "127.0.0.1"
    engine_port = 6878
    
    stream_manager_1 = StreamManager(
        content_id="infohash_1",
        engine_host=engine_host,
        engine_port=engine_port,
        engine_container_id="test_container",
        buffer=StreamBuffer(content_id="infohash_1", redis_client=mock_redis_client),
        client_manager=Mock()
    )
    
    stream_manager_2 = StreamManager(
        content_id="infohash_2",
        engine_host=engine_host,
        engine_port=engine_port,
        engine_container_id="test_container",
        buffer=StreamBuffer(content_id="infohash_2", redis_client=mock_redis_client),
        client_manager=Mock()
    )
    
    pids_used = []
    
    with patch('app.proxy.stream_manager.requests.get') as mock_get:
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {
            "response": {
                "playback_url": "http://127.0.0.1:6878/ace/r/test",
                "stat_url": "http://127.0.0.1:6878/ace/stat",
                "command_url": "http://127.0.0.1:6878/ace/cmd",
                "playback_session_id": "test_session",
                "is_live": 1
            }
        }
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response
        
        # Request both streams
        stream_manager_1.request_stream_from_engine()
        stream_manager_2.request_stream_from_engine()
        
        # Verify both requests were to the same engine
        assert mock_get.call_count == 2, "Should have made 2 requests"
        
        for call_item in mock_get.call_args_list:
            # Verify the URL points to the same engine
            url = call_item.args[0]
            assert f"{engine_host}:{engine_port}" in url, \
                "Both requests should be to the same engine"
            
            # Collect the PID used
            params = call_item.kwargs.get('params', {})
            pids_used.append(params['pid'])
    
    # The critical assertion: PIDs must be different even for the same engine
    # This prevents the "PID already in use" error when playing multiple streams
    assert pids_used[0] != pids_used[1], \
        "CRITICAL: Different content IDs on the same engine MUST use different PIDs"
    
    print(f"✅ Test passed: Two streams to the same engine used different PIDs")
    print(f"   Stream 1 PID: {pids_used[0][:8]}...")
    print(f"   Stream 2 PID: {pids_used[1][:8]}...")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
