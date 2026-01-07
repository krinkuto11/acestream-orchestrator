"""Tests for stream session fixes"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from app.services.proxy.stream_session import StreamSession
from app.services.proxy.stream_buffer import StreamBuffer
from app.services.proxy.stream_manager import StreamManager


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = MagicMock()
    redis.get.return_value = None
    redis.incr.return_value = 1
    redis.setex.return_value = True
    redis.set.return_value = True
    redis.delete.return_value = 1
    redis.scan.return_value = (0, [])
    return redis


def test_redis_import_uses_correct_path():
    """Test that RedisClient import uses correct path (app.core.utils)"""
    session = StreamSession(
        stream_id="test_stream",
        ace_id="test_ace_id",
        engine_host="localhost",
        engine_port=6878,
        container_id="test_container"
    )
    
    # Mock the HTTP response to simulate successful initialization
    with patch('app.services.proxy.stream_session.httpx.AsyncClient') as mock_client:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": {
                "playback_url": "http://localhost:6878/ace/getstream/test",
                "stat_url": "http://localhost:6878/ace/stat",
                "command_url": "http://localhost:6878/ace/cmd/test/session",
                "playback_session_id": "test_session",
                "is_live": 1
            }
        }
        mock_response.raise_for_status = MagicMock()
        
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_instance
        
        # Mock RedisClient import at the correct path
        with patch('app.core.utils.RedisClient') as mock_redis_client:
            mock_redis_client.get_client.return_value = None
            
            # Mock StreamManager to avoid creating real async tasks
            with patch('app.services.proxy.stream_session.StreamManager'):
                # This should not raise an import error
                import asyncio
                result = asyncio.run(session.initialize())
                
                # Verify RedisClient.get_client was called
                mock_redis_client.get_client.assert_called_once()


def test_has_data_with_stream_manager_chunks():
    """Test _has_data returns True when stream manager has received chunks"""
    session = StreamSession(
        stream_id="test_stream",
        ace_id="test_ace_id",
        engine_host="localhost",
        engine_port=6878,
        container_id="test_container"
    )
    
    # Create mock buffer and stream manager
    session.buffer = Mock(spec=StreamBuffer)
    session.buffer.index = 0
    session.buffer._write_buffer = bytearray()
    
    session.stream_manager = Mock(spec=StreamManager)
    session.stream_manager.chunks_received = 5
    
    # Should return True because stream manager has received chunks
    assert session._has_data() is True


def test_has_data_with_buffer_index():
    """Test _has_data returns True when buffer has completed chunks"""
    session = StreamSession(
        stream_id="test_stream",
        ace_id="test_ace_id",
        engine_host="localhost",
        engine_port=6878,
        container_id="test_container"
    )
    
    # Create mock buffer with non-zero index
    session.buffer = Mock(spec=StreamBuffer)
    session.buffer.index = 1
    session.buffer._write_buffer = bytearray()
    
    session.stream_manager = Mock(spec=StreamManager)
    session.stream_manager.chunks_received = 0
    
    # Should return True because buffer has completed chunks
    assert session._has_data() is True


def test_has_data_with_write_buffer():
    """Test _has_data returns True when buffer has data in write buffer"""
    session = StreamSession(
        stream_id="test_stream",
        ace_id="test_ace_id",
        engine_host="localhost",
        engine_port=6878,
        container_id="test_container"
    )
    
    # Create mock buffer with data in write buffer
    session.buffer = Mock(spec=StreamBuffer)
    session.buffer.index = 0
    session.buffer._write_buffer = bytearray(b'some data')
    
    session.stream_manager = Mock(spec=StreamManager)
    session.stream_manager.chunks_received = 0
    
    # Should return True because write buffer has data
    assert session._has_data() is True


def test_has_data_with_no_data():
    """Test _has_data returns False when no data exists"""
    session = StreamSession(
        stream_id="test_stream",
        ace_id="test_ace_id",
        engine_host="localhost",
        engine_port=6878,
        container_id="test_container"
    )
    
    # Create mock buffer with no data
    session.buffer = Mock(spec=StreamBuffer)
    session.buffer.index = 0
    session.buffer._write_buffer = bytearray()
    
    session.stream_manager = Mock(spec=StreamManager)
    session.stream_manager.chunks_received = 0
    
    # Should return False because no data exists
    assert session._has_data() is False


def test_has_data_with_no_buffer_or_manager():
    """Test _has_data returns False when buffer or stream manager is None"""
    session = StreamSession(
        stream_id="test_stream",
        ace_id="test_ace_id",
        engine_host="localhost",
        engine_port=6878,
        container_id="test_container"
    )
    
    # No buffer or stream manager
    assert session._has_data() is False
    
    # Only buffer
    session.buffer = Mock(spec=StreamBuffer)
    session.buffer.index = 0
    session.buffer._write_buffer = bytearray()
    assert session._has_data() is False
    
    # Only stream manager
    session.buffer = None
    session.stream_manager = Mock(spec=StreamManager)
    session.stream_manager.chunks_received = 0
    assert session._has_data() is False
