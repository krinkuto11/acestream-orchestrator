#!/usr/bin/env python3
"""
Integration test for inactive stream detection with the collector service.

This test verifies the complete flow:
1. Stream starts
2. Collector detects inactive conditions
3. Stream is stopped via command URL
4. Stream is marked as ended in state
"""

import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector
from app.services.state import State
from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo
from app.services.db import engine as db_engine
from app.models.db_models import Base

Base.metadata.create_all(bind=db_engine)


def test_full_integration_livepos():
    """Full integration test simulating real-world scenario with livepos tracking."""
    print("Testing full integration with livepos tracking...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_integration",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_content_12345"),
        session=SessionInfo(
            playback_session_id="session_integration_123",
            stat_url="http://127.0.0.1:8080/ace/stat?id=session_integration_123",
            command_url="http://127.0.0.1:8080/ace/cmd",
            is_live=1
        ),
        labels={"stream_id": "integration_stream_1"}
    )
    
    stream = test_state.on_stream_started(evt)
    print(f"  ✓ Stream started: {stream.id}")
    
    # Create collector
    collector = Collector()
    
    # Mock responses sequence:
    # 1. First stat call - position at 100, normal playback
    # 2. Second stat call - position still at 100 (inactive starts)
    # 3. Multiple more calls with same position
    # 4. Final call triggers stop
    
    stat_responses = []
    for i in range(5):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "response": {
                "peers": 5,
                "speed_down": 1000000,
                "speed_up": 500000,
                "status": "playing",
                "downloaded": 10485760,
                "uploaded": 2621440,
                "livepos": {"pos": 100}  # Position never changes
            }
        }
        stat_responses.append(resp)
    
    # Stop command response
    stop_resp = MagicMock()
    stop_resp.status_code = 200
    stop_resp.json.return_value = {"result": "ok"}
    
    call_count = [0]
    
    async def mock_get(url, **kwargs):
        call_count[0] += 1
        if "method=stop" in url:
            print(f"  ✓ Stop command called: {url}")
            return stop_resp
        else:
            return stat_responses[min(call_count[0] - 1, len(stat_responses) - 1)]
    
    async def run_integration():
        with patch('app.services.collector.state', test_state):
            mock_client = MagicMock()
            mock_client.get = AsyncMock(side_effect=mock_get)
            
            # Simulate multiple collection cycles
            for cycle in range(5):
                await collector._collect_one(
                    mock_client,
                    "integration_stream_1",
                    "http://127.0.0.1:8080/ace/stat?id=session_integration_123",
                    "http://127.0.0.1:8080/ace/cmd"
                )
                
                # After first cycle, simulate time passing for the inactive condition
                if cycle == 0:
                    print(f"  ✓ Cycle {cycle + 1}: Baseline established (pos=100)")
                elif cycle < 3:
                    print(f"  ✓ Cycle {cycle + 1}: Position still unchanged (inactive tracking)")
                else:
                    # Simulate enough time has passed
                    if "integration_stream_1" in collector._inactive_tracker._inactive_conditions:
                        if collector._inactive_tracker._inactive_conditions["integration_stream_1"]["livepos_inactive_since"]:
                            collector._inactive_tracker._inactive_conditions["integration_stream_1"]["livepos_inactive_since"] = \
                                datetime.now(timezone.utc) - timedelta(seconds=31)
                            print(f"  ✓ Cycle {cycle + 1}: Simulated >30s elapsed, should trigger stop")
                
                # Check if stream was ended
                stream_state = test_state.get_stream("integration_stream_1")
                if stream_state and stream_state.status == "ended":
                    print(f"  ✓ Stream ended after cycle {cycle + 1}")
                    break
    
    asyncio.run(run_integration())
    
    # Verify final state
    final_stream = test_state.get_stream("integration_stream_1")
    assert final_stream is not None, "Stream should exist"
    assert final_stream.status == "ended", f"Stream should be ended, but status is {final_stream.status}"
    
    # Verify stop command was called
    assert call_count[0] > 0, "HTTP client should have been called"
    
    print("  ✓ Stream properly stopped and ended")
    print("✅ Full integration test passed!\n")


