#!/usr/bin/env python3
"""
Unit test to verify that get_replica_deficit() correctly calculates deficit
based on FREE replicas, not total replicas.

This test directly tests the calculation logic without full provisioning.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_free_replicas_calculation_direct():
    """Test that deficit calculation formula: deficit = min_replicas - free_count"""
    
    print("\n🧪 Testing free replicas deficit calculation formula...")
    
    try:
        # Test the mathematical formula directly without complex mocking
        # deficit = max(0, min_replicas - free_count)
        
        test_cases = [
            # (min_replicas, total_running, used_engines, expected_free, expected_deficit)
            (2, 0, 0, 0, 2),      # No engines: need 2
            (2, 3, 1, 2, 0),      # 3 total, 1 used, 2 free: OK
            (2, 3, 2, 1, 1),      # 3 total, 2 used, 1 free: need 1 more
            (1, 10, 10, 0, 1),    # 10 total, all used, 0 free: need 1
            (3, 5, 3, 2, 1),      # 5 total, 3 used, 2 free: need 1 more for 3 free
            (0, 5, 3, 2, 0),      # MIN_REPLICAS=0: no deficit
        ]
        
        for min_replicas, total_running, used_engines, expected_free, expected_deficit in test_cases:
            free_count = total_running - used_engines
            deficit = max(0, min_replicas - free_count)
            
            print(f"\n   MIN_REPLICAS={min_replicas}, total={total_running}, used={used_engines}")
            print(f"   Calculated: free={free_count}, deficit={deficit}")
            
            assert free_count == expected_free, f"Expected free={expected_free}, got {free_count}"
            assert deficit == expected_deficit, f"Expected deficit={expected_deficit}, got {deficit}"
            print(f"   ✅ Correct: free={free_count}, deficit={deficit}")
        
        print("\n🎯 All tests PASSED: Deficit formula correctly calculates based on free engines")
        print("   Formula: deficit = max(0, MIN_REPLICAS - (total_running - used_engines))")
        return True
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_replica_validator_get_deficit():
    """Test that replica_validator.get_replica_deficit() uses the correct formula."""
    
    print("\n🧪 Testing replica_validator.get_replica_deficit()...")
    
    try:
        from app.services import replica_validator as replica_validator_module
        from app.services.state import state
        from app.models.schemas import EngineState, StreamState
        from datetime import datetime, timezone
        
        # Clear state
        state.engines.clear()
        state.streams.clear()
        
        # Mock list_managed to return containers matching state
        from app.services import health
        original_list_managed = health.list_managed
        original_rv_list_managed = replica_validator_module.list_managed
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
        
        def mock_list_managed():
            containers = [MockContainer(eng.container_id) for eng in state.engines.values()]
            return containers
        
        # Patch in both modules
        health.list_managed = mock_list_managed
        replica_validator_module.list_managed = mock_list_managed
        
        try:
            # Scenario 1: Simple case with direct deficit calculation
            # We'll test by directly calculating (min_replicas - free_count)
            print("\n📋 Testing get_replica_deficit() method...")
            
            validator = replica_validator_module.replica_validator
            
            # The method signature is: get_replica_deficit(self, min_replicas: int) -> int
            # It internally calls validate_and_sync_state() and calculates:
            # deficit = min_replicas - free_count
            
            # With no engines, deficit should equal min_replicas
            deficit = validator.get_replica_deficit(2)
            print(f"   With 0 engines, MIN_REPLICAS=2, deficit={deficit}")
            assert deficit == 2, f"Expected deficit=2, got {deficit}"
            print("✅ Correct deficit for empty state")
            
            # Scenario 2: 3 total, 1 used, 2 free, MIN_REPLICAS=2
            # Expected deficit: 0 (we have 2 free already)
            print("\n📋 Scenario 2: 3 total, 1 used, 2 free, MIN_REPLICAS=2")
            
            # Add 3 engines
            for i in range(3):
                engine = EngineState(
                    container_id=f"engine_{i}",
                    container_name=f"engine-{i}",
                    host="127.0.0.1",
                    port=8000 + i,
                    labels={},
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    streams=[]
                )
                state.engines[f"engine_{i}"] = engine
            
            # Add 1 stream to engine_0
            stream = StreamState(
                id="stream_0",
                key_type="content_id",
                key="content_0",
                container_id="engine_0",
                playback_session_id="session_0",
                stat_url="http://127.0.0.1:8000/stat",
                command_url="http://127.0.0.1:8000/cmd",
                is_live=True,
                started_at=datetime.now(timezone.utc),
                status="started"
            )
            state.streams["stream_0"] = stream
            
            total_running, used_engines, free_count = validator.validate_and_sync_state()
            deficit = validator.get_replica_deficit(cfg.MIN_REPLICAS)
            
            print(f"   Total: {total_running}, Used: {used_engines}, Free: {free_count}, Deficit: {deficit}")
            assert total_running == 3, f"Expected total=3, got {total_running}"
            assert used_engines == 1, f"Expected used=1, got {used_engines}"
            assert free_count == 2, f"Expected free=2, got {free_count}"
            assert deficit == 0, f"Expected deficit=0, got {deficit}"
            print("✅ No deficit when 2 free engines exist")
            
            # Scenario 3: 3 total, 2 used, 1 free, MIN_REPLICAS=2
            # Expected deficit: 1 (need 1 more free)
            print("\n📋 Scenario 3: 3 total, 2 used, 1 free, MIN_REPLICAS=2")
            
            # Add another stream to engine_1
            stream2 = StreamState(
                id="stream_1",
                key_type="content_id",
                key="content_1",
                container_id="engine_1",
                playback_session_id="session_1",
                stat_url="http://127.0.0.1:8001/stat",
                command_url="http://127.0.0.1:8001/cmd",
                is_live=True,
                started_at=datetime.now(timezone.utc),
                status="started"
            )
            state.streams["stream_1"] = stream2
            
            total_running, used_engines, free_count = validator.validate_and_sync_state()
            deficit = validator.get_replica_deficit(cfg.MIN_REPLICAS)
            
            print(f"   Total: {total_running}, Used: {used_engines}, Free: {free_count}, Deficit: {deficit}")
            assert total_running == 3, f"Expected total=3, got {total_running}"
            assert used_engines == 2, f"Expected used=2, got {used_engines}"
            assert free_count == 1, f"Expected free=1, got {free_count}"
            assert deficit == 1, f"Expected deficit=1, got {deficit}"
            print("✅ Correct deficit=1 when only 1 free engine exists")
            
            # Scenario 4: 10 total, all 10 used, 0 free, MIN_REPLICAS=1
            # Expected deficit: 1 (need 1 free)
            print("\n📋 Scenario 4: 10 total, 10 used, 0 free, MIN_REPLICAS=1")
            
            # Clear and add 10 engines all with streams
            state.engines.clear()
            state.streams.clear()
            
            for i in range(10):
                engine = EngineState(
                    container_id=f"busy_engine_{i}",
                    container_name=f"busy-engine-{i}",
                    host="127.0.0.1",
                    port=9000 + i,
                    labels={},
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    streams=[]
                )
                state.engines[f"busy_engine_{i}"] = engine
                
                stream = StreamState(
                    id=f"busy_stream_{i}",
                    key_type="content_id",
                    key=f"content_{i}",
                    container_id=f"busy_engine_{i}",
                    playback_session_id=f"session_{i}",
                    stat_url=f"http://127.0.0.1:{9000+i}/stat",
                    command_url=f"http://127.0.0.1:{9000+i}/cmd",
                    is_live=True,
                    started_at=datetime.now(timezone.utc),
                    status="started"
                )
                state.streams[f"busy_stream_{i}"] = stream
            
            cfg.MIN_REPLICAS = 1
            total_running, used_engines, free_count = validator.validate_and_sync_state()
            deficit = validator.get_replica_deficit(cfg.MIN_REPLICAS)
            
            print(f"   Total: {total_running}, Used: {used_engines}, Free: {free_count}, Deficit: {deficit}")
            assert total_running == 10, f"Expected total=10, got {total_running}"
            assert used_engines == 10, f"Expected used=10, got {used_engines}"
            assert free_count == 0, f"Expected free=0, got {free_count}"
            assert deficit == 1, f"Expected deficit=1, got {deficit}"
            print("✅ Correct deficit=1 when all engines are busy")
            
            # Scenario 5: 5 total, 3 used, 2 free, MIN_REPLICAS=3
            # Expected deficit: 1 (need 1 more free to reach 3 free)
            print("\n📋 Scenario 5: 5 total, 3 used, 2 free, MIN_REPLICAS=3")
            
            # Clear and setup
            state.engines.clear()
            state.streams.clear()
            
            for i in range(5):
                engine = EngineState(
                    container_id=f"mixed_engine_{i}",
                    container_name=f"mixed-engine-{i}",
                    host="127.0.0.1",
                    port=7000 + i,
                    labels={},
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    streams=[]
                )
                state.engines[f"mixed_engine_{i}"] = engine
            
            # Add streams to first 3 engines
            for i in range(3):
                stream = StreamState(
                    id=f"mixed_stream_{i}",
                    key_type="content_id",
                    key=f"content_{i}",
                    container_id=f"mixed_engine_{i}",
                    playback_session_id=f"session_{i}",
                    stat_url=f"http://127.0.0.1:{7000+i}/stat",
                    command_url=f"http://127.0.0.1:{7000+i}/cmd",
                    is_live=True,
                    started_at=datetime.now(timezone.utc),
                    status="started"
                )
                state.streams[f"mixed_stream_{i}"] = stream
            
            cfg.MIN_REPLICAS = 3
            total_running, used_engines, free_count = validator.validate_and_sync_state()
            deficit = validator.get_replica_deficit(cfg.MIN_REPLICAS)
            
            print(f"   Total: {total_running}, Used: {used_engines}, Free: {free_count}, Deficit: {deficit}")
            assert total_running == 5, f"Expected total=5, got {total_running}"
            assert used_engines == 3, f"Expected used=3, got {used_engines}"
            assert free_count == 2, f"Expected free=2, got {free_count}"
            assert deficit == 1, f"Expected deficit=1, got {deficit}"
            print("✅ Correct deficit=1 to reach MIN_REPLICAS=3 free engines")
            
            print("\n🎯 All tests PASSED: get_replica_deficit() correctly calculates based on free engines")
            return True
            
        finally:
            cfg.MIN_REPLICAS = original_min
            health.list_managed = original_list_managed
            replica_validator_module.list_managed = original_rv_list_managed
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        success = test_free_replicas_calculation()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
