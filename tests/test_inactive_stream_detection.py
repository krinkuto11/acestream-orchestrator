#!/usr/bin/env python3
"""
Test inactive stream detection functionality.

Tests that the collector properly detects and stops inactive streams based on:
1. livepos/pos field unchanged for >30 seconds
2. status="prebuf" for >30 seconds
3. download/upload speed both 0 for >30 seconds
"""

import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector, InactiveStreamTracker
from app.services.state import State
from app.models.schemas import StreamState, StreamStartedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_inactive_tracker_livepos_unchanged():
    """Test detection of inactive streams when livepos.pos is unchanged for >15 seconds."""
    print("Testing livepos.pos unchanged detection...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_1"
    
    # First update with pos=100
    should_stop = tracker.update_stream(stream_id, livepos_pos=100, status="playing", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is False, "Should not stop on first update"
    
    # Second update with same pos=100 (but not enough time has passed)
    should_stop = tracker.update_stream(stream_id, livepos_pos=100, status="playing", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is False, "Should not stop immediately"
    
    # Simulate time passing by directly modifying the tracker's internal state
    tracker._inactive_conditions[stream_id]["livepos_inactive_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=16)
    
    # Third update with same pos=100 (now >15 seconds have passed)
    should_stop = tracker.update_stream(stream_id, livepos_pos=100, status="playing", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is True, "Should stop after >15 seconds of unchanged pos"
    
    print("✅ livepos.pos unchanged detection test passed!")


def test_inactive_tracker_livepos_changes():
    """Test that tracker resets when livepos.pos changes."""
    print("Testing livepos.pos change resets tracking...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_2"
    
    # First update with pos=100
    tracker.update_stream(stream_id, livepos_pos=100, status="playing", 
                         speed_down=1000, speed_up=500)
    
    # Simulate time passing
    tracker._inactive_conditions[stream_id]["livepos_inactive_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=25)
    
    # Update with changed pos=200 (should reset tracking)
    should_stop = tracker.update_stream(stream_id, livepos_pos=200, status="playing", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is False, "Should not stop when pos changes"
    assert tracker._inactive_conditions[stream_id]["livepos_inactive_since"] is None, \
        "Tracking should be reset"
    
    print("✅ livepos.pos change reset test passed!")


def test_inactive_tracker_prebuf_status():
    """Test detection of inactive streams when status="prebuf" for >10 seconds."""
    print("Testing status=prebuf detection...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_3"
    
    # First update with status="prebuf"
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="prebuf", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is False, "Should not stop on first prebuf update"
    
    # Simulate time passing by directly modifying the tracker's internal state
    tracker._inactive_conditions[stream_id]["prebuf_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=11)
    
    # Second update still with status="prebuf" (now >10 seconds)
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="prebuf", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is True, "Should stop after >10 seconds of prebuf status"
    
    print("✅ status=prebuf detection test passed!")


def test_inactive_tracker_prebuf_changes():
    """Test that tracker resets when status changes from prebuf."""
    print("Testing status change from prebuf resets tracking...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_4"
    
    # First update with status="prebuf"
    tracker.update_stream(stream_id, livepos_pos=None, status="prebuf", 
                         speed_down=1000, speed_up=500)
    
    # Simulate time passing
    tracker._inactive_conditions[stream_id]["prebuf_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=25)
    
    # Update with changed status="playing" (should reset tracking)
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is False, "Should not stop when status changes"
    assert tracker._inactive_conditions[stream_id]["prebuf_since"] is None, \
        "Prebuf tracking should be reset"
    
    print("✅ Status change from prebuf reset test passed!")


def test_inactive_tracker_zero_speed():
    """Test detection of inactive streams when both speeds are 0 for >10 seconds."""
    print("Testing zero speed detection...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_5"
    
    # First update with speed_down=0 and speed_up=0
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=0, speed_up=0)
    assert should_stop is False, "Should not stop on first zero speed update"
    
    # Simulate time passing by directly modifying the tracker's internal state
    tracker._inactive_conditions[stream_id]["zero_speed_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=11)
    
    # Second update still with both speeds at 0 (now >10 seconds)
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=0, speed_up=0)
    assert should_stop is True, "Should stop after >10 seconds of zero speed"
    
    print("✅ Zero speed detection test passed!")


def test_inactive_tracker_speed_changes():
    """Test that tracker resets when speeds change from zero."""
    print("Testing speed change from zero resets tracking...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_6"
    
    # First update with both speeds at 0
    tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                         speed_down=0, speed_up=0)
    
    # Simulate time passing
    tracker._inactive_conditions[stream_id]["zero_speed_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=25)
    
    # Update with non-zero speed (should reset tracking)
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=1000, speed_up=0)
    assert should_stop is False, "Should not stop when speeds change"
    assert tracker._inactive_conditions[stream_id]["zero_speed_since"] is None, \
        "Zero speed tracking should be reset"
    
    print("✅ Speed change from zero reset test passed!")


def test_inactive_tracker_one_speed_zero():
    """Test that only one speed being zero does not trigger detection."""
    print("Testing that only one speed being zero doesn't trigger...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_7"
    
    # Update with only download speed at 0 (upload is non-zero)
    tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                         speed_down=0, speed_up=500)
    
    # Should not have started tracking since both speeds aren't zero
    assert tracker._inactive_conditions[stream_id]["zero_speed_since"] is None, \
        "Should not track when only one speed is zero"
    
    # Even if we simulate time passing, it shouldn't trigger
    # since the condition was never started
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=0, speed_up=500)
    assert should_stop is False, "Should not stop when only one speed is zero"
    
    print("✅ One speed zero test passed!")


def test_inactive_tracker_multiple_conditions():
    """Test that any one condition being met for its threshold triggers stop."""
    print("Testing multiple conditions...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_8"
    
    # First update: pos unchanged, status playing, speeds normal
    tracker.update_stream(stream_id, livepos_pos=100, status="playing", 
                         speed_down=1000, speed_up=500)
    
    # Second update: same pos (starts livepos tracking), prebuf (starts prebuf tracking)
    tracker.update_stream(stream_id, livepos_pos=100, status="prebuf", 
                         speed_down=1000, speed_up=500)
    
    # Simulate time passing only for prebuf (not livepos)
    tracker._inactive_conditions[stream_id]["prebuf_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=11)
    
    # Should stop due to prebuf even though livepos hasn't been inactive for 15s
    should_stop = tracker.update_stream(stream_id, livepos_pos=100, status="prebuf", 
                                       speed_down=1000, speed_up=500)
    assert should_stop is True, "Should stop when any condition is met for its threshold"
    
    print("✅ Multiple conditions test passed!")


def test_inactive_tracker_low_speed():
    """Test detection of inactive streams when download speed is below threshold for >20 seconds."""
    print("Testing low speed detection...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_9"
    
    # First update with speed_down below threshold (300 KB/s < 400 KB/s)
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=300, speed_up=500)
    assert should_stop is False, "Should not stop on first low speed update"
    
    # Simulate time passing by directly modifying the tracker's internal state
    tracker._inactive_conditions[stream_id]["low_speed_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=21)
    
    # Second update still with low speed (now >20 seconds)
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=300, speed_up=500)
    assert should_stop is True, "Should stop after >20 seconds of low speed"
    
    print("✅ Low speed detection test passed!")


def test_inactive_tracker_low_speed_changes():
    """Test that tracker resets when speed increases above threshold."""
    print("Testing low speed change resets tracking...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_10"
    
    # First update with low speed
    tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                         speed_down=300, speed_up=500)
    
    # Simulate time passing
    tracker._inactive_conditions[stream_id]["low_speed_since"] = \
        datetime.now(timezone.utc) - timedelta(seconds=15)
    
    # Update with speed above threshold (should reset tracking)
    should_stop = tracker.update_stream(stream_id, livepos_pos=None, status="playing", 
                                       speed_down=500, speed_up=500)
    assert should_stop is False, "Should not stop when speed increases above threshold"
    assert tracker._inactive_conditions[stream_id]["low_speed_since"] is None, \
        "Low speed tracking should be reset"
    
    print("✅ Low speed change reset test passed!")


def test_inactive_tracker_remove_stream():
    """Test that removing a stream cleans up tracking data."""
    print("Testing stream removal...")
    
    tracker = InactiveStreamTracker()
    stream_id = "test_stream_9"
    
    # Add tracking data
    tracker.update_stream(stream_id, livepos_pos=100, status="prebuf", 
                         speed_down=0, speed_up=0)
    
    assert stream_id in tracker._inactive_conditions, "Stream should be tracked"
    assert stream_id in tracker._last_values, "Stream values should be tracked"
    
    # Remove the stream
    tracker.remove_stream(stream_id)
    
    assert stream_id not in tracker._inactive_conditions, "Stream should not be tracked after removal"
    assert stream_id not in tracker._last_values, "Stream values should not be tracked after removal"
    
    print("✅ Stream removal test passed!")


def test_collector_stops_inactive_stream_livepos():
    """Test that collector stops stream when livepos.pos is unchanged for >15 seconds."""
    print("Testing collector stops stream with unchanged livepos...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_123",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_123",
            stat_url="http://127.0.0.1:8080/ace/stat?id=test_session_123",
            command_url="http://127.0.0.1:8080/ace/cmd",
            is_live=1
        ),
        labels={"stream_id": "test_stream_inactive_1"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create collector
    collector = Collector()
    
    # Mock HTTP responses
    stat_response = MagicMock()
    stat_response.status_code = 200
    stat_response.json.return_value = {
        "response": {
            "peers": 5,
            "speed_down": 1000,
            "speed_up": 500,
            "status": "playing",
            "livepos": {"pos": 12345}
        }
    }
    
    stop_response = MagicMock()
    stop_response.status_code = 200
    
    # Mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[stat_response, stop_response])
    
    # Run collector with patched state
    async def run_test():
        with patch('app.services.collector.state', test_state):
            # First collection - establishes baseline
            await collector._collect_one(
                mock_client,
                "test_stream_inactive_1",
                "http://127.0.0.1:8080/ace/stat?id=test_session_123",
                "http://127.0.0.1:8080/ace/cmd"
            )
            
            # Simulate time passing
            collector._inactive_tracker._inactive_conditions["test_stream_inactive_1"]["livepos_inactive_since"] = \
                datetime.now(timezone.utc) - timedelta(seconds=16)
            
            # Mock for second collection (stop command)
            mock_client.get = AsyncMock(side_effect=[stat_response, stop_response])
            
            # Second collection - should detect inactive and stop
            await collector._collect_one(
                mock_client,
                "test_stream_inactive_1",
                "http://127.0.0.1:8080/ace/stat?id=test_session_123",
                "http://127.0.0.1:8080/ace/cmd"
            )
    
    asyncio.run(run_test())
    
    # Verify stream was ended
    stream_after = test_state.get_stream("test_stream_inactive_1")
    assert stream_after is not None
    assert stream_after.status == "ended", f"Expected stream to be ended, but status is {stream_after.status}"
    
    # Verify stop command was called
    calls = mock_client.get.call_args_list
    # Should have: stat call, stat call again, stop call
    assert len(calls) >= 2, "Should have called stat URL and stop URL"
    # Check if stop command was called with method=stop
    stop_called = any("method=stop" in str(call) for call in calls)
    assert stop_called, "Stop command should have been called"
    
    print("✅ Collector stops inactive stream (livepos) test passed!")


def test_collector_stops_inactive_stream_prebuf():
    """Test that collector stops stream when status=prebuf for >10 seconds."""
    print("Testing collector stops stream with prebuf status...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_456",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_456",
            stat_url="http://127.0.0.1:8080/ace/stat?id=test_session_456",
            command_url="http://127.0.0.1:8080/ace/cmd",
            is_live=1
        ),
        labels={"stream_id": "test_stream_inactive_2"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create collector
    collector = Collector()
    
    # Mock HTTP responses with prebuf status
    stat_response = MagicMock()
    stat_response.status_code = 200
    stat_response.json.return_value = {
        "response": {
            "peers": 5,
            "speed_down": 1000,
            "speed_up": 500,
            "status": "prebuf"
        }
    }
    
    stop_response = MagicMock()
    stop_response.status_code = 200
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[stat_response, stop_response])
    
    # Run collector
    async def run_test():
        with patch('app.services.collector.state', test_state):
            # First collection
            await collector._collect_one(
                mock_client,
                "test_stream_inactive_2",
                "http://127.0.0.1:8080/ace/stat?id=test_session_456",
                "http://127.0.0.1:8080/ace/cmd"
            )
            
            # Simulate time passing
            collector._inactive_tracker._inactive_conditions["test_stream_inactive_2"]["prebuf_since"] = \
                datetime.now(timezone.utc) - timedelta(seconds=11)
            
            # Mock for second collection
            mock_client.get = AsyncMock(side_effect=[stat_response, stop_response])
            
            # Second collection - should detect inactive and stop
            await collector._collect_one(
                mock_client,
                "test_stream_inactive_2",
                "http://127.0.0.1:8080/ace/stat?id=test_session_456",
                "http://127.0.0.1:8080/ace/cmd"
            )
    
    asyncio.run(run_test())
    
    # Verify stream was ended
    stream_after = test_state.get_stream("test_stream_inactive_2")
    assert stream_after is not None
    assert stream_after.status == "ended"
    
    print("✅ Collector stops inactive stream (prebuf) test passed!")


def test_collector_stops_inactive_stream_zero_speed():
    """Test that collector stops stream when both speeds are 0 for >10 seconds."""
    print("Testing collector stops stream with zero speeds...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_789",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_789",
            stat_url="http://127.0.0.1:8080/ace/stat?id=test_session_789",
            command_url="http://127.0.0.1:8080/ace/cmd",
            is_live=1
        ),
        labels={"stream_id": "test_stream_inactive_3"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create collector
    collector = Collector()
    
    # Mock HTTP responses with zero speeds
    stat_response = MagicMock()
    stat_response.status_code = 200
    stat_response.json.return_value = {
        "response": {
            "peers": 5,
            "speed_down": 0,
            "speed_up": 0,
            "status": "playing"
        }
    }
    
    stop_response = MagicMock()
    stop_response.status_code = 200
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[stat_response, stop_response])
    
    # Run collector
    async def run_test():
        with patch('app.services.collector.state', test_state):
            # First collection
            await collector._collect_one(
                mock_client,
                "test_stream_inactive_3",
                "http://127.0.0.1:8080/ace/stat?id=test_session_789",
                "http://127.0.0.1:8080/ace/cmd"
            )
            
            # Simulate time passing
            collector._inactive_tracker._inactive_conditions["test_stream_inactive_3"]["zero_speed_since"] = \
                datetime.now(timezone.utc) - timedelta(seconds=11)
            
            # Mock for second collection
            mock_client.get = AsyncMock(side_effect=[stat_response, stop_response])
            
            # Second collection - should detect inactive and stop
            await collector._collect_one(
                mock_client,
                "test_stream_inactive_3",
                "http://127.0.0.1:8080/ace/stat?id=test_session_789",
                "http://127.0.0.1:8080/ace/cmd"
            )
    
    asyncio.run(run_test())
    
    # Verify stream was ended
    stream_after = test_state.get_stream("test_stream_inactive_3")
    assert stream_after is not None
    assert stream_after.status == "ended"
    
    print("✅ Collector stops inactive stream (zero speeds) test passed!")


if __name__ == "__main__":
    print("🧪 Running inactive stream detection tests...\n")
    
    # InactiveStreamTracker tests
    test_inactive_tracker_livepos_unchanged()
    test_inactive_tracker_livepos_changes()
    test_inactive_tracker_prebuf_status()
    test_inactive_tracker_prebuf_changes()
    test_inactive_tracker_zero_speed()
    test_inactive_tracker_speed_changes()
    test_inactive_tracker_one_speed_zero()
    test_inactive_tracker_multiple_conditions()
    test_inactive_tracker_low_speed()
    test_inactive_tracker_low_speed_changes()
    test_inactive_tracker_remove_stream()
    
    # Collector integration tests
    test_collector_stops_inactive_stream_livepos()
    test_collector_stops_inactive_stream_prebuf()
    test_collector_stops_inactive_stream_zero_speed()
    
    print("\n🎉 All inactive stream detection tests passed!")
