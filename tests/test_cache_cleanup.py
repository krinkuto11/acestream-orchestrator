#!/usr/bin/env python3
"""
Test cache cleanup when engine becomes idle.
"""

import sys
import os
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_cache_cleanup_on_idle():
    """Test that cache is cleared when an engine becomes idle."""
    print("\n🧪 Testing cache cleanup when engine becomes idle...")
    
    from app.services.state import State
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    # Create state instance
    state = State()
    
    # Mock the database operations
    with patch('app.services.state.SessionLocal'):
        # Mock the clear_acestream_cache function
        with patch('app.services.provisioner.clear_acestream_cache') as mock_clear_cache:
            # Create a stream started event
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
            
            # Start a stream on the engine
            stream_state = state.on_stream_started(evt_started)
            print(f"✅ Stream started: {stream_state.id[:8]}")
            
            # Verify engine has one active stream
            engine = state.get_engine("test_container_123")
            assert engine is not None, "Engine should exist"
            assert len(engine.streams) == 1, "Engine should have 1 active stream"
            print(f"✅ Engine has {len(engine.streams)} active stream(s)")
            
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
            engine = state.get_engine("test_container_123")
            assert len(engine.streams) == 0, "Engine should have no active streams"
            print(f"✅ Engine now has {len(engine.streams)} active streams")
            
            # Check that clear_acestream_cache was called
            mock_clear_cache.assert_called_once_with("test_container_123")
            print(f"✅ Cache cleanup called for container test_container_123")
        
    print("\n✅ Cache cleanup test passed!")
    return True


def test_cache_cleanup_not_called_with_multiple_streams():
    """Test that cache is NOT cleared when engine still has active streams."""
    print("\n🧪 Testing cache cleanup is NOT called when engine has multiple streams...")
    
    from app.services.state import State
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    # Create state instance
    state = State()
    
    # Mock the database operations
    with patch('app.services.state.SessionLocal'):
        # Mock the clear_acestream_cache function
        with patch('app.services.provisioner.clear_acestream_cache') as mock_clear_cache:
            # Start first stream
            evt_started_1 = StreamStartedEvent(
                container_id="test_container_456",
                engine=EngineAddress(host="127.0.0.1", port=8080),
                stream=StreamKey(key_type="content_id", key="test_content_id_1"),
                session=SessionInfo(
                    playback_session_id="session_456_1",
                    stat_url="http://127.0.0.1:8080/stat",
                    command_url="http://127.0.0.1:8080/command",
                    is_live=1
                )
            )
            
            stream_state_1 = state.on_stream_started(evt_started_1)
            print(f"✅ First stream started: {stream_state_1.id[:8]}")
            
            # Start second stream on the same engine
            evt_started_2 = StreamStartedEvent(
                container_id="test_container_456",
                engine=EngineAddress(host="127.0.0.1", port=8080),
                stream=StreamKey(key_type="content_id", key="test_content_id_2"),
                session=SessionInfo(
                    playback_session_id="session_456_2",
                    stat_url="http://127.0.0.1:8080/stat2",
                    command_url="http://127.0.0.1:8080/command2",
                    is_live=1
                )
            )
            
            stream_state_2 = state.on_stream_started(evt_started_2)
            print(f"✅ Second stream started: {stream_state_2.id[:8]}")
            
            # Verify engine has two active streams
            engine = state.get_engine("test_container_456")
            assert len(engine.streams) == 2, "Engine should have 2 active streams"
            print(f"✅ Engine has {len(engine.streams)} active streams")
            
            # End the first stream
            evt_ended_1 = StreamEndedEvent(
                container_id="test_container_456",
                stream_id=stream_state_1.id,
                reason="test_ended"
            )
            
            ended_stream = state.on_stream_ended(evt_ended_1)
            assert ended_stream is not None, "Stream should have ended"
            print(f"✅ First stream ended: {ended_stream.id[:8]}")
            
            # Verify engine still has one active stream
            engine = state.get_engine("test_container_456")
            assert len(engine.streams) == 1, "Engine should still have 1 active stream"
            print(f"✅ Engine still has {len(engine.streams)} active stream(s)")
            
            # Cache cleanup should NOT have been called yet
            mock_clear_cache.assert_not_called()
            print(f"✅ Cache cleanup NOT called (engine still has active streams)")
            
            # End the second stream
            evt_ended_2 = StreamEndedEvent(
                container_id="test_container_456",
                stream_id=stream_state_2.id,
                reason="test_ended"
            )
            
            ended_stream_2 = state.on_stream_ended(evt_ended_2)
            assert ended_stream_2 is not None, "Second stream should have ended"
            print(f"✅ Second stream ended: {ended_stream_2.id[:8]}")
            
            # Verify engine now has no active streams
            engine = state.get_engine("test_container_456")
            assert len(engine.streams) == 0, "Engine should have no active streams"
            print(f"✅ Engine now has {len(engine.streams)} active streams")
            
            # NOW cache cleanup should have been called
            mock_clear_cache.assert_called_once_with("test_container_456")
            print(f"✅ Cache cleanup called for container test_container_456")
        
    print("\n✅ Multiple streams test passed!")
    return True


def test_clear_acestream_cache_function():
    """Test the clear_acestream_cache function directly."""
    print("\n🧪 Testing clear_acestream_cache function...")
    
    from app.services.provisioner import clear_acestream_cache
    
    # Create a mock container
    mock_container = MagicMock()
    mock_container.status = "running"
    # Mock both size check and cleanup commands
    mock_container.exec_run.side_effect = [
        MagicMock(exit_code=0, output=b"1048576\t/home/appuser/.ACEStream/.acestream_cache"),  # size check
        MagicMock(exit_code=0, output=b"")  # cleanup command
    ]
    
    # Mock get_client
    with patch('app.services.provisioner.get_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client
        
        # Call the function
        success, cache_size = clear_acestream_cache("test_container_789")
        
        # Verify the function was called correctly
        assert success is True, "Function should return True on success"
        assert cache_size == 1048576, f"Cache size should be 1048576, got {cache_size}"
        print(f"✅ Cache cleanup command executed successfully, cache size: {cache_size} bytes")
    
    # Test with non-running container
    mock_container.status = "stopped"
    with patch('app.services.provisioner.get_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_get_client.return_value = mock_client
        
        success, cache_size = clear_acestream_cache("test_container_stopped")
        assert success is False, "Function should return False for non-running container"
        assert cache_size == 0, "Cache size should be 0 for non-running container"
        print(f"✅ Function correctly skipped non-running container")
    
    print("\n✅ Direct function test passed!")
    return True


if __name__ == "__main__":
    print("🔧 Testing AceStream Cache Cleanup")
    print("=" * 60)
    
    try:
        test_cache_cleanup_on_idle()
        test_cache_cleanup_not_called_with_multiple_streams()
        test_clear_acestream_cache_function()
        
        print("\n" + "=" * 60)
        print("🎉 All cache cleanup tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
