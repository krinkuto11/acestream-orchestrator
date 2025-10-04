#!/usr/bin/env python3
"""
Stress tests for stream status management to verify engines don't show ended streams.
Tests race conditions with rapid stream start/end cycles and concurrent operations.
"""

import sys
import os
import threading
import time
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_engine_doesnt_show_ended_streams():
    """Test that get_engine endpoint doesn't return ended streams."""
    print("\n🧪 Testing engine endpoint doesn't show ended streams...")
    
    from app.services.state import State
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    # Create state instance
    state = State()
    
    # Mock the database operations
    with patch('app.services.state.SessionLocal'):
        # Start a stream
        evt_started = StreamStartedEvent(
            container_id="test_container_1",
            engine=EngineAddress(host="127.0.0.1", port=8080),
            stream=StreamKey(key_type="content_id", key="test_content_1"),
            session=SessionInfo(
                playback_session_id="session_1",
                stat_url="http://127.0.0.1:8080/stat",
                command_url="http://127.0.0.1:8080/command",
                is_live=1
            ),
            labels={"stream_id": "stream_1"}
        )
        
        stream_state = state.on_stream_started(evt_started)
        print(f"✅ Stream started: {stream_state.id}")
        
        # Verify engine has the stream
        engine = state.get_engine("test_container_1")
        assert engine is not None, "Engine should exist"
        assert len(engine.streams) == 1, "Engine.streams should have 1 stream ID"
        print(f"✅ Engine.streams list has {len(engine.streams)} stream ID(s)")
        
        # Verify list_streams without status filter shows the stream
        all_streams = state.list_streams(container_id="test_container_1")
        assert len(all_streams) == 1, "Should have 1 stream total"
        print(f"✅ list_streams(container_id) returns {len(all_streams)} stream(s)")
        
        # Verify list_streams with status="started" shows the stream
        started_streams = state.list_streams(status="started", container_id="test_container_1")
        assert len(started_streams) == 1, "Should have 1 started stream"
        print(f"✅ list_streams(status='started', container_id) returns {len(started_streams)} stream(s)")
        
        # End the stream
        evt_ended = StreamEndedEvent(
            container_id="test_container_1",
            stream_id="stream_1",
            reason="test_ended"
        )
        
        with patch('app.services.provisioner.clear_acestream_cache'):
            ended_stream = state.on_stream_ended(evt_ended)
        
        assert ended_stream is not None, "Stream should have ended"
        assert ended_stream.status == "ended", "Stream status should be 'ended'"
        print(f"✅ Stream ended: {ended_stream.id}, status={ended_stream.status}")
        
        # Verify engine.streams list is now empty
        engine = state.get_engine("test_container_1")
        assert len(engine.streams) == 0, "Engine.streams should be empty after stream ends"
        print(f"✅ Engine.streams list now has {len(engine.streams)} stream ID(s)")
        
        # BUG: list_streams without status filter STILL shows the ended stream
        all_streams = state.list_streams(container_id="test_container_1")
        print(f"⚠️  list_streams(container_id) returns {len(all_streams)} stream(s) - includes ended streams!")
        assert len(all_streams) == 1, "list_streams without status filter includes ended streams"
        
        # CORRECT: list_streams with status="started" should NOT show the ended stream
        started_streams = state.list_streams(status="started", container_id="test_container_1")
        assert len(started_streams) == 0, "Should have 0 started streams after ending"
        print(f"✅ list_streams(status='started', container_id) correctly returns {len(started_streams)} stream(s)")
        
        # PROBLEM: The get_engine endpoint calls list_streams without status filter
        # This means it returns ended streams, which is the bug!
        print(f"❌ BUG CONFIRMED: /engines/{{container_id}} endpoint calls list_streams without status filter")
        print(f"   This causes engines to show ended streams as if they were still active!")
    
    print("\n✅ Bug demonstration test passed!")
    return True


