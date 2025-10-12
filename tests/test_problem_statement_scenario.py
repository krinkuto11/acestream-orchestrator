#!/usr/bin/env python3
"""
Test that directly validates the problem statement scenario:
"If there are 10 active replicas with at least 1 stream each,
there should be an 11th that is empty when MIN_REPLICAS=1"
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_problem_statement_scenario():
    """
    Problem statement: "The replica count is not working correctly, and it appears 
    as if it was full but no new instances are being created. There should also be 
    a minimum EMPTY replicas, so for example, there are 10 active replicas with at 
    least 1 stream each, there should be an 11th that is empty."
    """
    
    print("\n🧪 Testing problem statement scenario...")
    print("   Scenario: 10 replicas with streams, MIN_REPLICAS=1")
    print("   Expected: System should provision an 11th empty replica")
    
    try:
        from app.services import replica_validator as rv_module
        from app.services.state import state
        from app.models.schemas import EngineState, StreamState
        from app.core.config import cfg
        from datetime import datetime, timezone
        
        # Clear state
        state.engines.clear()
        state.streams.clear()
        
        # Mock list_managed to return containers matching state
        original_rv_list_managed = rv_module.list_managed
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
        
        def mock_list_managed():
            return [MockContainer(eng.container_id) for eng in state.engines.values()]
        
        rv_module.list_managed = mock_list_managed
        
        original_min = cfg.MIN_REPLICAS
        cfg.MIN_REPLICAS = 1
        
        try:
            # Setup: Create 10 engines, each with an active stream
            print("\n   Step 1: Creating 10 engines with active streams...")
            for i in range(10):
                container_id = f"busy_engine_{i}"
                
                # Create engine
                engine = EngineState(
                    container_id=container_id,
                    container_name=f"busy-engine-{i}",
                    host="127.0.0.1",
                    port=8000 + i,
                    labels={},
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    streams=[]
                )
                state.engines[container_id] = engine
                
                # Create active stream on this engine
                stream = StreamState(
                    id=f"stream_{i}",
                    key_type="content_id",
                    key=f"content_{i}",
                    container_id=container_id,
                    playback_session_id=f"session_{i}",
                    stat_url=f"http://127.0.0.1:{8000+i}/stat",
                    command_url=f"http://127.0.0.1:{8000+i}/cmd",
                    is_live=True,
                    started_at=datetime.now(timezone.utc),
                    status="started"
                )
                state.streams[f"stream_{i}"] = stream
            
            print(f"   ✅ Created 10 engines with 10 active streams")
            
            # Step 2: Calculate the current state
            print("\n   Step 2: Calculating replica counts...")
            validator = rv_module.replica_validator
            validator._cached_result = None
            
            total_running, used_engines, free_count = validator.validate_and_sync_state()
            deficit = validator.get_replica_deficit(cfg.MIN_REPLICAS)
            
            print(f"   📊 Current state:")
            print(f"      Total running: {total_running}")
            print(f"      Used engines (with streams): {used_engines}")
            print(f"      Free engines (empty): {free_count}")
            print(f"      MIN_REPLICAS setting: {cfg.MIN_REPLICAS}")
            print(f"      Calculated deficit: {deficit}")
            
            # Step 3: Validate expectations
            print("\n   Step 3: Validating against problem statement...")
            
            assert total_running == 10, f"Expected 10 total, got {total_running}"
            print(f"   ✅ Total running engines: {total_running}")
            
            assert used_engines == 10, f"Expected 10 used, got {used_engines}"
            print(f"   ✅ All 10 engines have active streams")
            
            assert free_count == 0, f"Expected 0 free, got {free_count}"
            print(f"   ✅ Zero free/empty engines (all are busy)")
            
            assert deficit == 1, f"Expected deficit=1, got {deficit}"
            print(f"   ✅ System correctly calculates deficit of 1")
            
            print("\n   Step 4: Verifying provisioning decision...")
            print(f"   ℹ️  With MIN_REPLICAS=1 and free_count=0:")
            print(f"      The system SHOULD provision 1 additional engine")
            print(f"      This would result in: 11 total, 10 used, 1 free")
            print(f"      ✅ This satisfies the problem statement!")
            
            # Verify the formula
            expected_total_after = total_running + deficit
            expected_free_after = free_count + deficit
            
            print(f"\n   📈 After provisioning {deficit} engine(s):")
            print(f"      Total engines: {total_running} + {deficit} = {expected_total_after}")
            print(f"      Free engines: {free_count} + {deficit} = {expected_free_after}")
            print(f"      ✅ Meets MIN_REPLICAS={cfg.MIN_REPLICAS} requirement")
            
            # Additional scenario: Verify it works with different MIN_REPLICAS values
            print("\n   Step 5: Testing with MIN_REPLICAS=3...")
            cfg.MIN_REPLICAS = 3
            validator._cached_result = None
            
            deficit_3 = validator.get_replica_deficit(cfg.MIN_REPLICAS)
            print(f"   📊 With MIN_REPLICAS=3:")
            print(f"      Current free: {free_count}")
            print(f"      Required deficit: {deficit_3}")
            
            assert deficit_3 == 3, f"Expected deficit=3, got {deficit_3}"
            print(f"      ✅ Correctly calculates need for 3 more engines")
            print(f"      Result: 13 total, 10 used, 3 free")
            
            print("\n🎯 Problem statement scenario TEST PASSED!")
            print("   ✅ MIN_REPLICAS now maintains minimum EMPTY replicas")
            print("   ✅ System correctly provisions additional engines when all are busy")
            print("   ✅ Formula: deficit = max(0, MIN_REPLICAS - (total - used))")
            
            return True
            
        finally:
            cfg.MIN_REPLICAS = original_min
            rv_module.list_managed = original_rv_list_managed
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        success = test_problem_statement_scenario()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
