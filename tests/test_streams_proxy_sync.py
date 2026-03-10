#!/usr/bin/env python3
"""
Deep investigation tests for synchronization between /streams endpoint and proxy.

These tests expose edge cases where streams disappear from UI but remain active in proxy.
"""

import sys
import os
from datetime import datetime, timezone
import time
import threading

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.state import State
from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_stream_removal_atomicity():
    """Test that stream removal from memory is atomic and immediate."""
    print("Testing stream removal atomicity...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_atomic",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_atomic",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_atomic",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_atomic",
            is_live=1
        ),
        labels={"stream_id": "test_stream_atomic"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Track if stream is visible during concurrent access
    visibility_tracker = []
    stop_flag = threading.Event()
    
    def check_visibility():
        """Continuously check if stream is visible."""
        while not stop_flag.is_set():
            streams = test_state.list_streams_with_stats(status="started")
            stream_ids = [s.id for s in streams]
            visibility_tracker.append(stream_id in stream_ids)
            time.sleep(0.001)  # Check every 1ms
    
    # Start background thread checking visibility
    checker_thread = threading.Thread(target=check_visibility, daemon=True)
    checker_thread.start()
    
    # Let it run for a bit with stream active
    time.sleep(0.1)
    
    # End the stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_atomic",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Continue checking for a bit after ending
    time.sleep(0.1)
    stop_flag.set()
    checker_thread.join(timeout=1.0)
    
    # Analyze visibility tracker
    # Before ending: should be True
    # After ending: should be False
    # There should be NO brief appearances after it disappears
    
    if len(visibility_tracker) > 0:
        # Find the transition point
        first_false_index = None
        for i, visible in enumerate(visibility_tracker):
            if not visible:
                first_false_index = i
                break
        
        if first_false_index is not None:
            # Check that stream never reappears after disappearing
            after_removal = visibility_tracker[first_false_index:]
            assert not any(after_removal), \
                f"Stream reappeared after removal! Visibility pattern: {visibility_tracker[max(0, first_false_index-5):first_false_index+10]}"
    
    # Final verification
    final_streams = test_state.list_streams_with_stats(status="started")
    assert stream_id not in [s.id for s in final_streams], "Stream should not be visible after ending"
    
    print("✅ Stream removal is atomic!")


def test_concurrent_stream_access_during_removal():
    """Test that concurrent access during removal doesn't cause race conditions."""
    print("Testing concurrent access during stream removal...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_concurrent",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_concurrent",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_concurrent",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_concurrent",
            is_live=1
        ),
        labels={"stream_id": "test_stream_concurrent"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    errors = []
    
    def concurrent_reader():
        """Continuously read stream list."""
        try:
            for _ in range(100):
                streams = test_state.list_streams_with_stats(status="started")
                # This should never throw an exception
                _ = [s.id for s in streams]
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"Reader error: {e}")
    
    # Start multiple reader threads
    readers = [threading.Thread(target=concurrent_reader, daemon=True) for _ in range(5)]
    for t in readers:
        t.start()
    
    # Let readers run for a bit
    time.sleep(0.05)
    
    # End the stream while readers are active
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_concurrent",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Wait for readers to finish
    for t in readers:
        t.join(timeout=2.0)
    
    # Check for errors
    assert len(errors) == 0, f"Concurrent access caused errors: {errors}"
    
    print("✅ Concurrent access is safe!")


def test_stream_stats_consistency():
    """Test that stream stats are always consistent with stream presence."""
    print("Testing stream stats consistency...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_stats_consistency",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_stats_consistency",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_stats_consistency",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_stats_consistency",
            is_live=1
        ),
        labels={"stream_id": "test_stream_stats_consistency"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Add some stats
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
    
    # Verify stream and stats are both present
    streams = test_state.list_streams_with_stats(status="started")
    assert len(streams) == 1, "Stream should be visible"
    assert stream_id in test_state.stream_stats, "Stats should be present"
    
    # End the stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_stats_consistency",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Verify both stream and stats are removed together
    streams_after = test_state.list_streams_with_stats(status="started")
    assert len(streams_after) == 0, "Stream should not be visible"
    assert stream_id not in test_state.stream_stats, "Stats should be removed"
    
    # Verify they stay removed (no resurrection)
    time.sleep(0.05)
    streams_final = test_state.list_streams_with_stats(status="started")
    assert len(streams_final) == 0, "Stream should stay removed"
    assert stream_id not in test_state.stream_stats, "Stats should stay removed"
    
    print("✅ Stream stats consistency verified!")


def test_filter_consistency_after_removal():
    """Test that status filters work correctly after stream removal."""
    print("Testing filter consistency after stream removal...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_filter",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_filter",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_filter",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_filter",
            is_live=1
        ),
        labels={"stream_id": "test_stream_filter"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Verify stream appears with different filters
    all_streams = test_state.list_streams_with_stats()
    started_streams = test_state.list_streams_with_stats(status="started")
    ended_streams = test_state.list_streams_with_stats(status="ended")
    
    assert len(all_streams) == 1, "Should have 1 stream in all"
    assert len(started_streams) == 1, "Should have 1 started stream"
    assert len(ended_streams) == 0, "Should have 0 ended streams"
    
    # End the stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_filter",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Verify stream doesn't appear with any filter
    all_streams_after = test_state.list_streams_with_stats()
    started_streams_after = test_state.list_streams_with_stats(status="started")
    ended_streams_after = test_state.list_streams_with_stats(status="ended")
    
    assert len(all_streams_after) == 0, "Should have 0 streams in all"
    assert len(started_streams_after) == 0, "Should have 0 started streams"
    assert len(ended_streams_after) == 0, "Should have 0 ended streams (immediate removal)"
    
    print("✅ Filter consistency verified!")


def test_engine_stream_reference_cleanup():
    """Test that engine's stream references are cleaned up when stream ends."""
    print("Testing engine stream reference cleanup...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_engine_ref",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_engine_ref",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_engine_ref",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_engine_ref",
            is_live=1
        ),
        labels={"stream_id": "test_stream_engine_ref"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Verify engine has the stream reference
    engine = test_state.get_engine("test_container_engine_ref")
    if engine:
        assert stream_id in engine.streams, "Engine should reference the stream"
    
    # End the stream
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="test_container_engine_ref",
        stream_id=stream_id,
        reason="test"
    ))
    
    # Verify engine's stream reference is removed
    engine_after = test_state.get_engine("test_container_engine_ref")
    if engine_after:
        assert stream_id not in engine_after.streams, "Engine should not reference the ended stream"
    
    print("✅ Engine stream reference cleanup verified!")


if __name__ == "__main__":
    print("🧪 Running deep synchronization tests...\n")
    
    test_stream_removal_atomicity()
    test_concurrent_stream_access_during_removal()
    test_stream_stats_consistency()
    test_filter_consistency_after_removal()
    test_engine_stream_reference_cleanup()
    
    print("\n🎉 All synchronization tests passed!")
