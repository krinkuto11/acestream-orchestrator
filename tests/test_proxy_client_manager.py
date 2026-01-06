"""Tests for proxy client manager"""

import pytest
import asyncio
import time
from app.services.proxy.client_manager import ClientManager


@pytest.mark.asyncio
async def test_add_client():
    """Test adding clients to stream"""
    manager = ClientManager("test_stream")
    
    # Add first client
    count = await manager.add_client("client1")
    assert count == 1
    assert manager.get_client_count() == 1
    assert manager.has_clients() is True
    
    # Add second client
    count = await manager.add_client("client2")
    assert count == 2
    assert manager.get_client_count() == 2
    
    # Add duplicate client (should not increase count)
    count = await manager.add_client("client1")
    assert count == 2  # Still 2 unique clients


@pytest.mark.asyncio
async def test_remove_client():
    """Test removing clients from stream"""
    manager = ClientManager("test_stream")
    
    # Add clients
    await manager.add_client("client1")
    await manager.add_client("client2")
    await manager.add_client("client3")
    
    # Remove one client
    count = await manager.remove_client("client2")
    assert count == 2
    assert manager.get_client_count() == 2
    
    # Remove non-existent client (should not error)
    count = await manager.remove_client("client999")
    assert count == 2
    
    # Remove all clients
    await manager.remove_client("client1")
    count = await manager.remove_client("client3")
    assert count == 0
    assert manager.has_clients() is False


@pytest.mark.asyncio
async def test_activity_tracking():
    """Test activity timestamp tracking"""
    manager = ClientManager("test_stream")
    
    # Initial activity time
    initial_time = manager.last_activity
    
    # Wait a bit
    await asyncio.sleep(0.1)
    
    # Add client should update activity
    await manager.add_client("client1")
    assert manager.last_activity > initial_time
    
    # Wait and update activity manually
    await asyncio.sleep(0.1)
    before_update = manager.last_activity
    manager.update_activity()
    assert manager.last_activity > before_update


def test_idle_time():
    """Test idle time calculation"""
    manager = ClientManager("test_stream")
    
    # Should start with minimal idle time
    assert manager.get_idle_time() < 0.1
    
    # Wait a bit
    time.sleep(0.2)
    
    # Idle time should be at least 0.2 seconds
    assert manager.get_idle_time() >= 0.2


def test_get_client_ids():
    """Test getting client ID list"""
    manager = ClientManager("test_stream")
    
    # Empty initially
    assert len(manager.get_client_ids()) == 0
    
    # Add clients (synchronously for this test)
    manager.clients.add("client1")
    manager.clients.add("client2")
    manager.clients.add("client3")
    
    # Get client IDs
    ids = manager.get_client_ids()
    assert len(ids) == 3
    assert "client1" in ids
    assert "client2" in ids
    assert "client3" in ids
    
    # Returned set should be a copy
    ids.add("client4")
    assert len(manager.get_client_ids()) == 3


@pytest.mark.asyncio
async def test_concurrent_client_operations():
    """Test thread-safe client operations"""
    manager = ClientManager("test_stream")
    
    # Add multiple clients concurrently
    tasks = [
        manager.add_client(f"client{i}")
        for i in range(10)
    ]
    
    results = await asyncio.gather(*tasks)
    
    # All should complete successfully
    assert len(results) == 10
    assert manager.get_client_count() == 10
    
    # Remove clients concurrently
    tasks = [
        manager.remove_client(f"client{i}")
        for i in range(10)
    ]
    
    results = await asyncio.gather(*tasks)
    
    # All should complete successfully
    assert len(results) == 10
    assert manager.get_client_count() == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
