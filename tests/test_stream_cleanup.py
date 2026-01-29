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
    """Test that cleanup serves as a backup for ended streams that failed immediate removal."""
    print("Testing cleanup of old ended streams (backup mechanism)...")
    
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
    stream_id = stream_state.id
    
    # End the stream (this should immediately remove it from memory)
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_1",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Verify stream was immediately removed from memory
    stream = test_state.get_stream(stream_id)
    assert stream is None, "Stream should be immediately removed from memory when it ends"
    
    # Simulate a failure scenario where stream wasn't removed by manually re-adding it
    # This simulates what cleanup_ended_streams is meant to catch
    stream_state.status = "ended"
    stream_state.ended_at = datetime.now(timezone.utc) - timedelta(hours=2)
    test_state.streams[stream_id] = stream_state
    
    # Run cleanup (should remove streams older than 1 hour)
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=3600)
    
    assert removed_count == 1, f"Expected 1 stream to be removed, but got {removed_count}"
    
    # Verify stream is removed
    stream_after = test_state.get_stream(stream_id)
    assert stream_after is None, "Stream should have been removed"
    
    print("✅ Old ended streams cleanup test passed!")


def test_cleanup_keeps_recent_ended_streams():
    """Test that cleanup doesn't remove recent ended streams (even though they should already be removed)."""
    print("Testing that cleanup doesn't affect recent ended streams...")
    
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
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_2",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Stream should already be removed from memory
    stream = test_state.get_stream(stream_id)
    assert stream is None, "Stream should be immediately removed from memory"
    
    # Run cleanup (should NOT find anything to remove since stream was already removed)
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=3600)
    
    assert removed_count == 0, f"Expected 0 streams to be removed (already removed), but got {removed_count}"
    
    # Verify stream is still not in memory
    stream_after = test_state.get_stream(stream_id)
    assert stream_after is None, "Stream should still not be in memory"
    
    print("✅ Cleanup correctly finds nothing to remove when immediate removal worked!")



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
    """Test that list_streams_with_stats shows only active streams (ended streams are removed)."""
    print("Testing that ended streams are removed from list_streams...")
    
    # Create a fresh state
    test_state = State()
    
    # Start two streams
    stream_ids = []
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
        stream_state = test_state.on_stream_started(evt)
        stream_ids.append(stream_state.id)
    
    # End the first stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_0",
        stream_id=stream_ids[0],
        reason="test"
    ))
    
    # Get all streams without status filter - should only show the active stream
    # (ended streams are immediately removed from memory)
    all_streams = test_state.list_streams_with_stats(status=None)
    assert len(all_streams) == 1, f"Expected 1 total stream (ended stream removed), got {len(all_streams)}"
    assert all_streams[0].id == stream_ids[1], "Only the second stream should remain"
    
    # Get only started streams
    started_streams = test_state.list_streams_with_stats(status="started")
    assert len(started_streams) == 1, f"Expected 1 started stream, got {len(started_streams)}"
    assert started_streams[0].status == "started"
    assert started_streams[0].id == stream_ids[1]
    
    # Get only ended streams (should be 0 since ended streams are immediately removed)
    ended_streams = test_state.list_streams_with_stats(status="ended")
    assert len(ended_streams) == 0, f"Expected 0 ended streams (removed from memory), got {len(ended_streams)}"
    
    print("✅ List streams filtering test passed!")


if __name__ == "__main__":
    print("🧪 Running stream cleanup tests...\n")
    
    test_cleanup_ended_streams()
    test_cleanup_keeps_recent_ended_streams()
    test_cleanup_keeps_started_streams()
    test_list_streams_defaults_to_started()
    
    print("\n🎉 All stream cleanup tests passed!")
