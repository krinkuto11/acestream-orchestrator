#!/usr/bin/env python3
"""
Test that streams are immediately removed from memory when they end.

This tests the requirement: "Once a stream ends, make sure to remove it from the /streams endpoint."
The stale stream removal routine (cleanup_ended_streams) should be used as a backup in case this fails.
"""

import sys
import os
from datetime import datetime, timezone, timedelta

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.state import State
from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_stream_removed_immediately_on_end():
    """Test that a stream is removed from memory immediately when it ends."""
    print("Testing immediate removal of ended streams...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_immediate",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_immediate",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_immediate",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_immediate",
            is_live=1
        ),
        labels={"stream_id": "test_stream_immediate"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    stream_id = stream_state.id
    
    # Verify stream is in memory
    stream = test_state.get_stream(stream_id)
    assert stream is not None, "Stream should exist in memory after starting"
    assert stream.status == "started"
    
    # Verify stream appears in list_streams
    all_streams = test_state.list_streams()
    assert len(all_streams) == 1, f"Expected 1 stream, got {len(all_streams)}"
    assert all_streams[0].id == stream_id
    
    # End the stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_immediate",
        stream_id=stream_id,
        reason="test"
    ))
    
    # CRITICAL: Stream should be immediately removed from memory
    stream_after = test_state.get_stream(stream_id)
    assert stream_after is None, "Stream should be immediately removed from memory when it ends"
    
    # Verify stream does NOT appear in list_streams
    all_streams_after = test_state.list_streams()
    assert len(all_streams_after) == 0, f"Expected 0 streams, got {len(all_streams_after)}"
    
    # Verify stream does NOT appear in list_streams with status filter
    started_streams = test_state.list_streams(status="started")
    assert len(started_streams) == 0, f"Expected 0 started streams, got {len(started_streams)}"
    
    ended_streams = test_state.list_streams(status="ended")
    assert len(ended_streams) == 0, f"Expected 0 ended streams in memory, got {len(ended_streams)}"
    
    print("✅ Stream immediately removed from memory when ended!")


def test_stream_stats_removed_immediately_on_end():
    """Test that stream stats are removed from memory immediately when a stream ends."""
    print("Testing immediate removal of stream stats...")
    
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
    stream_id = stream_state.id
    
    # Add some fake stats
    from app.models.schemas import StreamStatSnapshot
    test_state.stream_stats[stream_id] = [
        StreamStatSnapshot(
            ts=datetime.now(timezone.utc),
            peers=10,
            speed_down=100,
            speed_up=50,
            downloaded=1000,
            uploaded=500,
            livepos=None
        )
    ]
    
    # Verify stats exist
    assert stream_id in test_state.stream_stats, "Stats should exist before ending stream"
    assert len(test_state.stream_stats[stream_id]) == 1
    
    # End the stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_stats",
        stream_id=stream_id,
        reason="test"
    ))
    
    # CRITICAL: Stats should be immediately removed from memory
    assert stream_id not in test_state.stream_stats, "Stream stats should be immediately removed when stream ends"
    
    print("✅ Stream stats immediately removed from memory when stream ended!")


def test_multiple_streams_selective_removal():
    """Test that only the ended stream is removed, not other active streams."""
    print("Testing selective removal of ended streams...")
    
    # Create a fresh state
    test_state = State()
    
    # Start two streams
    stream_ids = []
    for i in range(2):
        evt = StreamStartedEvent(
            container_id=f"test_container_multi_{i}",
            engine=EngineAddress(host="127.0.0.1", port=8080 + i),
            stream=StreamKey(key_type="content_id", key=f"test_stream_key_{i}"),
            session=SessionInfo(
                playback_session_id=f"test_session_multi_{i}",
                stat_url=f"http://127.0.0.1:808{i}/ace/stat/test_session_multi_{i}",
                command_url=f"http://127.0.0.1:808{i}/ace/cmd/test_session_multi_{i}",
                is_live=1
            ),
            labels={"stream_id": f"test_stream_multi_{i}"}
        )
        stream_state = test_state.on_stream_started(evt)
        stream_ids.append(stream_state.id)
    
    # Verify both streams exist
    all_streams = test_state.list_streams()
    assert len(all_streams) == 2, f"Expected 2 streams, got {len(all_streams)}"
    
    # End only the first stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_multi_0",
        stream_id=stream_ids[0],
        reason="test"
    ))
    
    # Verify first stream is removed
    stream_0 = test_state.get_stream(stream_ids[0])
    assert stream_0 is None, "First stream should be removed"
    
    # Verify second stream still exists
    stream_1 = test_state.get_stream(stream_ids[1])
    assert stream_1 is not None, "Second stream should still exist"
    assert stream_1.status == "started"
    
    # Verify list_streams shows only one stream
    all_streams_after = test_state.list_streams()
    assert len(all_streams_after) == 1, f"Expected 1 stream remaining, got {len(all_streams_after)}"
    assert all_streams_after[0].id == stream_ids[1]
    
    print("✅ Selective removal works correctly!")


def test_cleanup_as_backup_mechanism():
    """Test that cleanup_ended_streams serves as a backup for failed immediate removal."""
    print("Testing cleanup_ended_streams as backup mechanism...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_backup",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_backup",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_backup",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_backup",
            is_live=1
        ),
        labels={"stream_id": "test_stream_backup"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Simulate a scenario where immediate removal failed by manually re-adding the stream
    # as an ended stream (this would only happen in an error scenario)
    stream_state.status = "ended"
    stream_state.ended_at = datetime.now(timezone.utc) - timedelta(hours=2)
    test_state.streams[stream_id] = stream_state
    
    # Run cleanup (should remove the stale ended stream)
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=3600)
    
    assert removed_count == 1, f"Expected 1 stream to be removed by backup cleanup, got {removed_count}"
    
    # Verify stream is removed
    stream_after = test_state.get_stream(stream_id)
    assert stream_after is None, "Stream should be removed by backup cleanup"
    
    print("✅ Backup cleanup mechanism works correctly!")


def test_normal_case_cleanup_removes_nothing():
    """Test that cleanup normally doesn't find anything to remove (immediate removal works)."""
    print("Testing that cleanup normally finds nothing to remove...")
    
    # Create a fresh state
    test_state = State()
    
    # Start and end a stream normally
    evt = StreamStartedEvent(
        container_id="test_container_normal",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_normal",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_normal",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_normal",
            is_live=1
        ),
        labels={"stream_id": "test_stream_normal"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # End the stream normally
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_normal",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Run cleanup - should find nothing to remove since stream was already removed
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=0)  # Even with 0 seconds threshold
    
    assert removed_count == 0, f"Expected 0 streams to be removed (already removed immediately), got {removed_count}"
    
    print("✅ Normal case: cleanup finds nothing to remove (immediate removal worked)!")


if __name__ == "__main__":
    print("🧪 Running stream immediate removal tests...\n")
    
    test_stream_removed_immediately_on_end()
    test_stream_stats_removed_immediately_on_end()
    test_multiple_streams_selective_removal()
    test_cleanup_as_backup_mechanism()
    test_normal_case_cleanup_removes_nothing()
    
    print("\n🎉 All stream immediate removal tests passed!")
