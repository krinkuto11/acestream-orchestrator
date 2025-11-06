#!/usr/bin/env python3
"""
Test to verify that engines part of MIN_REPLICAS or MIN_FREE_REPLICAS
are not added to grace period tracking, preventing repeated warnings.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_grace_period_tracking_cleanup():
    """Test that engines protected by MIN_REPLICAS/MIN_FREE_REPLICAS are not tracked in grace period."""
    
    print("\n🧪 Testing grace period tracking cleanup for protected engines...")
    
    try:
        from app.services.autoscaler import can_stop_engine, _empty_engine_timestamps
        from app.services.state import state
        from app.models.schemas import EngineState
        from app.core.config import cfg
        from app.services import health
        from datetime import datetime, timezone, timedelta
        
        # Clear state and grace period tracking
        state.engines.clear()
        state.streams.clear()
        _empty_engine_timestamps.clear()
        
        # Mock Docker containers to avoid actual container operations
        from app.services import autoscaler, replica_validator as rv_module
        original_list_managed = autoscaler.list_managed
        original_rv_list_managed = rv_module.list_managed
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
        
        # Test scenario: 2 running engines, MIN_REPLICAS=2
        mock_containers = [
            MockContainer("engine_1", "running"),
            MockContainer("engine_2", "running")
        ]
        
        def mock_list_managed():
            return mock_containers
        
        autoscaler.list_managed = mock_list_managed
        rv_module.list_managed = mock_list_managed
        rv_module.replica_validator._cached_result = None
        rv_module.replica_validator._last_validation = None
        
        # Set MIN_REPLICAS for testing
        original_min = cfg.MIN_REPLICAS
        original_grace = cfg.ENGINE_GRACE_PERIOD_S
        cfg.MIN_REPLICAS = 2
        cfg.ENGINE_GRACE_PERIOD_S = 30  # Use actual grace period
        
        try:
            # Test 1: Check that engines protected by MIN_REPLICAS are not tracked in grace period
            print("\nTest 1: Engines protected by MIN_REPLICAS should not be in grace period tracking")
            
            # Clear cache
            rv_module.replica_validator._cached_result = None
            
            # Try to stop engine_1 (should fail due to MIN_REPLICAS)
            can_stop = can_stop_engine("engine_1", bypass_grace_period=False)
            assert not can_stop, "Engine should not be stoppable (MIN_REPLICAS constraint)"
            
            # Verify engine is NOT in grace period tracking
            assert "engine_1" not in _empty_engine_timestamps, \
                "Engine protected by MIN_REPLICAS should NOT be in grace period tracking"
            print("✅ Engine protected by MIN_REPLICAS is not tracked in grace period")
            
            # Test 2: Multiple calls should not add it to grace period tracking
            print("\nTest 2: Multiple calls should not re-add protected engine to grace period")
            
            # Call again
            rv_module.replica_validator._cached_result = None
            can_stop = can_stop_engine("engine_1", bypass_grace_period=False)
            assert not can_stop, "Engine should still not be stoppable"
            assert "engine_1" not in _empty_engine_timestamps, \
                "Engine should still NOT be in grace period tracking after multiple calls"
            print("✅ Protected engine stays out of grace period tracking after multiple calls")
            
            # Test 3: When an engine was in grace period but becomes protected, it should be removed
            print("\nTest 3: Engine in grace period that becomes protected should be removed from tracking")
            
            # Add a third engine
            mock_containers.append(MockContainer("engine_3", "running"))
            rv_module.replica_validator._cached_result = None
            
            # Engine 3 should start grace period (would leave 2 engines, which equals MIN_REPLICAS)
            # First call starts the grace period
            can_stop = can_stop_engine("engine_3", bypass_grace_period=False)
            # This should start grace period since it's the first check
            assert not can_stop or "engine_3" in _empty_engine_timestamps, \
                "Engine should either not be stoppable or start grace period"
            
            # Clear the grace period if it was started
            _empty_engine_timestamps.clear()
            
            # Now remove engine_3 from mock to simulate it not being stopped
            mock_containers.pop()
            rv_module.replica_validator._cached_result = None
            
            # Manually add engine_1 to grace period tracking to simulate a previous state
            _empty_engine_timestamps["engine_1"] = datetime.now()
            
            # Now check engine_1 again - it should be removed from tracking
            rv_module.replica_validator._cached_result = None
            can_stop = can_stop_engine("engine_1", bypass_grace_period=False)
            assert not can_stop, "Engine should not be stoppable (MIN_REPLICAS constraint)"
            assert "engine_1" not in _empty_engine_timestamps, \
                "Engine should be removed from grace period tracking when protected"
            print("✅ Engine in grace period is removed when it becomes protected")
            
            # Test 4: Test with MIN_FREE_REPLICAS constraint
            print("\nTest 4: Engines protected by MIN_FREE_REPLICAS should not be tracked")
            
            # Add more engines to test MIN_FREE_REPLICAS
            cfg.MIN_REPLICAS = 1  # Lower MIN_REPLICAS
            cfg.MIN_FREE_REPLICAS = 2  # Set MIN_FREE_REPLICAS
            _empty_engine_timestamps.clear()  # Clear any previous tracking
            mock_containers = [
                MockContainer("engine_a", "running"),
                MockContainer("engine_b", "running"),
                MockContainer("engine_c", "running")
            ]
            rv_module.replica_validator._cached_result = None
            
            # Try to stop engine_c (would leave 2 free, which equals MIN_FREE_REPLICAS)
            # With MIN_FREE_REPLICAS=2, stopping engine_c would leave 2 free (engines a and b)
            # First call starts the grace period since free_count=3 and 3-1=2 equals MIN_FREE_REPLICAS
            can_stop = can_stop_engine("engine_c", bypass_grace_period=False)
            # This starts grace period on first check
            assert not can_stop or "engine_c" in _empty_engine_timestamps, \
                "Engine should start grace period or be stoppable"
            
            # Clear tracking for next test
            _empty_engine_timestamps.clear()
            
            # Now try with 2 engines (at the limit)
            mock_containers.pop()  # Remove engine_c
            rv_module.replica_validator._cached_result = None
            
            # Try to stop engine_b (would leave 1 free, which is less than MIN_FREE_REPLICAS=2)
            can_stop = can_stop_engine("engine_b", bypass_grace_period=False)
            assert not can_stop, "Engine should not be stoppable (would violate MIN_FREE_REPLICAS)"
            assert "engine_b" not in _empty_engine_timestamps, \
                "Engine protected by MIN_FREE_REPLICAS should NOT be in grace period tracking"
            print("✅ Engine protected by MIN_FREE_REPLICAS is not tracked in grace period")
            
            print("\n🎯 All tests PASSED: Grace period tracking is properly cleaned up for protected engines")
            return True
            
        finally:
            # Restore original values
            cfg.MIN_REPLICAS = original_min
            cfg.ENGINE_GRACE_PERIOD_S = original_grace
            cfg.MIN_FREE_REPLICAS = 1  # Reset to default
            if original_list_managed:
                autoscaler.list_managed = original_list_managed
            if original_rv_list_managed:
                rv_module.list_managed = original_rv_list_managed
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("🚀 Starting grace period tracking cleanup tests...")
    
    success = test_grace_period_tracking_cleanup()
    
    print(f"\n🎯 Overall result: {'PASSED' if success else 'FAILED'}")
    return success

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Tests failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
