"""Test for proxy connection race condition fix.

This test verifies that the StreamManager waits for connection establishment
before allowing clients to stream data, preventing the race condition where
clients timeout waiting for data that hasn't started streaming yet.
"""

import pytest
import asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.proxy.stream_manager import StreamManager
from app.services.proxy.stream_buffer import StreamBuffer


class MockStreamResponse:
    """Mock streaming response that simulates slow connection."""
    
    def __init__(self, delay_before_connect: float = 0.1, chunks: int = 5):
        """
        Args:
            delay_before_connect: Simulates network delay before connection
            chunks: Number of chunks to yield
        """
        self.delay_before_connect = delay_before_connect
        self.chunks = chunks
        self.status_code = 200
        
    async def __aenter__(self):
        # Simulate network delay
        await asyncio.sleep(self.delay_before_connect)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
        
    def raise_for_status(self):
        pass
        
    async def aiter_bytes(self, chunk_size):
        """Yield chunks of data."""
        for i in range(self.chunks):
            # Yield 64KB chunks
            yield b'x' * chunk_size
            await asyncio.sleep(0.01)  # Small delay between chunks


@pytest.mark.asyncio
async def test_stream_manager_waits_for_connection():
    """Test that wait_for_connection() properly waits for connection establishment."""
    
    # Create mock buffer
    buffer = MagicMock(spec=StreamBuffer)
    buffer.add_chunk = MagicMock(return_value=True)
    buffer.index = 0
    
    # Create mock HTTP client
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    
    # Use a mock response with 0.2s delay before connection
    mock_response = MockStreamResponse(delay_before_connect=0.2, chunks=3)
    mock_client.stream = MagicMock(return_value=mock_response)
    
    # Create stream manager
    manager = StreamManager(
        stream_id="test_stream",
        playback_url="http://test.example.com/stream",
        buffer=buffer,
        http_client=mock_client
    )
    
    # Start the stream manager (creates async task)
    await manager.start()
    
    # Immediately check - should not be connected yet
    assert not manager.is_connected
    
    # Wait for connection with sufficient timeout
    connection_ok = await manager.wait_for_connection(timeout=5.0)
    
    # Should be connected now
    assert connection_ok
    assert manager.is_connected
    assert manager.error is None
    
    # Clean up
    await manager.stop()


@pytest.mark.asyncio
async def test_stream_manager_connection_timeout():
    """Test that wait_for_connection() times out if connection takes too long."""
    
    # Create mock buffer
    buffer = MagicMock(spec=StreamBuffer)
    buffer.add_chunk = MagicMock(return_value=True)
    
    # Create mock HTTP client
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    
    # Use a mock response with 3s delay (will exceed our timeout)
    mock_response = MockStreamResponse(delay_before_connect=3.0, chunks=1)
    mock_client.stream = MagicMock(return_value=mock_response)
    
    # Create stream manager
    manager = StreamManager(
        stream_id="test_stream_timeout",
        playback_url="http://test.example.com/stream",
        buffer=buffer,
        http_client=mock_client
    )
    
    # Start the stream manager
    await manager.start()
    
    # Wait for connection with short timeout
    connection_ok = await manager.wait_for_connection(timeout=0.5)
    
    # Should timeout and return False
    assert not connection_ok
    
    # Clean up
    await manager.stop()


@pytest.mark.asyncio
async def test_stream_manager_connection_error():
    """Test that wait_for_connection() returns False on connection error."""
    
    # Create mock buffer
    buffer = MagicMock(spec=StreamBuffer)
    
    # Create mock HTTP client that raises error
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    
    # Mock stream() to raise HTTPError
    async def failing_stream(*args, **kwargs):
        raise httpx.HTTPError("Connection failed")
    
    mock_client.stream = failing_stream
    
    # Create stream manager
    manager = StreamManager(
        stream_id="test_stream_error",
        playback_url="http://test.example.com/stream",
        buffer=buffer,
        http_client=mock_client
    )
    
    # Start the stream manager
    await manager.start()
    
    # Wait for connection
    connection_ok = await manager.wait_for_connection(timeout=2.0)
    
    # Should fail
    assert not connection_ok
    assert manager.error is not None
    
    # Clean up
    await manager.stop()


@pytest.mark.asyncio
async def test_race_condition_prevented():
    """Test that the fix prevents the race condition in the original issue.
    
    This simulates the scenario where:
    1. StreamManager.start() is called
    2. Client immediately tries to read data
    3. Without the fix, client might timeout before connection is established
    4. With the fix, we wait for connection before allowing reads
    """
    
    # Create mock buffer
    buffer = MagicMock(spec=StreamBuffer)
    buffer.add_chunk = MagicMock(return_value=True)
    buffer.index = 0
    
    # Create mock HTTP client
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    
    # Simulate slow network - 0.5s before connection establishes
    mock_response = MockStreamResponse(delay_before_connect=0.5, chunks=5)
    mock_client.stream = MagicMock(return_value=mock_response)
    
    # Create stream manager
    manager = StreamManager(
        stream_id="test_race_condition",
        playback_url="http://test.example.com/stream",
        buffer=buffer,
        http_client=mock_client
    )
    
    # Measure time to start and wait for connection
    import time
    start = time.time()
    
    # Start manager (this creates async task but returns immediately)
    await manager.start()
    start_elapsed = time.time() - start
    
    # Start should be quick (< 0.1s) - doesn't wait for connection
    assert start_elapsed < 0.1
    
    # Now wait for connection
    wait_start = time.time()
    connection_ok = await manager.wait_for_connection(timeout=2.0)
    wait_elapsed = time.time() - wait_start
    
    # Wait should take approximately the connection delay time
    assert connection_ok
    # Should wait at least the delay time
    assert wait_elapsed >= 0.4  # Allow some tolerance
    # But not too much longer (< 1s)
    assert wait_elapsed < 1.0
    
    # Connection should be established
    assert manager.is_connected
    
    # Clean up
    await manager.stop()


@pytest.mark.asyncio  
async def test_connection_event_set_on_error():
    """Test that connection_event is set even when connection fails.
    
    This is important to prevent wait_for_connection() from hanging forever
    when the connection fails.
    """
    
    # Create mock buffer
    buffer = MagicMock(spec=StreamBuffer)
    
    # Create mock HTTP client that fails immediately
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    
    async def immediate_error(*args, **kwargs):
        # Fail quickly
        await asyncio.sleep(0.05)
        raise httpx.ConnectError("Immediate connection failure")
    
    mock_client.stream = immediate_error
    
    # Create stream manager
    manager = StreamManager(
        stream_id="test_event_on_error",
        playback_url="http://test.example.com/stream",
        buffer=buffer,
        http_client=mock_client
    )
    
    # Start manager
    await manager.start()
    
    # Wait for connection (should not hang even though it fails)
    import time
    start = time.time()
    connection_ok = await manager.wait_for_connection(timeout=5.0)
    elapsed = time.time() - start
    
    # Should complete quickly (not wait full 5s timeout)
    assert elapsed < 1.0
    
    # Should return False
    assert not connection_ok
    
    # Error should be set
    assert manager.error is not None
    
    # Clean up
    await manager.stop()
