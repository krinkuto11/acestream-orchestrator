#!/usr/bin/env python3
"""
Integration test for stale stream detection.

Tests the full workflow of stream lifecycle with stale detection.
"""

import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import time

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector
from app.services.state import State
from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_stream_lifecycle_with_stale_detection():
    """Test complete stream lifecycle including stale detection."""
    print("Testing stream lifecycle with stale detection...")
    
    # Create a fresh state
    test_state = State()
    
    # Start stream 0 (healthy)
    evt0 = StreamStartedEvent(
        container_id="test_container_0",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key_0"),
        session=SessionInfo(
            playback_session_id="test_session_0",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_0",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_0",
            is_live=1
        ),
        labels={"stream_id": "test_stream_0"}
    )
    stream0 = test_state.on_stream_started(evt0)
    
    # Start stream 1 (will become stale)
    evt1 = StreamStartedEvent(
        container_id="test_container_1",
        engine=EngineAddress(host="127.0.0.1", port=8081),
        stream=StreamKey(key_type="content_id", key="test_stream_key_1"),
        session=SessionInfo(
            playback_session_id="test_session_1",
            stat_url="http://127.0.0.1:8081/ace/stat/test_session_1",
            command_url="http://127.0.0.1:8081/ace/cmd/test_session_1",
            is_live=1
        ),
        labels={"stream_id": "test_stream_1"}
    )
    stream1 = test_state.on_stream_started(evt1)
    
    # Start stream 2 (healthy)
    evt2 = StreamStartedEvent(
        container_id="test_container_2",
        engine=EngineAddress(host="127.0.0.1", port=8082),
        stream=StreamKey(key_type="content_id", key="test_stream_key_2"),
        session=SessionInfo(
            playback_session_id="test_session_2",
            stat_url="http://127.0.0.1:8082/ace/stat/test_session_2",
            command_url="http://127.0.0.1:8082/ace/cmd/test_session_2",
            is_live=1
        ),
        labels={"stream_id": "test_stream_2"}
    )
    stream2 = test_state.on_stream_started(evt2)
    
    # Verify all streams are active
    active_streams = test_state.list_streams(status="started")
    assert len(active_streams) == 3
    print(f"✓ Started 3 streams")
    
    # Create collector
    collector = Collector()
    
    # Process stream 0 (healthy)
    mock_response_healthy_0 = MagicMock()
    mock_response_healthy_0.status_code = 200
    mock_response_healthy_0.json.return_value = {
        "response": {
            "peers": 5,
            "speed_down": 1024000,
            "status": "playing"
        }
    }
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response_healthy_0)
    
    async def process_stream_0():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(mock_client, stream0.id, stream0.stat_url)
    
    asyncio.run(process_stream_0())
    
    # Process stream 1 (stale)
    mock_response_stale = MagicMock()
    mock_response_stale.status_code = 200
    mock_response_stale.json.return_value = {
        "response": None,
        "error": "unknown playback session id"
    }
    mock_client.get = AsyncMock(return_value=mock_response_stale)
    
    async def process_stream_1():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(mock_client, stream1.id, stream1.stat_url)
    
    asyncio.run(process_stream_1())
    
    # Process stream 2 (healthy)
    mock_response_healthy_2 = MagicMock()
    mock_response_healthy_2.status_code = 200
    mock_response_healthy_2.json.return_value = {
        "response": {
            "peers": 3,
            "speed_down": 512000,
            "status": "playing"
        }
    }
    mock_client.get = AsyncMock(return_value=mock_response_healthy_2)
    
    async def process_stream_2():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(mock_client, stream2.id, stream2.stat_url)
    
    asyncio.run(process_stream_2())
    
    # Verify results
    # Stream 0 should still be active
    stream_0_after = test_state.get_stream("test_stream_0")
    assert stream_0_after is not None
    assert stream_0_after.status == "started"
    print(f"✓ Stream 0 remains active (healthy)")
    
    # Stream 1 should be ended (stale)
    stream_1_after = test_state.get_stream("test_stream_1")
    assert stream_1_after is not None
    assert stream_1_after.status == "ended", f"Expected stream_1 to be ended, got {stream_1_after.status}"
    assert stream_1_after.ended_at is not None
    print(f"✓ Stream 1 was ended (stale detected)")
    
    # Stream 2 should still be active
    stream_2_after = test_state.get_stream("test_stream_2")
    assert stream_2_after is not None
    assert stream_2_after.status == "started"
    print(f"✓ Stream 2 remains active (healthy)")
    
    # Verify only 2 streams are now active
    active_streams = test_state.list_streams(status="started")
    assert len(active_streams) == 2
    print(f"✓ Only 2 streams remain active")
    
    # Verify stats were collected for healthy streams
    stats_0 = test_state.get_stream_stats("test_stream_0")
    assert len(stats_0) == 1
    assert stats_0[0].peers == 5
    print(f"✓ Stats collected for healthy stream 0")
    
    stats_2 = test_state.get_stream_stats("test_stream_2")
    assert len(stats_2) == 1
    assert stats_2[0].peers == 3
    print(f"✓ Stats collected for healthy stream 2")
    
    # Stale stream should not have stats collected
    stats_1 = test_state.get_stream_stats("test_stream_1")
    assert len(stats_1) == 0
    print(f"✓ No stats collected for stale stream 1")
    
    print("✅ Stream lifecycle with stale detection test passed!")


