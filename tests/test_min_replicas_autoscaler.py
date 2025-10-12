#!/usr/bin/env python3
"""
Test to verify that the autoscaler respects MIN_REPLICAS when deciding
whether empty engines can be stopped.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_min_replicas_respected_in_can_stop_engine():
    """Test that can_stop_engine respects MIN_REPLICAS constraint."""
    
    print("\n🧪 Testing MIN_REPLICAS constraint in can_stop_engine...")
    
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
        # We need to import the module and patch the imported reference
        from app.services import autoscaler, replica_validator as rv_module
        original_list_managed = autoscaler.list_managed
        original_rv_list_managed = rv_module.list_managed
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
        
        # Test scenario: 3 running engines, MIN_REPLICAS=3
        mock_containers = [
            MockContainer("engine_1", "running"),
            MockContainer("engine_2", "running"), 
            MockContainer("engine_3", "running")
        ]
        
        def mock_list_managed():
            return mock_containers
        
        # Patch the imported reference in both autoscaler and replica_validator modules
        autoscaler.list_managed = mock_list_managed
        rv_module.list_managed = mock_list_managed
        
        # Clear any cached results
        rv_module.replica_validator._cached_result = None
        rv_module.replica_validator._last_validation = None
        
        # Set MIN_REPLICAS for testing
        original_min = cfg.MIN_REPLICAS
        original_grace = cfg.ENGINE_GRACE_PERIOD_S
        cfg.MIN_REPLICAS = 3
        cfg.ENGINE_GRACE_PERIOD_S = 0  # Bypass grace period for immediate testing
        
        try:
            # Test 1: With MIN_REPLICAS=3 and 3 running engines (all free), none should be stoppable
            print("\nTest 1: MIN_REPLICAS=3, 3 free engines")
            # Clear cache before each test
            rv_module.replica_validator._cached_result = None
            can_stop_1 = can_stop_engine("engine_1", bypass_grace_period=True)
            rv_module.replica_validator._cached_result = None
            can_stop_2 = can_stop_engine("engine_2", bypass_grace_period=True) 
            rv_module.replica_validator._cached_result = None
            can_stop_3 = can_stop_engine("engine_3", bypass_grace_period=True)
            
            assert not can_stop_1, "Engine 1 should not be stoppable (would leave 2 free, need 3)"
            assert not can_stop_2, "Engine 2 should not be stoppable (would leave 2 free, need 3)"
            assert not can_stop_3, "Engine 3 should not be stoppable (would leave 2 free, need 3)"
            print("✅ All engines correctly protected by MIN_REPLICAS constraint")
            
            # Test 2: With 5 free engines, 1 should be stoppable (would leave 4 free, which is > MIN_REPLICAS=3)
            print("\nTest 2: MIN_REPLICAS=3, 5 free engines")
            mock_containers.append(MockContainer("engine_4", "running"))
            mock_containers.append(MockContainer("engine_5", "running"))
            
            rv_module.replica_validator._cached_result = None
            can_stop_5 = can_stop_engine("engine_5", bypass_grace_period=True)
            assert can_stop_5, "Engine 5 should be stoppable (would leave 4 free, above MIN_REPLICAS=3)"
            print("✅ Extra free engine above MIN_REPLICAS can be stopped")
            
            # Test 2b: With 4 free engines, 1 should be stoppable (would leave 3 free, which equals MIN_REPLICAS)
            print("\nTest 2b: MIN_REPLICAS=3, 4 free engines")
            mock_containers.pop()  # Remove engine_5, leaving 4 engines
            
            rv_module.replica_validator._cached_result = None
            can_stop_4 = can_stop_engine("engine_4", bypass_grace_period=True)
            assert can_stop_4, "Engine 4 should be stoppable (would leave 3 free, which satisfies MIN_REPLICAS=3)"
            print("✅ Engine that would leave exactly MIN_REPLICAS free can be stopped")
            
            # Test 3: With MIN_REPLICAS=0, all empty engines should be stoppable
            print("\nTest 3: MIN_REPLICAS=0, engines should be stoppable")
            cfg.MIN_REPLICAS = 0
            
            rv_module.replica_validator._cached_result = None
            can_stop_1_zero = can_stop_engine("engine_1", bypass_grace_period=True)
            assert can_stop_1_zero, "Engine 1 should be stoppable when MIN_REPLICAS=0"
            print("✅ Engines can be stopped when MIN_REPLICAS=0")
            
            # Test 4: Test grace period still works with MIN_REPLICAS
            print("\nTest 4: MIN_REPLICAS=2, grace period still applies")
            cfg.MIN_REPLICAS = 2
            cfg.ENGINE_GRACE_PERIOD_S = 30  # Restore grace period
            mock_containers = mock_containers[:4]  # Reset to 4 engines (above MIN_REPLICAS=2)
            
            # First call should start grace period (not bypass it)
            rv_module.replica_validator._cached_result = None
            can_stop_first = can_stop_engine("engine_4", bypass_grace_period=False)
            assert not can_stop_first, "Engine should be in grace period"
            assert "engine_4" in _empty_engine_timestamps, "Grace period should be tracked"
            
            # Simulate time passing beyond grace period
            past_time = datetime.now() - timedelta(seconds=31)
            _empty_engine_timestamps["engine_4"] = past_time
            
            rv_module.replica_validator._cached_result = None
            can_stop_after_grace = can_stop_engine("engine_4", bypass_grace_period=False)
            assert can_stop_after_grace, "Engine should be stoppable after grace period (would leave 3 free, above MIN_REPLICAS=2)"
            print("✅ Grace period still works correctly with MIN_REPLICAS")
            
            print("\n🎯 All tests PASSED: MIN_REPLICAS constraint is properly respected")
            return True
            
        finally:
            # Restore original values
            cfg.MIN_REPLICAS = original_min
            cfg.ENGINE_GRACE_PERIOD_S = original_grace
            if original_list_managed:
                autoscaler.list_managed = original_list_managed
            if original_rv_list_managed:
                rv_module.list_managed = original_rv_list_managed
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_min_replicas_with_active_streams():
    """Test that engines with active streams are never stopped regardless of MIN_REPLICAS."""
    
    print("\n🧪 Testing engines with active streams...")
    
    try:
        from app.services.autoscaler import can_stop_engine, _empty_engine_timestamps
        from app.services.state import state
        from app.models.schemas import EngineState, StreamState
        from app.core.config import cfg
        from app.services import health
        from datetime import datetime, timezone
        
        # Clear state
        state.engines.clear()
        state.streams.clear()
        _empty_engine_timestamps.clear()
        
        # Mock containers
        from app.services import autoscaler, replica_validator as rv_module
        original_list_managed = autoscaler.list_managed
        original_rv_list_managed = rv_module.list_managed
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
        
        def mock_list_managed():
            return [MockContainer("engine_with_stream", "running")]
        
        autoscaler.list_managed = mock_list_managed
        rv_module.list_managed = mock_list_managed
        rv_module.replica_validator._cached_result = None
        
        # Set MIN_REPLICAS=1 
        original_min = cfg.MIN_REPLICAS
        cfg.MIN_REPLICAS = 1
        
        try:
            # Create an engine with an active stream
            stream = StreamState(
                id="test_stream",
                key_type="content_id", 
                key="12345",
                container_id="engine_with_stream",
                playback_session_id="session_123",
                stat_url="http://127.0.0.1:8080/stat",
                command_url="http://127.0.0.1:8080/cmd",
                is_live=True,
                started_at=datetime.now(timezone.utc),
                status="started"
            )
            state.streams["test_stream"] = stream
            
            # Engine with active stream should never be stopped, even if above MIN_REPLICAS
            can_stop = can_stop_engine("engine_with_stream", bypass_grace_period=True)
            assert not can_stop, "Engine with active stream should never be stopped"
            print("✅ Engine with active stream is protected")
            
            return True
            
        finally:
            cfg.MIN_REPLICAS = original_min
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
    print("🚀 Starting MIN_REPLICAS autoscaler tests...")
    
    success = True
    success &= test_min_replicas_respected_in_can_stop_engine()
    success &= test_min_replicas_with_active_streams()
    
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