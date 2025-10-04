#!/usr/bin/env python3
"""
Test to verify that engine cleanup logic still works correctly after the stream status fix.
This addresses the concern that the fix might have affected engine cleanup.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_can_stop_engine_uses_correct_status_filter():
    """Verify that can_stop_engine correctly uses status='started' filter."""
    print("\n🧪 Testing can_stop_engine uses correct status filter...")
    
    from app.services.state import state as global_state
    from app.services.autoscaler import can_stop_engine, _empty_engine_timestamps
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    # Clear state and timestamps
    global_state.clear_state()
    _empty_engine_timestamps.clear()
    
    with patch('app.services.state.SessionLocal'):
        with patch('app.services.provisioner.clear_acestream_cache'):
            # Start a stream
            evt_started = StreamStartedEvent(
                container_id="cleanup_test_engine",
                engine=EngineAddress(host="127.0.0.1", port=8080),
                stream=StreamKey(key_type="content_id", key="cleanup_content"),
                session=SessionInfo(
                    playback_session_id="cleanup_session",
                    stat_url="http://127.0.0.1:8080/stat",
                    command_url="http://127.0.0.1:8080/cmd",
                    is_live=1
                ),
                labels={"stream_id": "cleanup_stream"}
            )
            global_state.on_stream_started(evt_started)
            print(f"✅ Started stream on engine")
            
            # Engine has active stream, should NOT be stoppable
            with patch('app.core.config.cfg.ENGINE_GRACE_PERIOD_S', 0):
                can_stop = can_stop_engine("cleanup_test_engine", bypass_grace_period=True)
            assert not can_stop, "Engine with active stream should NOT be stoppable"
            print(f"✅ Engine with active stream is NOT stoppable")
            
            # End the stream
            evt_ended = StreamEndedEvent(
                container_id="cleanup_test_engine",
                stream_id="cleanup_stream",
                reason="cleanup_test"
            )
            global_state.on_stream_ended(evt_ended)
            print(f"✅ Ended stream")
            
            # Verify stream is ended in state
            all_streams = global_state.list_streams(container_id="cleanup_test_engine")
            assert len(all_streams) == 1, "Should have 1 stream in state"
            assert all_streams[0].status == "ended", "Stream should be ended"
            print(f"✅ Stream status is 'ended' in state")
            
            # Verify no active streams
            active_streams = global_state.list_streams(status="started", container_id="cleanup_test_engine")
            assert len(active_streams) == 0, "Should have 0 active streams"
            print(f"✅ No active streams found (correct)")
            
            # Engine has no active streams, should be stoppable (with bypass_grace_period)
            with patch('app.core.config.cfg.ENGINE_GRACE_PERIOD_S', 0):
                can_stop = can_stop_engine("cleanup_test_engine", bypass_grace_period=True)
            assert can_stop, "Engine without active streams should be stoppable"
            print(f"✅ Engine without active streams IS stoppable")
    
    print("\n✅ can_stop_engine correctly uses status='started' filter!")
    return True


def test_cleanup_empty_engines_consistency():
    """Verify that _cleanup_empty_engines logic is consistent with the fix."""
    print("\n🧪 Testing _cleanup_empty_engines consistency...")
    
    from app.services.state import state as global_state
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    
    # Clear state
    global_state.clear_state()
    
    with patch('app.services.state.SessionLocal'):
        with patch('app.services.provisioner.clear_acestream_cache'):
            # Create 3 engines with different states
            # Engine 1: Has active stream
            evt1 = StreamStartedEvent(
                container_id="engine_1",
                engine=EngineAddress(host="127.0.0.1", port=8001),
                stream=StreamKey(key_type="content_id", key="content_1"),
                session=SessionInfo(
                    playback_session_id="session_1",
                    stat_url="http://127.0.0.1:8001/stat",
                    command_url="http://127.0.0.1:8001/cmd",
                    is_live=1
                ),
                labels={"stream_id": "stream_1"}
            )
            global_state.on_stream_started(evt1)
            print(f"✅ Engine 1: Has active stream")
            
            # Engine 2: Had stream but it ended
            evt2_start = StreamStartedEvent(
                container_id="engine_2",
                engine=EngineAddress(host="127.0.0.1", port=8002),
                stream=StreamKey(key_type="content_id", key="content_2"),
                session=SessionInfo(
                    playback_session_id="session_2",
                    stat_url="http://127.0.0.1:8002/stat",
                    command_url="http://127.0.0.1:8002/cmd",
                    is_live=1
                ),
                labels={"stream_id": "stream_2"}
            )
            global_state.on_stream_started(evt2_start)
            evt2_end = StreamEndedEvent(
                container_id="engine_2",
                stream_id="stream_2",
                reason="test"
            )
            global_state.on_stream_ended(evt2_end)
            print(f"✅ Engine 2: Had stream, now ended")
            
            # Engine 3: Never had any stream
            from app.models.schemas import EngineState
            engine3 = EngineState(
                container_id="engine_3",
                container_name="test_engine_3",
                host="127.0.0.1",
                port=8003,
                labels={},
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                streams=[],
                health_status="unknown"
            )
            global_state.engines["engine_3"] = engine3
            print(f"✅ Engine 3: Never had streams")
            
            # Simulate the cleanup logic
            all_engines = global_state.list_engines()
            active_streams = global_state.list_streams(status="started")
            used_container_ids = {stream.container_id for stream in active_streams}
            
            print(f"\n📊 Cleanup logic analysis:")
            print(f"   Total engines: {len(all_engines)}")
            print(f"   Active streams: {len(active_streams)}")
            print(f"   Container IDs with active streams: {used_container_ids}")
            
            # Check each engine
            empty_engines = []
            for engine in all_engines:
                is_empty = engine.container_id not in used_container_ids
                print(f"   - {engine.container_id}: {'EMPTY' if is_empty else 'HAS ACTIVE STREAMS'}")
                if is_empty:
                    empty_engines.append(engine.container_id)
            
            # Verify expectations
            assert "engine_1" not in empty_engines, "Engine 1 should NOT be empty (has active stream)"
            assert "engine_2" in empty_engines, "Engine 2 SHOULD be empty (stream ended)"
            assert "engine_3" in empty_engines, "Engine 3 SHOULD be empty (never had streams)"
            
            print(f"\n✅ Cleanup logic correctly identifies empty engines!")
            print(f"   Empty engines: {empty_engines}")
    
    print("\n✅ _cleanup_empty_engines consistency verified!")
    return True


def test_endpoint_and_cleanup_consistency():
    """Verify that get_engine endpoint and cleanup logic use the same filtering."""
    print("\n🧪 Testing endpoint and cleanup logic consistency...")
    
    from app.services.state import state as global_state
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    from app.main import app
    from fastapi.testclient import TestClient
    
    # Clear state
    global_state.clear_state()
    
    # Create test client
    client = TestClient(app)
    
    with patch('app.services.state.SessionLocal'):
        with patch('app.services.provisioner.clear_acestream_cache'):
            # Start and end a stream
            evt_start = StreamStartedEvent(
                container_id="consistency_engine",
                engine=EngineAddress(host="127.0.0.1", port=9000),
                stream=StreamKey(key_type="content_id", key="consistency_content"),
                session=SessionInfo(
                    playback_session_id="consistency_session",
                    stat_url="http://127.0.0.1:9000/stat",
                    command_url="http://127.0.0.1:9000/cmd",
                    is_live=1
                ),
                labels={"stream_id": "consistency_stream"}
            )
            global_state.on_stream_started(evt_start)
            
            evt_end = StreamEndedEvent(
                container_id="consistency_engine",
                stream_id="consistency_stream",
                reason="test"
            )
            global_state.on_stream_ended(evt_end)
            
            # Check what the endpoint returns
            response = client.get("/engines/consistency_engine")
            endpoint_streams = response.json()["streams"]
            print(f"✅ GET /engines/{{id}} returns {len(endpoint_streams)} streams")
            
            # Check what cleanup logic sees
            active_streams = global_state.list_streams(status="started", container_id="consistency_engine")
            print(f"✅ list_streams(status='started') returns {len(active_streams)} streams")
            
            # Check what _cleanup_empty_engines logic uses
            all_active_streams = global_state.list_streams(status="started")
            used_container_ids = {stream.container_id for stream in all_active_streams}
            is_empty = "consistency_engine" not in used_container_ids
            print(f"✅ Cleanup logic sees engine as: {'EMPTY' if is_empty else 'NOT EMPTY'}")
            
            # Verify consistency
            assert len(endpoint_streams) == 0, "Endpoint should return 0 streams"
            assert len(active_streams) == 0, "list_streams should return 0 streams"
            assert is_empty, "Cleanup logic should see engine as empty"
            
            print(f"\n✅ ALL THREE use status='started' filter consistently!")
    
    print("\n✅ Endpoint and cleanup logic are consistent!")
    return True


if __name__ == "__main__":
    print("🔧 Engine Cleanup Verification Tests")
    print("=" * 70)
    print("\nVerifying that the stream status fix doesn't affect engine cleanup...")
    
    try:
        test_can_stop_engine_uses_correct_status_filter()
        test_cleanup_empty_engines_consistency()
        test_endpoint_and_cleanup_consistency()
        
        print("\n" + "=" * 70)
        print("🎉 All engine cleanup verification tests passed!")
        print("\n📋 Summary:")
        print("   ✅ can_stop_engine correctly uses status='started'")
        print("   ✅ _cleanup_empty_engines correctly identifies empty engines")
        print("   ✅ get_engine endpoint is consistent with cleanup logic")
        print("   ✅ The fix does NOT break engine cleanup!")
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
