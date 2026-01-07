"""Tests for buffer-based stream multiplexing components"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock
from app.services.proxy.stream_buffer import StreamBuffer
from app.services.proxy.stream_manager import StreamManager
from app.services.proxy.stream_generator import StreamGenerator


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


def test_stream_buffer_add_chunk(mock_redis):
    """Test adding chunks to buffer."""
    buffer = StreamBuffer("test_stream", redis_client=mock_redis)
    
    # Add a chunk (188 bytes = 1 TS packet)
    chunk = b'\x47' + b'\x00' * 187
    assert buffer.add_chunk(chunk)
    
    # Index shouldn't increment yet (need more data for target chunk size)
    assert buffer.index == 0


def test_stream_buffer_ts_packet_alignment():
    """Test that buffer properly aligns TS packets."""
    buffer = StreamBuffer("test_stream", redis_client=None)  # In-memory mode
    
    # Create multiple TS packets
    ts_packet = b'\x47' + b'\x00' * 187  # 188 bytes
    
    # Add enough packets to exceed target chunk size
    for _ in range(2000):  # 2000 * 188 = 376KB > 320KB target
        buffer.add_chunk(ts_packet)
    
    # Should have written at least one chunk
    assert buffer.index > 0


def test_stream_buffer_get_chunk_memory():
    """Test retrieving chunks from memory."""
    buffer = StreamBuffer("test_stream", redis_client=None)
    
    # Manually add a chunk to memory
    test_data = b"test chunk data"
    buffer._memory_chunks.append((1, test_data))
    buffer.index = 1
    
    # Retrieve it
    chunk = buffer.get_chunk(1)
    assert chunk == test_data


def test_stream_buffer_get_chunks_from():
    """Test retrieving multiple chunks."""
    buffer = StreamBuffer("test_stream", redis_client=None)
    
    # Add chunks to memory
    buffer._memory_chunks.append((1, b"chunk1"))
    buffer._memory_chunks.append((2, b"chunk2"))
    buffer._memory_chunks.append((3, b"chunk3"))
    buffer.index = 3
    
    # Get chunks starting from index 1
    chunks, next_index = buffer.get_chunks_from(1, count=2)
    
    assert len(chunks) == 2
    assert chunks[0] == b"chunk1"
    assert chunks[1] == b"chunk2"
    assert next_index == 3


@pytest.mark.asyncio
async def test_stream_manager_start_stop():
    """Test starting and stopping stream manager."""
    mock_client = Mock()
    buffer = StreamBuffer("test_stream", redis_client=None)
    
    manager = StreamManager(
        stream_id="test_stream",
        playback_url="http://test.com/stream",
        buffer=buffer,
        http_client=mock_client
    )
    
    # Initially not running
    assert not manager.is_running
    
    # Start (will fail but that's ok for this test)
    await manager.start()
    assert manager.is_running
    
    # Stop
    await manager.stop()
    assert not manager.is_running


@pytest.mark.asyncio
async def test_stream_generator_basic():
    """Test basic stream generation from buffer."""
    buffer = StreamBuffer("test_stream", redis_client=None)
    
    # Add some chunks to buffer
    ts_packet = b'\x47' + b'\x00' * 187
    test_chunk = ts_packet * 100  # 100 packets
    buffer._memory_chunks.append((1, test_chunk))
    buffer.index = 1
    
    generator = StreamGenerator(
        stream_id="test_stream",
        client_id="test_client",
        buffer=buffer,
        initial_behind=0  # Start at beginning
    )
    
    # Generate and collect chunks
    chunks = []
    async for chunk in generator.generate():
        chunks.append(chunk)
        # Only get one chunk for this test
        break
    
    assert len(chunks) == 1
    assert chunks[0] == test_chunk


@pytest.mark.asyncio
async def test_stream_generator_empty_buffer_timeout():
    """Test that generator times out when buffer is empty."""
    buffer = StreamBuffer("test_stream", redis_client=None)
    buffer.index = 0  # Empty buffer
    
    generator = StreamGenerator(
        stream_id="test_stream",
        client_id="test_client",
        buffer=buffer,
        initial_behind=0
    )
    
    # Should timeout quickly since buffer is empty
    chunks = []
    start = asyncio.get_event_loop().time()
    
    try:
        async for chunk in generator.generate():
            chunks.append(chunk)
            # Should exit from timeout before getting here
            if asyncio.get_event_loop().time() - start > 5:
                break
    except Exception:
        pass
    
    # Should have minimal chunks (maybe some keepalives)
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 40  # Should timeout in 30 seconds


@pytest.mark.asyncio
async def test_stream_generator_catchup():
    """Test that generator catches up when behind."""
    buffer = StreamBuffer("test_stream", redis_client=None)
    
    # Add many chunks to buffer
    for i in range(1, 60):  # 59 chunks
        buffer._memory_chunks.append((i, b"chunk" + str(i).encode()))
    buffer.index = 59
    
    generator = StreamGenerator(
        stream_id="test_stream",
        client_id="test_client",
        buffer=buffer,
        initial_behind=3
    )
    
    # Should start at index 56 (59 - 3)
    chunks = []
    async for chunk in generator.generate():
        chunks.append(chunk)
        if len(chunks) >= 3:  # Get a few chunks
            break
    
    # Should have caught up
    assert len(chunks) >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