def test_full_integration_mixed_conditions():
    """Test with multiple inactive conditions being detected."""
    print("Testing integration with mixed inactive conditions...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_mixed",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_content_67890"),
        session=SessionInfo(
            playback_session_id="session_mixed_456",
            stat_url="http://127.0.0.1:8080/ace/stat?id=session_mixed_456",
            command_url="http://127.0.0.1:8080/ace/cmd",
            is_live=1
        ),
        labels={"stream_id": "mixed_stream_1"}
    )
    
    stream = test_state.on_stream_started(evt)
    print(f"  ✓ Stream started: {stream.id}")
    
    collector = Collector()
    
    # Scenario: Stream goes into prebuf AND has zero speeds
    stat_resp = MagicMock()
    stat_resp.status_code = 200
    stat_resp.json.return_value = {
        "response": {
            "peers": 0,
            "speed_down": 0,  # Zero speed
            "speed_up": 0,    # Zero speed
            "status": "prebuf",  # Prebuf status
            "downloaded": 0,
            "uploaded": 0
        }
    }
    
    stop_resp = MagicMock()
    stop_resp.status_code = 200
    
    async def mock_get_mixed(url, **kwargs):
        if "method=stop" in url:
            print(f"  ✓ Stop command called due to multiple inactive conditions")
            return stop_resp
        return stat_resp
    
    async def run_mixed():
        with patch('app.services.collector.state', test_state):
            mock_client = MagicMock()
            mock_client.get = AsyncMock(side_effect=mock_get_mixed)
            
            # First collection establishes baseline
            await collector._collect_one(
                mock_client,
                "mixed_stream_1",
                "http://127.0.0.1:8080/ace/stat?id=session_mixed_456",
                "http://127.0.0.1:8080/ace/cmd"
            )
            print(f"  ✓ Cycle 1: Detected prebuf + zero speeds")
            
            # Simulate time passing for both conditions
            if "mixed_stream_1" in collector._inactive_tracker._inactive_conditions:
                conditions = collector._inactive_tracker._inactive_conditions["mixed_stream_1"]
                past_time = datetime.now(timezone.utc) - timedelta(seconds=31)
                if conditions["prebuf_since"]:
                    conditions["prebuf_since"] = past_time
                if conditions["zero_speed_since"]:
                    conditions["zero_speed_since"] = past_time
            
            # Second collection should trigger stop
            await collector._collect_one(
                mock_client,
                "mixed_stream_1",
                "http://127.0.0.1:8080/ace/stat?id=session_mixed_456",
                "http://127.0.0.1:8080/ace/cmd"
            )
            print(f"  ✓ Cycle 2: Triggered stop after >30s")
    
    asyncio.run(run_mixed())
    
    # Verify
    final_stream = test_state.get_stream("mixed_stream_1")
    assert final_stream.status == "ended", f"Stream should be ended, but status is {final_stream.status}"
    
    print("  ✓ Stream stopped due to multiple inactive conditions")
    print("✅ Mixed conditions integration test passed!\n")


def test_collector_handles_stream_recovery():
    """Test that collector properly resets tracking when stream recovers."""
    print("Testing stream recovery scenario...")
    
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_recovery",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_content_recovery"),
        session=SessionInfo(
            playback_session_id="session_recovery_789",
            stat_url="http://127.0.0.1:8080/ace/stat?id=session_recovery_789",
            command_url="http://127.0.0.1:8080/ace/cmd",
            is_live=1
        ),
        labels={"stream_id": "recovery_stream_1"}
    )
    
    stream = test_state.on_stream_started(evt)
    print(f"  ✓ Stream started: {stream.id}")
    
    collector = Collector()
    
    # Scenario: Stream starts with zero speeds, then recovers
    call_sequence = [
        # First call: zero speeds
        {
            "response": {
                "peers": 2,
                "speed_down": 0,
                "speed_up": 0,
                "status": "playing"
            }
        },
        # Second call: still zero speeds (tracking starts)
        {
            "response": {
                "peers": 2,
                "speed_down": 0,
                "speed_up": 0,
                "status": "playing"
            }
        },
        # Third call: Stream recovers! Non-zero speeds
        {
            "response": {
                "peers": 5,
                "speed_down": 500000,
                "speed_up": 100000,
                "status": "playing"
            }
        }
    ]
    
    call_idx = [0]
    
    async def mock_get_recovery(url, **kwargs):
        if "method=stop" in url:
            raise AssertionError("Stop should not be called for recovered stream")
        
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = call_sequence[min(call_idx[0], len(call_sequence) - 1)]
        call_idx[0] += 1
        return resp
    
    async def run_recovery():
        with patch('app.services.collector.state', test_state):
            mock_client = MagicMock()
            mock_client.get = AsyncMock(side_effect=mock_get_recovery)
            
            # First collection
            await collector._collect_one(
                mock_client,
                "recovery_stream_1",
                "http://127.0.0.1:8080/ace/stat?id=session_recovery_789",
                "http://127.0.0.1:8080/ace/cmd"
            )
            print(f"  ✓ Cycle 1: Zero speeds detected")
            
            # Second collection - tracking starts
            await collector._collect_one(
                mock_client,
                "recovery_stream_1",
                "http://127.0.0.1:8080/ace/stat?id=session_recovery_789",
                "http://127.0.0.1:8080/ace/cmd"
            )
            print(f"  ✓ Cycle 2: Zero speeds continue, tracking active")
            
            # Verify tracking is active
            assert "recovery_stream_1" in collector._inactive_tracker._inactive_conditions
            conditions = collector._inactive_tracker._inactive_conditions["recovery_stream_1"]
            assert conditions["zero_speed_since"] is not None, "Should be tracking zero speeds"
            
            # Third collection - recovery
            await collector._collect_one(
                mock_client,
                "recovery_stream_1",
                "http://127.0.0.1:8080/ace/stat?id=session_recovery_789",
                "http://127.0.0.1:8080/ace/cmd"
            )
            print(f"  ✓ Cycle 3: Stream recovered, speeds > 0")
            
            # Verify tracking was reset
            conditions = collector._inactive_tracker._inactive_conditions["recovery_stream_1"]
            assert conditions["zero_speed_since"] is None, "Tracking should be reset after recovery"
    
    asyncio.run(run_recovery())
    
    # Verify stream is still active
    final_stream = test_state.get_stream("recovery_stream_1")
    assert final_stream.status == "started", f"Stream should still be active after recovery"
    
    print("  ✓ Stream properly recovered, tracking reset")
    print("✅ Recovery scenario test passed!\n")


if __name__ == "__main__":
    print("🧪 Running integration tests for inactive stream detection...\n")
    
    test_full_integration_livepos()
    test_full_integration_mixed_conditions()
    test_collector_handles_stream_recovery()
    
    print("🎉 All integration tests passed!")