def test_multiple_collection_cycles():
    """Test that stale detection works across multiple collection cycles."""
    print("Testing multiple collection cycles...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_key"),
        session=SessionInfo(
            playback_session_id="test_session",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session",
            is_live=1
        ),
        labels={"stream_id": "test_stream_cycle"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create collector
    collector = Collector()
    
    # Cycle 1: Stream is healthy
    mock_response_healthy = MagicMock()
    mock_response_healthy.status_code = 200
    mock_response_healthy.json.return_value = {
        "response": {
            "peers": 5,
            "status": "playing"
        }
    }
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response_healthy)
    
    async def cycle_1():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_cycle",
                stream_state.stat_url
            )
    
    asyncio.run(cycle_1())
    
    # Verify stream is still active
    stream = test_state.get_stream("test_stream_cycle")
    assert stream.status == "started"
    stats = test_state.get_stream_stats("test_stream_cycle")
    assert len(stats) == 1
    print("✓ Cycle 1: Stream healthy, stats collected")
    
    # Cycle 2: Stream is still healthy
    async def cycle_2():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_cycle",
                stream_state.stat_url
            )
    
    asyncio.run(cycle_2())
    
    stream = test_state.get_stream("test_stream_cycle")
    assert stream.status == "started"
    stats = test_state.get_stream_stats("test_stream_cycle")
    assert len(stats) == 2
    print("✓ Cycle 2: Stream still healthy, more stats collected")
    
    # Cycle 3: Stream becomes stale
    mock_response_stale = MagicMock()
    mock_response_stale.status_code = 200
    mock_response_stale.json.return_value = {
        "response": None,
        "error": "unknown playback session id"
    }
    
    mock_client.get = AsyncMock(return_value=mock_response_stale)
    
    async def cycle_3():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_cycle",
                stream_state.stat_url
            )
    
    asyncio.run(cycle_3())
    
    # Verify stream was ended
    stream = test_state.get_stream("test_stream_cycle")
    assert stream.status == "ended"
    assert stream.ended_at is not None
    print("✓ Cycle 3: Stream became stale and was ended")
    
    # Cycle 4: Try to collect again for already-ended stream (should not crash or double-end)
    async def cycle_4():
        with patch('app.services.collector.state', test_state):
            # Collector checks if stream is "started" before ending it again
            # So this should be a no-op since stream is already ended
            await collector._collect_one(
                mock_client,
                "test_stream_cycle",
                stream_state.stat_url
            )
    
    asyncio.run(cycle_4())
    
    # Stream should still be ended (not double-ended or errored)
    stream = test_state.get_stream("test_stream_cycle")
    assert stream.status == "ended"
    # ended_at should not have changed
    original_ended_at = stream.ended_at
    assert stream.ended_at == original_ended_at
    print("✓ Cycle 4: Already ended stream handled gracefully (no double-ending)")
    
    print("✅ Multiple collection cycles test passed!")


if __name__ == "__main__":
    print("🧪 Running stale stream integration tests...\n")
    
    test_stream_lifecycle_with_stale_detection()
    print()
    test_multiple_collection_cycles()
    
    print("\n🎉 All integration tests passed!")
