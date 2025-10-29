#!/usr/bin/env python3
"""
Test stale stream detection functionality.

Tests that the collector properly detects and handles stale streams
when the stat endpoint returns {"response": null, "error": "unknown playback session id"}.
"""

import sys
import os
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector
from app.services.state import State
from app.models.schemas import StreamState, StreamStartedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_stale_stream_detection():
    """Test that collector detects and handles stale streams."""
    print("Testing stale stream detection...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_123",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_123",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_123",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_123",
            is_live=1
        ),
        labels={"stream_id": "test_stream_1"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    assert stream_state.id == "test_stream_1"
    
    # Verify stream is in state
    streams = test_state.list_streams(status="started")
    assert len(streams) == 1
    assert streams[0].id == "test_stream_1"
    
    # Create a mock HTTP response that indicates a stale stream
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": None,
        "error": "unknown playback session id"
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector and patch the state
    collector = Collector()
    
    # Run the collector once with patched state
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_1",
                "http://127.0.0.1:8080/ace/stat/test_session_123"
            )
    
    asyncio.run(run_test())
    
    # Verify stream was ended
    stream_after = test_state.get_stream("test_stream_1")
    assert stream_after is not None
    assert stream_after.status == "ended", f"Expected stream to be ended, but status is {stream_after.status}"
    assert stream_after.ended_at is not None
    
    # Verify stream is no longer in "started" list
    started_streams = test_state.list_streams(status="started")
    assert len(started_streams) == 0
    
    print("✅ Stale stream detection test passed!")


def test_normal_stream_continues():
    """Test that normal streams with valid responses continue normally."""
    print("Testing normal stream continues with valid response...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_456",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_456",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_456",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_456",
            is_live=1
        ),
        labels={"stream_id": "test_stream_2"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create a mock HTTP response with valid stream data
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "peers": 5,
            "speed_down": 1024000,
            "speed_up": 256000,
            "downloaded": 10485760,
            "uploaded": 2621440,
            "status": "playing"
        }
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector
    collector = Collector()
    
    # Run the collector once with patched state
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_2",
                "http://127.0.0.1:8080/ace/stat/test_session_456"
            )
    
    asyncio.run(run_test())
    
    # Verify stream is still active
    stream_after = test_state.get_stream("test_stream_2")
    assert stream_after is not None
    assert stream_after.status == "started"
    assert stream_after.ended_at is None
    
    # Verify stream stats were collected
    stats = test_state.get_stream_stats("test_stream_2")
    assert len(stats) == 1
    assert stats[0].peers == 5
    assert stats[0].speed_down == 1024000
    
    print("✅ Normal stream continues test passed!")


def test_other_errors_do_not_end_stream():
    """Test that other error responses don't prematurely end streams."""
    print("Testing that other errors don't end stream...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_789",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_789",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_789",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_789",
            is_live=1
        ),
        labels={"stream_id": "test_stream_3"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create a mock HTTP response with a different error
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": None,
        "error": "some other error"
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector
    collector = Collector()
    
    # Run the collector once with patched state
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_3",
                "http://127.0.0.1:8080/ace/stat/test_session_789"
            )
    
    asyncio.run(run_test())
    
    # Verify stream is still active (not ended by this different error)
    stream_after = test_state.get_stream("test_stream_3")
    assert stream_after is not None
    assert stream_after.status == "started"
    assert stream_after.ended_at is None
    
    print("✅ Other errors don't end stream test passed!")


def test_http_errors_do_not_end_stream():
    """Test that HTTP errors (4xx, 5xx) don't end streams."""
    print("Testing that HTTP errors don't end stream...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_999",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_999",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_999",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_999",
            is_live=1
        ),
        labels={"stream_id": "test_stream_4"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create a mock HTTP response with 500 error
    mock_response = MagicMock()
    mock_response.status_code = 500
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector
    collector = Collector()
    
    # Run the collector once with patched state
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_4",
                "http://127.0.0.1:8080/ace/stat/test_session_999"
            )
    
    asyncio.run(run_test())
    
    # Verify stream is still active
    stream_after = test_state.get_stream("test_stream_4")
    assert stream_after is not None
    assert stream_after.status == "started"
    assert stream_after.ended_at is None
    
    print("✅ HTTP errors don't end stream test passed!")


if __name__ == "__main__":
    print("🧪 Running stale stream detection tests...\n")
    
    test_stale_stream_detection()
    test_normal_stream_continues()
    test_other_errors_do_not_end_stream()
    test_http_errors_do_not_end_stream()
    
    print("\n🎉 All stale stream detection tests passed!")
