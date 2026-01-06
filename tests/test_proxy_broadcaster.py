"""Tests for StreamBroadcaster multiplexing"""

import pytest
import asyncio
import httpx
from unittest.mock import Mock, AsyncMock
from app.services.proxy.broadcaster import StreamBroadcaster


class MockStreamContext:
    """Mock async context manager for httpx.stream()"""
    def __init__(self, response):
        self.response = response
    
    async def __aenter__(self):
        return self.response
    
    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_broadcaster_multiple_clients():
    """Test that multiple clients receive the same stream data."""
    # Mock HTTP client
    mock_client = Mock(spec=httpx.AsyncClient)
    
    # Create test chunks
    test_chunks = [b"chunk1", b"chunk2", b"chunk3", b"chunk4", b"chunk5"]
    
    # Mock the streaming response
    async def mock_aiter_bytes(chunk_size):
        for chunk in test_chunks:
            await asyncio.sleep(0.01)  # Simulate network delay
            yield chunk
    
    mock_response = AsyncMock()
    mock_response.aiter_bytes = mock_aiter_bytes
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    
    # Mock the stream method to return context manager
    mock_client.stream = Mock(return_value=MockStreamContext(mock_response))
    
    # Create broadcaster
    broadcaster = StreamBroadcaster(
        stream_id="test_stream",
        playback_url="http://test.com/stream",
        http_client=mock_client
    )
    
    # Start the broadcaster
    await broadcaster.start()
    
    # Wait for first chunk
    await broadcaster.wait_for_first_chunk(timeout=5.0)
    
    # Add multiple clients
    queue1 = await broadcaster.add_client()
    queue2 = await broadcaster.add_client()
    queue3 = await broadcaster.add_client()
    
    # Collect chunks from each client
    client1_chunks = []
    client2_chunks = []
    client3_chunks = []
    
    async def collect_chunks(queue, chunks_list):
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=2.0)
                if chunk is None:
                    break
                chunks_list.append(chunk)
            except asyncio.TimeoutError:
                break
    
    # Collect from all clients concurrently
    await asyncio.gather(
        collect_chunks(queue1, client1_chunks),
        collect_chunks(queue2, client2_chunks),
        collect_chunks(queue3, client3_chunks),
    )
    
    # Stop broadcaster
    await broadcaster.stop()
    
    # Verify all clients received the chunks
    # Each client may have received chunks after they joined
    # but they should all receive the same chunks
    assert len(client1_chunks) > 0
    assert len(client2_chunks) > 0
    assert len(client3_chunks) > 0
    
    # The chunks should be from the test data
    for chunk in client1_chunks:
        assert chunk in test_chunks
    for chunk in client2_chunks:
        assert chunk in test_chunks
    for chunk in client3_chunks:
        assert chunk in test_chunks


@pytest.mark.asyncio
async def test_broadcaster_late_joining_client():
    """Test that late-joining clients receive buffered chunks."""
    # Mock HTTP client
    mock_client = Mock(spec=httpx.AsyncClient)
    
    # Create test chunks
    test_chunks = [b"chunk1", b"chunk2", b"chunk3", b"chunk4", b"chunk5"]
    
    # Mock the streaming response
    async def mock_aiter_bytes(chunk_size):
        for chunk in test_chunks:
            await asyncio.sleep(0.05)  # Simulate network delay
            yield chunk
    
    mock_response = AsyncMock()
    mock_response.aiter_bytes = mock_aiter_bytes
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    
    # Mock the stream method to return context manager
    mock_client.stream = Mock(return_value=MockStreamContext(mock_response))
    
    # Create broadcaster
    broadcaster = StreamBroadcaster(
        stream_id="test_stream",
        playback_url="http://test.com/stream",
        http_client=mock_client
    )
    
    # Start the broadcaster
    await broadcaster.start()
    
    # Wait for first chunk
    await broadcaster.wait_for_first_chunk(timeout=5.0)
    
    # Add first client
    queue1 = await broadcaster.add_client()
    
    # Let some chunks flow
    await asyncio.sleep(0.2)
    
    # Add second client (late joiner)
    queue2 = await broadcaster.add_client()
    
    # Collect chunks from both clients
    client1_chunks = []
    client2_chunks = []
    
    async def collect_chunks(queue, chunks_list, max_chunks=10):
        count = 0
        while count < max_chunks:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
                if chunk is None:
                    break
                chunks_list.append(chunk)
                count += 1
            except asyncio.TimeoutError:
                break
    
    # Collect from both clients
    await asyncio.gather(
        collect_chunks(queue1, client1_chunks),
        collect_chunks(queue2, client2_chunks),
    )
    
    # Stop broadcaster
    await broadcaster.stop()
    
    # Verify client1 got chunks
    assert len(client1_chunks) > 0
    
    # Verify client2 (late joiner) also got chunks immediately from the buffer
    assert len(client2_chunks) > 0
    
    print(f"Client 1 received {len(client1_chunks)} chunks")
    print(f"Client 2 (late joiner) received {len(client2_chunks)} chunks")