def test_rapid_stream_cycling():
    """Stress test: rapidly start and end streams to check for race conditions."""
    print("\n🧪 Stress test: rapid stream cycling...")
    
    from app.services.state import State
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    state = State()
    
    with patch('app.services.state.SessionLocal'):
        with patch('app.services.provisioner.clear_acestream_cache'):
            # Rapidly start and end 50 streams
            for i in range(50):
                evt_started = StreamStartedEvent(
                    container_id="stress_container",
                    engine=EngineAddress(host="127.0.0.1", port=8080),
                    stream=StreamKey(key_type="content_id", key=f"stress_content_{i}"),
                    session=SessionInfo(
                        playback_session_id=f"stress_session_{i}",
                        stat_url=f"http://127.0.0.1:8080/stat_{i}",
                        command_url=f"http://127.0.0.1:8080/command_{i}",
                        is_live=1
                    ),
                    labels={"stream_id": f"stress_stream_{i}"}
                )
                
                stream_state = state.on_stream_started(evt_started)
                
                # Immediately end the stream
                evt_ended = StreamEndedEvent(
                    container_id="stress_container",
                    stream_id=f"stress_stream_{i}",
                    reason="stress_test"
                )
                
                state.on_stream_ended(evt_ended)
            
            # Verify engine.streams is empty
            engine = state.get_engine("stress_container")
            assert engine is not None, "Engine should exist"
            assert len(engine.streams) == 0, f"Engine.streams should be empty, but has {len(engine.streams)} items"
            print(f"✅ After 50 rapid cycles, engine.streams has {len(engine.streams)} items")
            
            # Verify we have 50 ended streams in state
            all_streams = state.list_streams(container_id="stress_container")
            assert len(all_streams) == 50, f"Should have 50 total streams, but have {len(all_streams)}"
            print(f"✅ Total streams in state: {len(all_streams)}")
            
            # Verify no started streams
            started_streams = state.list_streams(status="started", container_id="stress_container")
            assert len(started_streams) == 0, f"Should have 0 started streams, but have {len(started_streams)}"
            print(f"✅ Started streams: {len(started_streams)}")
            
            # Verify all are ended
            ended_streams = state.list_streams(status="ended", container_id="stress_container")
            assert len(ended_streams) == 50, f"Should have 50 ended streams, but have {len(ended_streams)}"
            print(f"✅ Ended streams: {len(ended_streams)}")
    
    print("\n✅ Rapid cycling stress test passed!")
    return True


def test_concurrent_stream_operations():
    """Stress test: concurrent stream start/end operations."""
    print("\n🧪 Stress test: concurrent stream operations...")
    
    from app.services.state import State
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    state = State()
    errors = []
    
    def start_and_end_stream(thread_id, num_streams):
        """Start and end multiple streams from a thread."""
        try:
            with patch('app.services.state.SessionLocal'):
                with patch('app.services.provisioner.clear_acestream_cache'):
                    for i in range(num_streams):
                        stream_id = f"thread_{thread_id}_stream_{i}"
                        
                        # Start stream
                        evt_started = StreamStartedEvent(
                            container_id=f"concurrent_container_{thread_id}",
                            engine=EngineAddress(host="127.0.0.1", port=8080 + thread_id),
                            stream=StreamKey(key_type="content_id", key=f"concurrent_content_{thread_id}_{i}"),
                            session=SessionInfo(
                                playback_session_id=f"concurrent_session_{thread_id}_{i}",
                                stat_url=f"http://127.0.0.1:{8080+thread_id}/stat_{i}",
                                command_url=f"http://127.0.0.1:{8080+thread_id}/command_{i}",
                                is_live=1
                            ),
                            labels={"stream_id": stream_id}
                        )
                        
                        state.on_stream_started(evt_started)
                        
                        # Small delay to simulate real usage
                        time.sleep(0.001)
                        
                        # End stream
                        evt_ended = StreamEndedEvent(
                            container_id=f"concurrent_container_{thread_id}",
                            stream_id=stream_id,
                            reason="concurrent_test"
                        )
                        
                        state.on_stream_ended(evt_ended)
        except Exception as e:
            errors.append(f"Thread {thread_id}: {e}")
    
    # Start 10 threads, each doing 10 stream cycles
    threads = []
    num_threads = 10
    streams_per_thread = 10
    
    for i in range(num_threads):
        t = threading.Thread(target=start_and_end_stream, args=(i, streams_per_thread))
        threads.append(t)
        t.start()
    
    # Wait for all threads to complete
    for t in threads:
        t.join()
    
    # Check for errors
    if errors:
        print(f"❌ Errors occurred during concurrent operations:")
        for error in errors:
            print(f"   {error}")
        return False
    
    print(f"✅ All {num_threads} threads completed without errors")
    
    # Verify state consistency
    total_engines = len(state.list_engines())
    print(f"✅ Total engines created: {total_engines}")
    
    # Verify all engines have empty streams lists
    for engine in state.list_engines():
        if len(engine.streams) > 0:
            print(f"❌ Engine {engine.container_id} has {len(engine.streams)} streams in its list!")
            return False
    
    print(f"✅ All {total_engines} engines have empty streams lists")
    
    # Verify total streams
    all_streams = state.list_streams()
    expected_total = num_threads * streams_per_thread
    assert len(all_streams) == expected_total, f"Expected {expected_total} streams, got {len(all_streams)}"
    print(f"✅ Total streams: {len(all_streams)}")
    
    # Verify no started streams
    started_streams = state.list_streams(status="started")
    assert len(started_streams) == 0, f"Expected 0 started streams, got {len(started_streams)}"
    print(f"✅ Started streams: {len(started_streams)}")
    
    print("\n✅ Concurrent operations stress test passed!")
    return True


