#!/usr/bin/env python3
"""
Test cache cleanup enhancements including periodic cleanup and tracking.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_cache_cleanup_with_size_tracking():
    """Test that cache cleanup tracks size and timestamp."""
    print("\n🧪 Testing cache cleanup with size tracking...")
    
    from app.services.state import State
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    # Create state instance
    state = State()
    
    # Mock the database operations
    with patch('app.services.state.SessionLocal'):
        # Mock the clear_acestream_cache function to return size
        with patch('app.services.provisioner.clear_acestream_cache') as mock_clear_cache:
            mock_clear_cache.return_value = (True, 10485760)  # 10MB cache size
            
            # Create and start a stream
            evt_started = StreamStartedEvent(
                container_id="test_container_123",
                engine=EngineAddress(host="127.0.0.1", port=8080),
                stream=StreamKey(key_type="content_id", key="test_content_id"),
                session=SessionInfo(
                    playback_session_id="session_123",
                    stat_url="http://127.0.0.1:8080/stat",
                    command_url="http://127.0.0.1:8080/command",
                    is_live=1
                )
            )
            
            stream_state = state.on_stream_started(evt_started)
            print(f"✅ Stream started: {stream_state.id[:8]}")
            
            # Verify engine exists and has correct initial state
            engine = state.get_engine("test_container_123")
            assert engine is not None, "Engine should exist"
            assert engine.last_cache_cleanup is None, "Cache cleanup timestamp should be None initially"
            assert engine.cache_size_bytes is None, "Cache size should be None initially"
            print(f"✅ Engine initial state verified (cleanup timestamp: None, cache size: None)")
            
            # End the stream
            evt_ended = StreamEndedEvent(
                container_id="test_container_123",
                stream_id=stream_state.id,
                reason="test_ended"
            )
            
            ended_stream = state.on_stream_ended(evt_ended)
            assert ended_stream is not None, "Stream should have ended"
            print(f"✅ Stream ended: {ended_stream.id[:8]}")
            
            # Verify cache cleanup was called
            mock_clear_cache.assert_called_once_with("test_container_123")
            print(f"✅ Cache cleanup called for container")
            
            # Verify engine state was updated with cleanup info
            engine = state.get_engine("test_container_123")
            assert engine.last_cache_cleanup is not None, "Cache cleanup timestamp should be set"
            assert engine.cache_size_bytes == 10485760, f"Cache size should be 10485760, got {engine.cache_size_bytes}"
            print(f"✅ Engine state updated (cleanup timestamp: {engine.last_cache_cleanup}, cache size: {engine.cache_size_bytes} bytes)")
        
    print("\n✅ Cache cleanup with size tracking test passed!")
    return True


def test_engine_state_with_cache_fields():
    """Test that EngineState can be created with cache cleanup fields."""
    print("\n🧪 Testing EngineState with cache cleanup fields...")
    
    from app.models.schemas import EngineState
    from datetime import datetime, timezone
    
    # Create engine with cache fields
    now = datetime.now(timezone.utc)
    engine = EngineState(
        container_id="test_123",
        container_name="test-engine",
        host="127.0.0.1",
        port=8080,
        labels={},
        first_seen=now,
        last_seen=now,
        streams=[],
        health_status="healthy",
        last_health_check=now,
        last_stream_usage=now,
        last_cache_cleanup=now,
        cache_size_bytes=5242880  # 5MB
    )
    
    assert engine.last_cache_cleanup == now, "Cache cleanup timestamp should be set"
    assert engine.cache_size_bytes == 5242880, "Cache size should be 5242880"
    print(f"✅ EngineState created with cache fields (timestamp: {engine.last_cache_cleanup}, size: {engine.cache_size_bytes})")
    
    # Test with None values
    engine_none = EngineState(
        container_id="test_456",
        container_name="test-engine-2",
        host="127.0.0.1",
        port=8081,
        labels={},
        first_seen=now,
        last_seen=now,
        streams=[],
        last_cache_cleanup=None,
        cache_size_bytes=None
    )
    
    assert engine_none.last_cache_cleanup is None, "Cache cleanup timestamp should be None"
    assert engine_none.cache_size_bytes is None, "Cache size should be None"
    print(f"✅ EngineState created with None cache fields")
    
    print("\n✅ EngineState cache fields test passed!")
    return True


def test_clear_acestream_cache_return_value():
    """Test that clear_acestream_cache returns both success and size."""
    print("\n🧪 Testing clear_acestream_cache return value...")
    
    from app.services.provisioner import clear_acestream_cache
    
    # Create a mock container
    mock_container = MagicMock()
    mock_container.status = "running"
    # Mock both size check and cleanup commands
    mock_container.exec_run.side_effect = [
        MagicMock(exit_code=0, output=b"2097152\t/home/appuser/.ACEStream/.acestream_cache"),  # 2MB
        MagicMock(exit_code=0, output=b"")  # cleanup command
    ]
    
    # Mock get_client
    with patch('app.services.provisioner.get_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client
        
        # Call the function
        success, cache_size = clear_acestream_cache("test_container_789")
        
        # Verify return value
        assert success is True, "Function should return True on success"
        assert cache_size == 2097152, f"Cache size should be 2097152, got {cache_size}"
        assert isinstance(cache_size, int), "Cache size should be an integer"
        print(f"✅ clear_acestream_cache returned (True, {cache_size})")
    
    # Test with failed cleanup (should still return size)
    mock_container.exec_run.side_effect = [
        MagicMock(exit_code=0, output=b"1048576\t/home/appuser/.ACEStream/.acestream_cache"),  # 1MB
        MagicMock(exit_code=1, output=b"error")  # failed cleanup
    ]
    
    with patch('app.services.provisioner.get_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client
        
        success, cache_size = clear_acestream_cache("test_container_fail")
        
        assert success is False, "Function should return False on failure"
        assert cache_size == 1048576, f"Cache size should still be returned, got {cache_size}"
        print(f"✅ clear_acestream_cache returned (False, {cache_size}) on failure")
    
    print("\n✅ clear_acestream_cache return value test passed!")
    return True


def test_periodic_cleanup_task():
    """Test that periodic cleanup task processes idle engines."""
    print("\n🧪 Testing periodic cleanup task...")
    
    from app.services.monitor import DockerMonitor
    from app.services.state import State
    from app.models.schemas import EngineState, StreamState
    from datetime import datetime, timezone
    
    # Create mock state
    state = State()
    now = datetime.now(timezone.utc)
    
    # Add two engines - one idle, one active
    with state._lock:
        state.engines["idle_engine"] = EngineState(
            container_id="idle_engine",
            container_name="idle-engine",
            host="127.0.0.1",
            port=8080,
            labels={},
            first_seen=now,
            last_seen=now,
            streams=[],  # No streams - idle
            last_cache_cleanup=None,
            cache_size_bytes=None
        )
        
        state.engines["active_engine"] = EngineState(
            container_id="active_engine",
            container_name="active-engine",
            host="127.0.0.1",
            port=8081,
            labels={},
            first_seen=now,
            last_seen=now,
            streams=["stream_1"],  # Has active stream
            last_cache_cleanup=None,
            cache_size_bytes=None
        )
        
        # Add actual stream object for the active engine
        state.streams["stream_1"] = StreamState(
            id="stream_1",
            key_type="content_id",
            key="test_key",
            container_id="active_engine",
            playback_session_id="session_1",
            stat_url="http://127.0.0.1:8081/stat",
            command_url="http://127.0.0.1:8081/cmd",
            is_live=True,
            started_at=now,
            status="started"
        )
    
    print(f"✅ Created test engines (1 idle, 1 active)")
    
    # Mock cleanup function
    with patch('app.services.monitor.state', state):
        with patch('app.services.provisioner.clear_acestream_cache') as mock_clear:
            with patch('app.services.db.SessionLocal'):
                mock_clear.return_value = (True, 5242880)  # 5MB
                
                # Create monitor and run periodic cleanup
                monitor = DockerMonitor()
                monitor._periodic_cache_cleanup()
                
                # Verify cleanup was called only for idle engine
                assert mock_clear.call_count == 1, f"Cleanup should be called once, got {mock_clear.call_count}"
                mock_clear.assert_called_with("idle_engine")
                print(f"✅ Cleanup called only for idle engine")
    
    print("\n✅ Periodic cleanup task test passed!")
    return True


if __name__ == "__main__":
    print("🔧 Testing Cache Cleanup Enhancements")
    print("=" * 60)
    
    try:
        test_cache_cleanup_with_size_tracking()
        test_engine_state_with_cache_fields()
        test_clear_acestream_cache_return_value()
        test_periodic_cleanup_task()
        
        print("\n" + "=" * 60)
        print("🎉 All cache cleanup enhancement tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
