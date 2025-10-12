#!/usr/bin/env python3
"""
Test that autoscaler respects MAX_ACTIVE_REPLICAS when using Gluetun.
This is a simple test that verifies the core fix.
"""

import sys
import os
from unittest.mock import patch, MagicMock

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_ensure_minimum_at_max_limit():
    """Test that ensure_minimum stops when at MAX_ACTIVE_REPLICAS limit."""
    print("\n🧪 Testing ensure_minimum at MAX_ACTIVE_REPLICAS limit...")
    
    try:
        from app.services.autoscaler import ensure_minimum
        from app.services.state import state
        from app.core.config import cfg
        from app.models.schemas import EngineState
        from datetime import datetime, timezone
        
        # Save original values
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_min_replicas = cfg.MIN_REPLICAS
        original_max_active = cfg.MAX_ACTIVE_REPLICAS
        
        try:
            # Set up test scenario: Using Gluetun, at MAX_ACTIVE_REPLICAS limit
            cfg.GLUETUN_CONTAINER_NAME = "gluetun"
            cfg.MIN_REPLICAS = 10
            cfg.MAX_ACTIVE_REPLICAS = 20
            
            # Clear state
            state.engines.clear()
            state.streams.clear()
            
            # Simulate 20 running containers (at MAX_ACTIVE_REPLICAS limit)
            mock_containers = []
            for i in range(20):
                container_id = f"test_engine_{i}"
                engine = EngineState(
                    container_id=container_id,
                    container_name=f"test-engine-{i}",
                    host="127.0.0.1",
                    port=8080 + i,
                    labels={},
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    streams=[]
                )
                state.engines[container_id] = engine
                
                mock_container = MagicMock()
                mock_container.id = container_id
                mock_container.status = "running"
                mock_containers.append(mock_container)
            
            print(f"   Created {len(state.engines)} engines in state (at MAX_ACTIVE_REPLICAS limit)")
            
            # Mock Docker status to return 20 running containers
            mock_docker_status = {
                'total_running': 20,
                'containers': mock_containers,
                'running_container_ids': {c.id for c in mock_containers},
                'container_details': {}
            }
            
            # Track if start_acestream is called
            start_called = [False]
            
            def mock_start_acestream(*args, **kwargs):
                start_called[0] = True
                raise RuntimeError("Should not be called!")
            
            # Mock replica_validator to return our docker status
            with patch('app.services.replica_validator.replica_validator') as mock_validator:
                mock_validator.get_docker_container_status.return_value = mock_docker_status
                
                # Mock circuit_breaker_manager to allow provisioning
                with patch('app.services.circuit_breaker.circuit_breaker_manager') as mock_breaker:
                    mock_breaker.can_provision.return_value = True
                    
                    # Patch start_acestream to track if it's called
                    with patch('app.services.autoscaler.start_acestream', side_effect=mock_start_acestream):
                        # Call ensure_minimum - it should NOT try to start containers
                        ensure_minimum()
                        
                        # Verify that start_acestream was NOT called
                        assert not start_called[0], "start_acestream should not be called when at MAX_ACTIVE_REPLICAS limit"
                        print("   ✅ ensure_minimum correctly avoided starting containers at MAX_ACTIVE_REPLICAS limit")
            
            print("\n🎯 Test PASSED: ensure_minimum respects MAX_ACTIVE_REPLICAS limit")
            return True
            
        finally:
            # Restore original values
            cfg.GLUETUN_CONTAINER_NAME = original_gluetun
            cfg.MIN_REPLICAS = original_min_replicas
            cfg.MAX_ACTIVE_REPLICAS = original_max_active
            
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scale_to_caps_at_max_limit():
    """Test that scale_to respects MAX_ACTIVE_REPLICAS when using Gluetun."""
    print("\n🧪 Testing scale_to capping at MAX_ACTIVE_REPLICAS...")
    
    try:
        from app.services.autoscaler import scale_to
        from app.services.state import state
        from app.core.config import cfg
        from app.models.schemas import EngineState
        from datetime import datetime, timezone
        
        # Save original values
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_min_replicas = cfg.MIN_REPLICAS
        original_max_replicas = cfg.MAX_REPLICAS
        original_max_active = cfg.MAX_ACTIVE_REPLICAS
        
        try:
            # Set up test scenario: Using Gluetun
            cfg.GLUETUN_CONTAINER_NAME = "gluetun"
            cfg.MIN_REPLICAS = 5
            cfg.MAX_REPLICAS = 50
            cfg.MAX_ACTIVE_REPLICAS = 20
            
            # Clear state
            state.engines.clear()
            state.streams.clear()
            
            # Simulate 10 running containers
            mock_containers = []
            for i in range(10):
                container_id = f"test_engine_{i}"
                engine = EngineState(
                    container_id=container_id,
                    container_name=f"test-engine-{i}",
                    host="127.0.0.1",
                    port=8080 + i,
                    labels={},
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    streams=[]
                )
                state.engines[container_id] = engine
                
                mock_container = MagicMock()
                mock_container.id = container_id
                mock_container.status = "running"
                mock_containers.append(mock_container)
            
            print(f"   Created {len(state.engines)} engines in state")
            
            mock_docker_status = {
                'total_running': 10,
                'containers': mock_containers,
                'running_container_ids': {c.id for c in mock_containers},
                'container_details': {}
            }
            
            # Track how many times start_acestream is called
            start_count = [0]
            max_starts = [10]  # Should start max 10 to reach MAX_ACTIVE_REPLICAS=20
            
            def mock_start_acestream(*args, **kwargs):
                start_count[0] += 1
                if start_count[0] > max_starts[0]:
                    raise AssertionError(f"start_acestream called {start_count[0]} times, expected max {max_starts[0]}")
                raise RuntimeError("Gluetun health check failed")  # Simulate failure to stop the loop
            
            with patch('app.services.replica_validator.replica_validator') as mock_validator:
                mock_validator.get_docker_container_status.return_value = mock_docker_status
                
                # Patch start_acestream to track calls
                with patch('app.services.autoscaler.start_acestream', side_effect=mock_start_acestream):
                    # Try to scale to 25 (exceeds MAX_ACTIVE_REPLICAS)
                    scale_to(25)
                    
                    # Should have tried to start exactly 10 containers
                    assert start_count[0] == 10, f"start_acestream should be called 10 times (to reach MAX_ACTIVE_REPLICAS=20), was called {start_count[0]} times"
                    print(f"   ✅ scale_to correctly tried to start only {start_count[0]} containers (capped by MAX_ACTIVE_REPLICAS)")
            
            print("\n🎯 Test PASSED: scale_to respects MAX_ACTIVE_REPLICAS")
            return True
            
        finally:
            # Restore original values
            cfg.GLUETUN_CONTAINER_NAME = original_gluetun
            cfg.MIN_REPLICAS = original_min_replicas
            cfg.MAX_REPLICAS = original_max_replicas
            cfg.MAX_ACTIVE_REPLICAS = original_max_active
            
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🚀 Testing autoscaler MAX_ACTIVE_REPLICAS fixes...")
    print("=" * 70)
    
    test1 = test_ensure_minimum_at_max_limit()
    test2 = test_scale_to_caps_at_max_limit()
    
    print("\n" + "=" * 70)
    if test1 and test2:
        print("🎉 All tests PASSED!")
        sys.exit(0)
    else:
        print("❌ Some tests FAILED")
        sys.exit(1)
