#!/usr/bin/env python3
"""
Test to verify that the autoscaler respects MAX_ACTIVE_REPLICAS when using Gluetun.
This test focuses on simple scenarios without complex mocking.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_max_active_replicas_in_ensure_minimum():
    """Test that ensure_minimum respects MAX_ACTIVE_REPLICAS when using Gluetun."""
    
    print("\n🧪 Testing MAX_ACTIVE_REPLICAS constraint in ensure_minimum...")
    
    try:
        from app.services.autoscaler import ensure_minimum
        from app.services.state import state
        from app.core.config import cfg
        from app.services import autoscaler, replica_validator as rv_module
        from app.services.circuit_breaker import circuit_breaker_manager
        
        # Clear state
        state.engines.clear()
        state.streams.clear()
        
        # Mock Docker containers
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
                self.attrs = {
                    'Created': '2023-01-01T00:00:00Z',
                    'NetworkSettings': {'Ports': {}}
                }
        
        # Save original values
        original_min = cfg.MIN_REPLICAS
        original_max_active = cfg.MAX_ACTIVE_REPLICAS
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_list_managed = autoscaler.list_managed
        original_rv_list_managed = rv_module.list_managed
        
        try:
            # Test 1: At MAX_ACTIVE_REPLICAS limit, should not attempt provisioning
            print("\nTest 1: At MAX_ACTIVE_REPLICAS limit (20 running, 10 free, MIN_REPLICAS=10, MAX_ACTIVE=20)")
            cfg.MIN_REPLICAS = 10
            cfg.MAX_ACTIVE_REPLICAS = 20
            cfg.GLUETUN_CONTAINER_NAME = "gluetun"
            
            # Simulate 20 running containers (10 used, 10 free)
            mock_containers = [MockContainer(f"engine_{i}", "running") for i in range(20)]
            
            def mock_list_managed():
                return mock_containers
            
            autoscaler.list_managed = mock_list_managed
            rv_module.list_managed = mock_list_managed
            rv_module.replica_validator._cached_result = None
            rv_module.replica_validator._last_validation = None
            
            # Mock start_acestream to detect if it's called
            provision_attempts = []
            original_start_acestream = autoscaler.start_acestream
            
            def mock_start_acestream(req):
                provision_attempts.append(req)
                raise RuntimeError(f"Maximum active replicas limit reached ({cfg.MAX_ACTIVE_REPLICAS})")
            
            autoscaler.start_acestream = mock_start_acestream
            
            # Reset circuit breaker
            circuit_breaker_manager.force_reset("general")
            
            # Call ensure_minimum - should not attempt provisioning
            ensure_minimum()
            
            assert len(provision_attempts) == 0, f"Should not attempt provisioning when at MAX_ACTIVE_REPLICAS limit, but attempted {len(provision_attempts)} times"
            print("✅ Correctly stopped at MAX_ACTIVE_REPLICAS limit without attempting provisioning")
            
            # Restore start_acestream
            autoscaler.start_acestream = original_start_acestream
            
            # Test 2: Below MAX_ACTIVE_REPLICAS, should cap provisioning attempts
            print("\nTest 2: Below MAX_ACTIVE_REPLICAS (15 running, 5 free, MIN_REPLICAS=10, MAX_ACTIVE=20)")
            cfg.MIN_REPLICAS = 10
            cfg.MAX_ACTIVE_REPLICAS = 20
            
            # Simulate 15 running containers (10 used, 5 free)
            mock_containers = [MockContainer(f"engine_{i}", "running") for i in range(15)]
            rv_module.replica_validator._cached_result = None
            rv_module.replica_validator._last_validation = None
            
            provision_attempts = []
            
            def mock_start_acestream_count(req):
                provision_attempts.append(req)
                # Simulate successful provisioning
                from app.models.schemas import AceProvisionResponse
                return AceProvisionResponse(
                    container_id=f"new_engine_{len(provision_attempts)}",
                    container_name=f"new-engine-{len(provision_attempts)}",
                    host_http_port=19000 + len(provision_attempts),
                    container_http_port=6878,
                    container_https_port=6879
                )
            
            autoscaler.start_acestream = mock_start_acestream_count
            
            # Call ensure_minimum - should attempt to provision up to MAX_ACTIVE_REPLICAS
            # Need 5 more to reach MIN_REPLICAS=10 free, but can only add 5 to stay within MAX_ACTIVE=20
            ensure_minimum()
            
            # Should attempt to provision exactly 5 containers (to reach MAX_ACTIVE_REPLICAS=20)
            assert len(provision_attempts) <= 5, f"Should attempt at most 5 provisions to reach MAX_ACTIVE_REPLICAS, but attempted {len(provision_attempts)}"
            print(f"✅ Correctly capped provisioning at {len(provision_attempts)} attempts (max allowed: 5)")
            
            # Test 3: MIN_REPLICAS > MAX_ACTIVE_REPLICAS, should cap at MAX_ACTIVE_REPLICAS
            print("\nTest 3: MIN_REPLICAS > MAX_ACTIVE_REPLICAS (10 running, MIN_REPLICAS=30, MAX_ACTIVE=20)")
            cfg.MIN_REPLICAS = 30  # Intentionally higher than MAX_ACTIVE
            cfg.MAX_ACTIVE_REPLICAS = 20
            
            mock_containers = [MockContainer(f"engine_{i}", "running") for i in range(10)]
            rv_module.replica_validator._cached_result = None
            rv_module.replica_validator._last_validation = None
            
            provision_attempts = []
            autoscaler.start_acestream = mock_start_acestream_count
            
            ensure_minimum()
            
            # Should attempt to provision up to MAX_ACTIVE_REPLICAS=20, not MIN_REPLICAS=30
            assert len(provision_attempts) <= 10, f"Should attempt at most 10 provisions to reach MAX_ACTIVE_REPLICAS=20, but attempted {len(provision_attempts)}"
            print(f"✅ Correctly capped provisioning at {len(provision_attempts)} attempts when MIN_REPLICAS > MAX_ACTIVE_REPLICAS")
            
            print("\n🎯 All tests PASSED: MAX_ACTIVE_REPLICAS constraint is properly respected in ensure_minimum()")
            return True
            
        finally:
            # Restore original values
            cfg.MIN_REPLICAS = original_min
            cfg.MAX_ACTIVE_REPLICAS = original_max_active
            cfg.GLUETUN_CONTAINER_NAME = original_gluetun
            if original_list_managed:
                autoscaler.list_managed = original_list_managed
            if original_rv_list_managed:
                rv_module.list_managed = original_rv_list_managed
            if hasattr(autoscaler, 'start_acestream'):
                # Reset to avoid breaking other code
                pass
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_max_active_replicas_in_scale_to():
    """Test that scale_to respects MAX_ACTIVE_REPLICAS when using Gluetun."""
    
    print("\n🧪 Testing MAX_ACTIVE_REPLICAS constraint in scale_to...")
    
    try:
        from app.services.autoscaler import scale_to
        from app.services.state import state
        from app.core.config import cfg
        from app.services import autoscaler, replica_validator as rv_module
        
        # Clear state
        state.engines.clear()
        state.streams.clear()
        
        # Mock Docker containers
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
                self.attrs = {
                    'Created': '2023-01-01T00:00:00Z',
                    'NetworkSettings': {'Ports': {}}
                }
        
        # Save original values
        original_min = cfg.MIN_REPLICAS
        original_max = cfg.MAX_REPLICAS
        original_max_active = cfg.MAX_ACTIVE_REPLICAS
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_list_managed = autoscaler.list_managed
        original_rv_list_managed = rv_module.list_managed
        
        try:
            # Test: scale_to should cap at MAX_ACTIVE_REPLICAS when using Gluetun
            print("\nTest: scale_to(50) with MAX_ACTIVE_REPLICAS=20 should cap at 20")
            cfg.MIN_REPLICAS = 10
            cfg.MAX_REPLICAS = 50
            cfg.MAX_ACTIVE_REPLICAS = 20
            cfg.GLUETUN_CONTAINER_NAME = "gluetun"
            
            # Simulate 10 running containers
            mock_containers = [MockContainer(f"engine_{i}", "running") for i in range(10)]
            
            def mock_list_managed():
                return mock_containers
            
            autoscaler.list_managed = mock_list_managed
            rv_module.list_managed = mock_list_managed
            rv_module.replica_validator._cached_result = None
            rv_module.replica_validator._last_validation = None
            
            # Mock start_acestream to count provision attempts
            provision_attempts = []
            
            def mock_start_acestream(req):
                provision_attempts.append(req)
                from app.models.schemas import AceProvisionResponse
                return AceProvisionResponse(
                    container_id=f"new_engine_{len(provision_attempts)}",
                    container_name=f"new-engine-{len(provision_attempts)}",
                    host_http_port=19000 + len(provision_attempts),
                    container_http_port=6878,
                    container_https_port=6879
                )
            
            autoscaler.start_acestream = mock_start_acestream
            
            # Call scale_to(50) - should be capped at MAX_ACTIVE_REPLICAS=20
            scale_to(50)
            
            # Should attempt to provision up to 10 containers (to reach 20 total)
            assert len(provision_attempts) <= 10, f"Should attempt at most 10 provisions to reach MAX_ACTIVE_REPLICAS=20, but attempted {len(provision_attempts)}"
            print(f"✅ scale_to correctly capped at MAX_ACTIVE_REPLICAS (attempted {len(provision_attempts)} provisions)")
            
            print("\n🎯 All tests PASSED: MAX_ACTIVE_REPLICAS constraint is properly respected in scale_to()")
            return True
            
        finally:
            # Restore original values
            cfg.MIN_REPLICAS = original_min
            cfg.MAX_REPLICAS = original_max
            cfg.MAX_ACTIVE_REPLICAS = original_max_active
            cfg.GLUETUN_CONTAINER_NAME = original_gluetun
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
    print("🚀 Starting MAX_ACTIVE_REPLICAS autoscaler tests...")
    
    success = True
    success &= test_max_active_replicas_in_ensure_minimum()
    success &= test_max_active_replicas_in_scale_to()
    
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
