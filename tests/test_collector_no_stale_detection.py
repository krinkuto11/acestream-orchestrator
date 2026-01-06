#!/usr/bin/env python3
"""
Test that the collector no longer performs stale stream detection.

This test verifies that:
1. The collector skips stats collection when errors are returned
2. The collector does NOT automatically stop/end streams
3. Stream lifecycle is managed by Acexy
"""

import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_collector_skips_error_responses():
    """Test that collector skips stats collection for error responses without ending stream."""
    print("Testing collector handling of error responses...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session",
            is_live=1
        ),
        labels={"stream_id": "test_stream"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create a mock HTTP response with "unknown playback session id" error
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": None,
        "error": "unknown playback session id"
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector
    collector = Collector()
    
    # Run the collector once
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream",
                "http://127.0.0.1:8080/ace/stat/test_session"
            )
    
    asyncio.run(run_test())
    
    # Verify stream is STILL ACTIVE (not ended)
    stream_after = test_state.get_stream("test_stream")
    assert stream_after is not None
    assert stream_after.status == "started", f"Stream should still be started, but status is {stream_after.status}"
    assert stream_after.ended_at is None, "Stream should not have ended_at timestamp"
    
    # Verify no stats were collected (because error was returned)
    stats = test_state.get_stream_stats("test_stream")
    assert len(stats) == 0, "No stats should be collected for error responses"
    
    print("✅ Collector correctly skips error responses without ending stream")


def test_collector_no_stop_stream_method():
    """Test that collector no longer has _stop_stream method."""
    print("Testing that collector doesn't have _stop_stream method...")
    
    collector = Collector()
    
    assert not hasattr(collector, '_stop_stream'), "Collector should not have _stop_stream method"
    
    print("✅ Collector does not have _stop_stream method")


def test_collector_collect_one_signature():
    """Test that _collect_one no longer requires command_url parameter."""
    print("Testing _collect_one method signature...")
    
    import inspect
    collector = Collector()
    sig = inspect.signature(collector._collect_one)
    params = list(sig.parameters.keys())
    
    assert 'client' in params, "_collect_one should have client parameter"
    assert 'stream_id' in params, "_collect_one should have stream_id parameter"
    assert 'stat_url' in params, "_collect_one should have stat_url parameter"
    assert 'command_url' not in params, "_collect_one should NOT have command_url parameter"
    
    print(f"✅ _collect_one has correct signature: {params}")


def test_collector_handles_normal_stats():
    """Test that collector still collects normal stream stats."""
    print("Testing collector still collects normal stats...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_stats",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_stats",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_stats",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_stats",
            is_live=1
        ),
        labels={"stream_id": "test_stream_stats"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create a mock HTTP response with valid stats
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "peers": 10,
            "speed_down": 2048000,
            "speed_up": 512000,
            "downloaded": 20971520,
            "uploaded": 5242880,
            "status": "playing"
        }
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector
    collector = Collector()
    
    # Run the collector once
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_stats",
                "http://127.0.0.1:8080/ace/stat/test_session_stats"
            )
    
    asyncio.run(run_test())
    
    # Verify stream is still active
    stream_after = test_state.get_stream("test_stream_stats")
    assert stream_after is not None
    assert stream_after.status == "started"
    
    # Verify stats were collected
    stats = test_state.get_stream_stats("test_stream_stats")
    assert len(stats) == 1, "Stats should be collected for normal responses"
    assert stats[0].peers == 10
    assert stats[0].speed_down == 2048000
    
    print("✅ Collector still collects normal stats correctly")


if __name__ == "__main__":
    print("🧪 Testing that collector no longer performs stale stream detection...\n")
    
    test_collector_skips_error_responses()
    print()
    test_collector_no_stop_stream_method()
    print()
    test_collector_collect_one_signature()
    print()
    test_collector_handles_normal_stats()
    
    print("\n🎉 All tests passed!")
    print("\nVerified that:")
    print("  • Collector does NOT automatically stop/end streams on errors")
    print("  • Collector skips stats collection when errors occur")
    print("  • Collector still collects stats for normal responses")
    print("  • Stream lifecycle is now managed by Acexy")