@pytest.mark.asyncio
async def test_broadcaster_first_chunk_event():
    """Test that wait_for_first_chunk waits until data is available."""
    # Mock HTTP client
    mock_client = Mock(spec=httpx.AsyncClient)
    
    # Create test chunks with a delay before first chunk
    test_chunks = [b"chunk1", b"chunk2"]
    
    first_chunk_delay = 0.1
    
    # Mock the streaming response
    async def mock_aiter_bytes(chunk_size):
        await asyncio.sleep(first_chunk_delay)  # Delay before first chunk
        for chunk in test_chunks:
            yield chunk
            await asyncio.sleep(0.01)
    
    mock_response = AsyncMock()
    mock_response.aiter_bytes = mock_aiter_bytes
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    
    # Mock the stream method to return context manager
    mock_client.stream = Mock(return_value=MockStreamContext(mock_response))
    
    # Create broadcaster
    broadcaster = StreamBroadcaster(
        stream_id="test_stream",
        playback_url="http://test.com/stream",
        http_client=mock_client
    )
    
    # Start the broadcaster
    await broadcaster.start()
    
    # Measure time to wait for first chunk
    import time
    start_time = time.time()
    await broadcaster.wait_for_first_chunk(timeout=5.0)
    elapsed = time.time() - start_time
    
    # Should have waited at least the first chunk delay
    assert elapsed >= first_chunk_delay
    
    # Should now be able to add a client and get data immediately
    queue = await broadcaster.add_client()
    
    # Get first chunk (should be available in buffer)
    chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert chunk is not None
    
    await broadcaster.stop()


@pytest.mark.asyncio
async def test_broadcaster_client_count():
    """Test that client count is tracked correctly."""
    # Mock HTTP client
    mock_client = Mock(spec=httpx.AsyncClient)
    
    # Create test chunks
    test_chunks = [b"chunk1", b"chunk2", b"chunk3"]
    
    # Mock the streaming response
    async def mock_aiter_bytes(chunk_size):
        for chunk in test_chunks:
            await asyncio.sleep(0.05)
            yield chunk
    
    mock_response = AsyncMock()
    mock_response.aiter_bytes = mock_aiter_bytes
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    
    # Mock the stream method to return context manager
    mock_client.stream = Mock(return_value=MockStreamContext(mock_response))
    
    # Create broadcaster
    broadcaster = StreamBroadcaster(
        stream_id="test_stream",
        playback_url="http://test.com/stream",
        http_client=mock_client
    )
    
    # Start the broadcaster
    await broadcaster.start()
    await broadcaster.wait_for_first_chunk(timeout=5.0)
    
    # Initially no clients
    assert broadcaster.get_client_count() == 0
    
    # Add clients
    queue1 = await broadcaster.add_client()
    assert broadcaster.get_client_count() == 1
    
    queue2 = await broadcaster.add_client()
    assert broadcaster.get_client_count() == 2
    
    queue3 = await broadcaster.add_client()
    assert broadcaster.get_client_count() == 3
    
    # Remove client
    await broadcaster.remove_client(queue1)
    assert broadcaster.get_client_count() == 2
    
    await broadcaster.remove_client(queue2)
    assert broadcaster.get_client_count() == 1
    
    await broadcaster.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
