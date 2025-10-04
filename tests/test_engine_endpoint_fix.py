#!/usr/bin/env python3
"""
Test to verify the fix for the bug where engines show ended streams.
This test validates that the /engines/{container_id} endpoint only returns active streams.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_get_engine_endpoint_fix():
    """Test that get_engine endpoint only returns started streams after fix."""
    print("\n🧪 Testing get_engine endpoint fix...")
    
    from app.services.state import State, state as global_state
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    from app.main import app
    from fastapi.testclient import TestClient
    
    # Use global state and clear it first
    global_state.clear_state()
    
    # Create test client
    client = TestClient(app)
    
    # Mock the database operations
    with patch('app.services.state.SessionLocal'):
        with patch('app.services.provisioner.clear_acestream_cache'):
            # Start 3 streams
            stream_ids = []
            for i in range(3):
                evt_started = StreamStartedEvent(
                    container_id="endpoint_test_container",
                    engine=EngineAddress(host="127.0.0.1", port=9000),
                    stream=StreamKey(key_type="content_id", key=f"endpoint_content_{i}"),
                    session=SessionInfo(
                        playback_session_id=f"endpoint_session_{i}",
                        stat_url=f"http://127.0.0.1:9000/stat_{i}",
                        command_url=f"http://127.0.0.1:9000/command_{i}",
                        is_live=1
                    ),
                    labels={"stream_id": f"endpoint_stream_{i}"}
                )
                stream_state = global_state.on_stream_started(evt_started)
                stream_ids.append(stream_state.id)
                print(f"✅ Started stream {i}: {stream_state.id}")
            
            # Call the endpoint - should return all 3 streams
            response = client.get("/engines/endpoint_test_container")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()
            
            assert "engine" in data, "Response should have 'engine' key"
            assert "streams" in data, "Response should have 'streams' key"
            assert len(data["streams"]) == 3, f"Should have 3 active streams, got {len(data['streams'])}"
            print(f"✅ GET /engines/{{id}} returns 3 active streams")
            
            # End 2 streams
            for i in [0, 2]:
                evt_ended = StreamEndedEvent(
                    container_id="endpoint_test_container",
                    stream_id=f"endpoint_stream_{i}",
                    reason="endpoint_test"
                )
                global_state.on_stream_ended(evt_ended)
                print(f"✅ Ended stream {i}")
            
            # Call the endpoint again - should now return only 1 active stream
            response = client.get("/engines/endpoint_test_container")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()
            
            assert len(data["streams"]) == 1, f"Should have 1 active stream after ending 2, got {len(data['streams'])}"
            assert data["streams"][0]["id"] == "endpoint_stream_1", "Should return the correct active stream"
            assert data["streams"][0]["status"] == "started", "Stream status should be 'started'"
            print(f"✅ GET /engines/{{id}} now correctly returns only 1 active stream")
            print(f"✅ Active stream ID: {data['streams'][0]['id']}, status: {data['streams'][0]['status']}")
            
            # Verify the engine.streams list also has 1 item
            engine = global_state.get_engine("endpoint_test_container")
            assert len(engine.streams) == 1, f"Engine.streams should have 1 item, got {len(engine.streams)}"
            print(f"✅ Engine.streams list also has 1 item (consistent with endpoint)")
            
            # End the last stream
            evt_ended = StreamEndedEvent(
                container_id="endpoint_test_container",
                stream_id="endpoint_stream_1",
                reason="endpoint_test"
            )
            global_state.on_stream_ended(evt_ended)
            print(f"✅ Ended last stream")
            
            # Call the endpoint again - should return 0 active streams
            response = client.get("/engines/endpoint_test_container")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()
            
            assert len(data["streams"]) == 0, f"Should have 0 active streams, got {len(data['streams'])}"
            print(f"✅ GET /engines/{{id}} correctly returns 0 active streams")
            
            # Verify we still have 3 ended streams in state (not returned by endpoint)
            all_streams = global_state.list_streams(container_id="endpoint_test_container")
            assert len(all_streams) == 3, f"State should still have 3 total streams, got {len(all_streams)}"
            ended_streams = global_state.list_streams(status="ended", container_id="endpoint_test_container")
            assert len(ended_streams) == 3, f"Should have 3 ended streams in state, got {len(ended_streams)}"
            print(f"✅ State still has 3 ended streams (correctly not returned by endpoint)")
    
    print("\n✅ Endpoint fix test passed!")
    return True


def test_engine_endpoint_consistency():
    """Test that engine.streams list is consistent with endpoint response."""
    print("\n🧪 Testing engine.streams consistency with endpoint...")
    
    from app.services.state import state as global_state
    from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo
    from app.main import app
    from fastapi.testclient import TestClient
    
    # Use global state and clear it first
    global_state.clear_state()
    
    # Create test client
    client = TestClient(app)
    
    with patch('app.services.state.SessionLocal'):
        with patch('app.services.provisioner.clear_acestream_cache'):
            # Start 5 streams
            for i in range(5):
                evt_started = StreamStartedEvent(
                    container_id="consistency_container",
                    engine=EngineAddress(host="127.0.0.1", port=9100),
                    stream=StreamKey(key_type="content_id", key=f"consistency_content_{i}"),
                    session=SessionInfo(
                        playback_session_id=f"consistency_session_{i}",
                        stat_url=f"http://127.0.0.1:9100/stat_{i}",
                        command_url=f"http://127.0.0.1:9100/command_{i}",
                        is_live=1
                    ),
                    labels={"stream_id": f"consistency_stream_{i}"}
                )
                global_state.on_stream_started(evt_started)
            
            # Check consistency at different points
            for streams_to_end in range(6):  # 0 to 5
                # Get engine state
                engine = global_state.get_engine("consistency_container")
                expected_active = 5 - streams_to_end
                
                # Check engine.streams list
                assert len(engine.streams) == expected_active, \
                    f"Expected {expected_active} streams in engine.streams, got {len(engine.streams)}"
                
                # Check endpoint response
                response = client.get("/engines/consistency_container")
                assert response.status_code == 200
                data = response.json()
                endpoint_streams = data["streams"]
                
                assert len(endpoint_streams) == expected_active, \
                    f"Expected {expected_active} streams from endpoint, got {len(endpoint_streams)}"
                
                # Verify consistency between engine.streams and endpoint response
                endpoint_stream_ids = {s["id"] for s in endpoint_streams}
                engine_stream_ids = set(engine.streams)
                
                assert endpoint_stream_ids == engine_stream_ids, \
                    f"Mismatch: endpoint returned {endpoint_stream_ids}, engine.streams has {engine_stream_ids}"
                
                print(f"✅ After ending {streams_to_end} streams: {expected_active} active (consistent)")
                
                # End one more stream (if any left)
                if streams_to_end < 5:
                    evt_ended = StreamEndedEvent(
                        container_id="consistency_container",
                        stream_id=f"consistency_stream_{streams_to_end}",
                        reason="consistency_test"
                    )
                    global_state.on_stream_ended(evt_ended)
    
    print("\n✅ Consistency test passed!")
    return True


if __name__ == "__main__":
    print("🔧 Testing Engine Endpoint Fix")
    print("=" * 70)
    
    try:
        test_get_engine_endpoint_fix()
        test_engine_endpoint_consistency()
        
        print("\n" + "=" * 70)
        print("🎉 All endpoint fix tests passed!")
        print("\n✅ FIX VERIFIED: /engines/{container_id} now only returns active streams")
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