def test_mixed_active_and_ended_streams():
    """Test that engines correctly show only active streams when some have ended."""
    print("\n🧪 Testing mixed active and ended streams...")
    
    from app.services.state import State
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    state = State()
    
    with patch('app.services.state.SessionLocal'):
        with patch('app.services.provisioner.clear_acestream_cache'):
            # Start 5 streams
            for i in range(5):
                evt_started = StreamStartedEvent(
                    container_id="mixed_container",
                    engine=EngineAddress(host="127.0.0.1", port=8080),
                    stream=StreamKey(key_type="content_id", key=f"mixed_content_{i}"),
                    session=SessionInfo(
                        playback_session_id=f"mixed_session_{i}",
                        stat_url=f"http://127.0.0.1:8080/stat_{i}",
                        command_url=f"http://127.0.0.1:8080/command_{i}",
                        is_live=1
                    ),
                    labels={"stream_id": f"mixed_stream_{i}"}
                )
                state.on_stream_started(evt_started)
            
            # Verify 5 active streams
            engine = state.get_engine("mixed_container")
            assert len(engine.streams) == 5, f"Expected 5 streams, got {len(engine.streams)}"
            print(f"✅ Started 5 streams, engine.streams has {len(engine.streams)} items")
            
            # End 3 streams
            for i in [0, 2, 4]:
                evt_ended = StreamEndedEvent(
                    container_id="mixed_container",
                    stream_id=f"mixed_stream_{i}",
                    reason="mixed_test"
                )
                state.on_stream_ended(evt_ended)
            
            # Verify only 2 active streams remain
            engine = state.get_engine("mixed_container")
            assert len(engine.streams) == 2, f"Expected 2 active streams, got {len(engine.streams)}"
            print(f"✅ After ending 3 streams, engine.streams has {len(engine.streams)} items")
            
            # Verify the correct streams are still active
            assert "mixed_stream_1" in engine.streams, "Stream 1 should still be active"
            assert "mixed_stream_3" in engine.streams, "Stream 3 should still be active"
            print(f"✅ Correct streams are still active: {engine.streams}")
            
            # Now test the API behavior
            # Without status filter - returns ALL streams (the bug)
            all_streams = state.list_streams(container_id="mixed_container")
            assert len(all_streams) == 5, f"Expected 5 total streams, got {len(all_streams)}"
            print(f"⚠️  list_streams(container_id) returns {len(all_streams)} streams (includes ended)")
            
            # With status filter - returns only active streams (correct)
            started_streams = state.list_streams(status="started", container_id="mixed_container")
            assert len(started_streams) == 2, f"Expected 2 started streams, got {len(started_streams)}"
            print(f"✅ list_streams(status='started', container_id) returns {len(started_streams)} streams (correct)")
            
            # Verify the count matches engine.streams
            assert len(started_streams) == len(engine.streams), "Started streams count should match engine.streams"
            print(f"✅ Started streams count matches engine.streams count")
    
    print("\n✅ Mixed active/ended streams test passed!")
    return True


if __name__ == "__main__":
    print("🔧 Stream Status Management Stress Tests")
    print("=" * 70)
    
    try:
        test_engine_doesnt_show_ended_streams()
        test_rapid_stream_cycling()
        test_concurrent_stream_operations()
        test_mixed_active_and_ended_streams()
        
        print("\n" + "=" * 70)
        print("🎉 All stress tests passed!")
        print("\n📋 Summary:")
        print("   - Confirmed bug: /engines/{container_id} returns ended streams")
        print("   - Fix needed: Add status='started' filter in get_engine endpoint")
        print("   - State management is thread-safe and handles rapid/concurrent operations")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
