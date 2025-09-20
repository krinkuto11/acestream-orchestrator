#!/usr/bin/env python3
"""
Test to verify that when streams end and containers are auto-deleted,
the system properly:
1. Removes engines from state
2. Maintains minimum replicas
3. Updates the /engines endpoint correctly
"""

import os
import sys
import time
import threading
from unittest.mock import Mock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(__file__))

def test_stream_ending_behavior():
    """Test that stream ending with AUTO_DELETE properly manages state and replicas."""
    
    print("🧪 Testing stream ending behavior with AUTO_DELETE...")
    
    try:
        from app.services.state import state
        from app.models.schemas import StreamEndedEvent, EngineState, StreamState
        from app.core.config import cfg
        from app.main import ev_stream_ended
        from fastapi import BackgroundTasks
        
        # Mock configuration for testing
        original_auto_delete = cfg.AUTO_DELETE
        original_min_replicas = cfg.MIN_REPLICAS
        cfg.AUTO_DELETE = True
        cfg.MIN_REPLICAS = 2
        
        print(f"✓ Set AUTO_DELETE={cfg.AUTO_DELETE}, MIN_REPLICAS={cfg.MIN_REPLICAS}")
        
        # Clear state first
        state.engines.clear()
        state.streams.clear()
        
        # Set up test data - create a fake engine and stream
        test_container_id = "test_container_123"
        test_stream_id = "test_stream_456"
        
        # Add an engine to state
        engine = EngineState(
            container_id=test_container_id,
            container_name="test-container",
            host="127.0.0.1",
            port=8080,
            labels={},
            first_seen=state.now(),
            last_seen=state.now(),
            streams=[test_stream_id]
        )
        state.engines[test_container_id] = engine
        
        # Add a stream to state
        stream = StreamState(
            id=test_stream_id,
            key_type="content_id",
            key="12345",
            container_id=test_container_id,
            playback_session_id="session_123",
            stat_url="http://127.0.0.1:8080/stat",
            command_url="http://127.0.0.1:8080/cmd",
            is_live=True,
            started_at=state.now(),
            status="started"
        )
        state.streams[test_stream_id] = stream
        
        print(f"✓ Created test engine {test_container_id} and stream {test_stream_id}")
        print(f"✓ Engines in state before: {len(state.engines)}")
        
        # Mock the container operations and autoscaler
        with patch('app.main.stop_container') as mock_stop, \
             patch('app.services.autoscaler.ensure_minimum') as mock_ensure:
            
            # Create event for stream ending
            event = StreamEndedEvent(
                stream_id=test_stream_id,
                container_id=test_container_id
            )
            
            # Mock background tasks
            bg_tasks = BackgroundTasks()
            
            # Call the stream ended handler
            print("📋 Calling ev_stream_ended...")
            result = ev_stream_ended(event, bg_tasks)
            
            print(f"✓ ev_stream_ended returned: {result}")
            
            # Execute background tasks synchronously for testing
            print("📋 Executing background tasks...")
            for task in bg_tasks.tasks:
                try:
                    task.func(*task.args, **task.kwargs)
                except Exception as e:
                    print(f"⚠️ Background task failed: {e}")
            
            # Verify behavior
            print("📋 Verifying results...")
            
            # Check that stop_container was called
            mock_stop.assert_called_once_with(test_container_id)
            print("✓ stop_container was called")
            
            # Check that ensure_minimum was called
            mock_ensure.assert_called_once()
            print("✓ ensure_minimum was called")
            
            # Check that engine was removed from state
            engines_after = len(state.engines)
            print(f"✓ Engines in state after: {engines_after}")
            assert engines_after == 0, f"Expected 0 engines, got {engines_after}"
            
            # Check that the specific engine was removed
            assert test_container_id not in state.engines, "Engine should be removed from state"
            print("✓ Engine was properly removed from state")
            
            # Check that stream status was updated
            updated_stream = state.streams.get(test_stream_id)
            if updated_stream:
                assert updated_stream.status == "ended", f"Stream status should be 'ended', got '{updated_stream.status}'"
                print("✓ Stream status was properly updated to 'ended'")
            
        print("\n🎯 Test PASSED: Stream ending properly removes engines and maintains replicas")
        return True
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Restore original config
        if 'original_auto_delete' in locals():
            cfg.AUTO_DELETE = original_auto_delete
        if 'original_min_replicas' in locals():
            cfg.MIN_REPLICAS = original_min_replicas

def test_engines_endpoint_consistency():
    """Test that /engines endpoint returns consistent data after container deletion."""
    
    print("\n🧪 Testing /engines endpoint consistency...")
    
    try:
        from app.services.state import state
        from app.models.schemas import EngineState
        from app.main import get_engines
        
        # Clear state
        state.engines.clear()
        
        # Add some engines
        for i in range(3):
            container_id = f"container_{i}"
            engine = EngineState(
                container_id=container_id,
                container_name=f"test-container-{i}",
                host="127.0.0.1",
                port=8080 + i,
                labels={},
                first_seen=state.now(),
                last_seen=state.now(),
                streams=[]
            )
            state.engines[container_id] = engine
        
        print(f"✓ Added 3 engines to state")
        
        # Test /engines endpoint
        engines = get_engines()
        assert len(engines) == 3, f"Expected 3 engines, got {len(engines)}"
        print(f"✓ /engines endpoint returns {len(engines)} engines")
        
        # Remove one engine
        removed = state.remove_engine("container_1")
        assert removed is not None, "Should have removed an engine"
        print("✓ Removed one engine using remove_engine()")
        
        # Test /engines endpoint again
        engines = get_engines()
        assert len(engines) == 2, f"Expected 2 engines after removal, got {len(engines)}"
        print(f"✓ /engines endpoint now returns {len(engines)} engines")
        
        # Verify the removed engine is not in the list
        container_ids = [e.container_id for e in engines]
        assert "container_1" not in container_ids, "Removed engine should not be in list"
        print("✓ Removed engine is not in the engines list")
        
        print("\n🎯 Test PASSED: /engines endpoint properly reflects state changes")
        return True
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 Running stream ending behavior tests...\n")
    
    success1 = test_stream_ending_behavior()
    success2 = test_engines_endpoint_consistency()
    
    overall_success = success1 and success2
    
    print(f"\n🎯 Overall result: {'PASSED' if overall_success else 'FAILED'}")
    sys.exit(0 if overall_success else 1)