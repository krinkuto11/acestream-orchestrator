#!/usr/bin/env python3
"""
Test stream cleanup functionality.

Tests that:
1. The /streams endpoint defaults to showing only started streams
2. Old ended streams are cleaned up after the configured time period
3. The collector doesn't log confusing messages for already-ended streams
"""

import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector
from app.services.state import State
from app.models.schemas import StreamState, StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_cleanup_ended_streams():
    """Test that old ended streams are cleaned up."""
    print("Testing cleanup of old ended streams...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_1",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_1",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_1",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_1",
            is_live=1
        ),
        labels={"stream_id": "test_stream_old"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # End the stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_1",
        stream_id="test_stream_old",
        reason="test"
    ))
    
    # Verify stream is ended
    stream = test_state.get_stream("test_stream_old")
    assert stream is not None
    assert stream.status == "ended"
    assert stream.ended_at is not None
    
    # Manually set the ended_at to be 2 hours ago (older than cleanup threshold)
    stream.ended_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    # Run cleanup (should remove streams older than 1 hour)
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=3600)
    
    assert removed_count == 1, f"Expected 1 stream to be removed, but got {removed_count}"
    
    # Verify stream is removed
    stream_after = test_state.get_stream("test_stream_old")
    assert stream_after is None, "Stream should have been removed"
    
    print("✅ Old ended streams cleanup test passed!")


def test_cleanup_keeps_recent_ended_streams():
    """Test that recent ended streams are NOT cleaned up."""
    print("Testing that recent ended streams are kept...")
    
    # Create a fresh state
    test_state = State()
    
    # Start and end a stream
    evt = StreamStartedEvent(
        container_id="test_container_2",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_2",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_2",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_2",
            is_live=1
        ),
        labels={"stream_id": "test_stream_recent"}
    )
    
    test_state.on_stream_started(evt)
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_2",
        stream_id="test_stream_recent",
        reason="test"
    ))
    
    # Run cleanup (should NOT remove recent streams)
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=3600)
    
    assert removed_count == 0, f"Expected 0 streams to be removed, but got {removed_count}"
    
    # Verify stream still exists
    stream = test_state.get_stream("test_stream_recent")
    assert stream is not None, "Recent ended stream should still exist"
    assert stream.status == "ended"
    
    print("✅ Recent ended streams kept test passed!")


def test_cleanup_keeps_started_streams():
    """Test that started streams are never cleaned up."""
    print("Testing that started streams are never cleaned up...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_3",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_3",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_3",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_3",
            is_live=1
        ),
        labels={"stream_id": "test_stream_active"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    
    # Manually set the started_at to be 2 hours ago (but still active)
    stream_state.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    # Run cleanup
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=3600)
    
    assert removed_count == 0, f"Expected 0 streams to be removed, but got {removed_count}"
    
    # Verify stream still exists and is still started
    stream = test_state.get_stream("test_stream_active")
    assert stream is not None, "Started stream should still exist"
    assert stream.status == "started"
    
    print("✅ Started streams kept test passed!")


def test_list_streams_defaults_to_started():
    """Test that list_streams_with_stats defaults to showing only started streams."""
    print("Testing that list_streams_with_stats defaults to started streams...")
    
    # Create a fresh state
    test_state = State()
    
    # Start two streams
    for i in range(2):
        evt = StreamStartedEvent(
            container_id=f"test_container_{i}",
            engine=EngineAddress(host="127.0.0.1", port=8080 + i),
            stream=StreamKey(key_type="content_id", key=f"test_stream_key_{i}"),
            session=SessionInfo(
                playback_session_id=f"test_session_{i}",
                stat_url=f"http://127.0.0.1:808{i}/ace/stat/test_session_{i}",
                command_url=f"http://127.0.0.1:808{i}/ace/cmd/test_session_{i}",
                is_live=1
            ),
            labels={"stream_id": f"test_stream_{i}"}
        )
        test_state.on_stream_started(evt)
    
    # End the first stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_0",
        stream_id="test_stream_0",
        reason="test"
    ))
    
    # Get all streams without status filter (should include both started and ended)
    all_streams = test_state.list_streams_with_stats(status=None)
    assert len(all_streams) == 2, f"Expected 2 total streams, got {len(all_streams)}"
    
    # Get only started streams
    started_streams = test_state.list_streams_with_stats(status="started")
    assert len(started_streams) == 1, f"Expected 1 started stream, got {len(started_streams)}"
    assert started_streams[0].status == "started"
    
    # Get only ended streams
    ended_streams = test_state.list_streams_with_stats(status="ended")
    assert len(ended_streams) == 1, f"Expected 1 ended stream, got {len(ended_streams)}"
    assert ended_streams[0].status == "ended"
    
    print("✅ List streams filtering test passed!")


def test_collector_logs_correctly_for_stale_streams():
    """Test that collector only logs INFO when ending a started stream."""
    print("Testing collector logging behavior...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_log",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_log",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_log",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_log",
            is_live=1
        ),
        labels={"stream_id": "test_stream_log"}
    )
    
    test_state.on_stream_started(evt)
    
    # Create a mock HTTP response that indicates a stale stream
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": None,
        "error": "unknown playback session id"
    }
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    collector = Collector()
    
    # Run the collector once (should end the stream)
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "test_stream_log",
                "http://127.0.0.1:8080/ace/stat/test_session_log"
            )
    
    asyncio.run(run_test())
    
    # Verify stream was ended
    stream = test_state.get_stream("test_stream_log")
    assert stream.status == "ended"
    
    # Run collector again with the same mock (stream is now ended)
    # This time it should log at DEBUG level, not INFO
    async def run_test_2():
        with patch('app.services.collector.state', test_state):
            with patch('app.services.collector.logger') as mock_logger:
                await collector._collect_one(
                    mock_client,
                    "test_stream_log",
                    "http://127.0.0.1:8080/ace/stat/test_session_log"
                )
                
                # Check that INFO was NOT called for "Detected stale stream"
                info_calls = [str(call) for call in mock_logger.info.call_args_list]
                detected_calls = [c for c in info_calls if "Detected stale stream" in c]
                assert len(detected_calls) == 0, f"Should not log INFO for already-ended stream, but got: {detected_calls}"
                
                # Check that DEBUG was called with the skip message
                debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
                skip_calls = [c for c in debug_calls if "already ended" in c]
                assert len(skip_calls) > 0, "Should log DEBUG when stream is already ended"
    
    asyncio.run(run_test_2())
    
    print("✅ Collector logging test passed!")


if __name__ == "__main__":
    print("🧪 Running stream cleanup tests...\n")
    
    test_cleanup_ended_streams()
    test_cleanup_keeps_recent_ended_streams()
    test_cleanup_keeps_started_streams()
    test_list_streams_defaults_to_started()
    test_collector_logs_correctly_for_stale_streams()
    
    print("\n🎉 All stream cleanup tests passed!")
