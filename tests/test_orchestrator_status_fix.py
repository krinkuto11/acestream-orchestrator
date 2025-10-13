#!/usr/bin/env python3
"""
Test to verify the fix for UnboundLocalError in get_orchestrator_status endpoint.
This test ensures that the datetime module is properly accessible throughout the function.
"""

import sys
import os

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_datetime_not_shadowed():
    """Test that datetime import is not shadowed in get_orchestrator_status function."""
    print("\n=== Testing datetime import fix ===\n")
    
    try:
        from app.main import app
        import inspect
        
        # Get the source code of the function
        from app.main import get_orchestrator_status
        source = inspect.getsource(get_orchestrator_status)
        
        # Check that there's no local datetime import
        lines = source.split('\n')
        for i, line in enumerate(lines):
            if 'from datetime import' in line and 'datetime' in line:
                print(f"❌ Found local datetime import at line {i}: {line.strip()}")
                print("   This will shadow the module-level import and cause UnboundLocalError")
                return False
        
        print("✓ No local datetime imports found in get_orchestrator_status")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_endpoint_structure():
    """Test that the endpoint can be called without UnboundLocalError."""
    print("\n=== Testing endpoint structure ===\n")
    
    try:
        from app.main import app, get_orchestrator_status
        from fastapi.testclient import TestClient
        from unittest.mock import Mock, patch
        
        # Mock the dependencies
        mock_state = Mock()
        mock_state.list_engines.return_value = []
        mock_state.list_streams.return_value = []
        
        mock_validator = Mock()
        mock_validator.get_docker_container_status.return_value = {
            'total_running': 0,
            'healthy': 0,
            'unhealthy': 0
        }
        
        mock_vpn_status = {
            'enabled': False,
            'connected': False,
            'health': 'unknown',
            'container': None,
            'forwarded_port': None
        }
        
        mock_health = {
            'healthy_engines': 0,
            'unhealthy_engines': 0
        }
        
        mock_cb_manager = Mock()
        mock_cb_manager.get_status.return_value = {
            'general': {
                'state': 'closed',
                'last_failure_time': None
            }
        }
        
        # Patch the dependencies
        with patch('app.main.state', mock_state), \
             patch('app.main.get_vpn_status', return_value=mock_vpn_status), \
             patch('app.main.health_manager.get_health_summary', return_value=mock_health):
            
            # Import and patch at module level
            import app.services.replica_validator
            import app.services.circuit_breaker
            
            with patch.object(app.services.replica_validator, 'replica_validator', mock_validator), \
                 patch.object(app.services.circuit_breaker, 'circuit_breaker_manager', mock_cb_manager):
                
                # Call the function
                result = get_orchestrator_status()
                
                # Verify the result structure
                required_keys = ['status', 'engines', 'streams', 'capacity', 'vpn', 'provisioning', 'config', 'timestamp']
                for key in required_keys:
                    if key not in result:
                        print(f"❌ Missing key in response: {key}")
                        return False
                
                # Check that timestamp is present and valid
                if 'timestamp' not in result:
                    print("❌ Missing timestamp in response")
                    return False
                
                timestamp = result['timestamp']
                if not isinstance(timestamp, str):
                    print(f"❌ Timestamp is not a string: {type(timestamp)}")
                    return False
                
                # Check that timestamp has timezone info
                if not (timestamp.endswith('Z') or '+' in timestamp or timestamp.endswith('+00:00')):
                    print(f"❌ Timestamp missing timezone info: {timestamp}")
                    return False
                
                print(f"✓ Endpoint returns valid response with timestamp: {timestamp}")
                print("✓ No UnboundLocalError occurred")
                return True
                
    except UnboundLocalError as e:
        print(f"❌ UnboundLocalError still occurs: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_circuit_breaker_with_last_failure():
    """Test that datetime works correctly when circuit breaker has a last_failure_time."""
    print("\n=== Testing datetime usage in circuit breaker scenario ===\n")
    
    try:
        from app.main import get_orchestrator_status
        from unittest.mock import Mock, patch
        from datetime import datetime, timezone
        
        # Create a timestamp in the past
        past_time = datetime.now(timezone.utc)
        past_time_str = past_time.isoformat()
        
        mock_state = Mock()
        mock_state.list_engines.return_value = []
        mock_state.list_streams.return_value = []
        
        mock_validator = Mock()
        mock_validator.get_docker_container_status.return_value = {
            'total_running': 5,
            'healthy': 5,
            'unhealthy': 0
        }
        
        mock_vpn_status = {
            'enabled': False,
            'connected': False,
            'health': 'unknown',
            'container': None,
            'forwarded_port': None
        }
        
        mock_health = {
            'healthy_engines': 5,
            'unhealthy_engines': 0
        }
        
        # Circuit breaker is open with a last_failure_time
        mock_cb_manager = Mock()
        mock_cb_manager.get_status.return_value = {
            'general': {
                'state': 'open',
                'last_failure_time': past_time_str,
                'recovery_timeout': 300
            }
        }
        
        # Patch the dependencies
        with patch('app.main.state', mock_state), \
             patch('app.main.get_vpn_status', return_value=mock_vpn_status), \
             patch('app.main.health_manager.get_health_summary', return_value=mock_health):
            
            import app.services.replica_validator
            import app.services.circuit_breaker
            
            with patch.object(app.services.replica_validator, 'replica_validator', mock_validator), \
                 patch.object(app.services.circuit_breaker, 'circuit_breaker_manager', mock_cb_manager):
                
                # Call the function - this should use datetime without UnboundLocalError
                result = get_orchestrator_status()
                
                # Verify the provisioning section
                if 'provisioning' not in result:
                    print("❌ Missing provisioning in response")
                    return False
                
                prov = result['provisioning']
                if 'blocked_reason_details' not in prov or prov['blocked_reason_details'] is None:
                    print("❌ Missing blocked_reason_details")
                    return False
                
                details = prov['blocked_reason_details']
                if 'recovery_eta_seconds' not in details:
                    print("❌ Missing recovery_eta_seconds")
                    return False
                
                recovery_eta = details['recovery_eta_seconds']
                if not isinstance(recovery_eta, int):
                    print(f"❌ recovery_eta_seconds is not an integer: {type(recovery_eta)}")
                    return False
                
                print(f"✓ Circuit breaker scenario processed correctly")
                print(f"✓ recovery_eta_seconds calculated: {recovery_eta}s")
                print("✓ No UnboundLocalError when processing last_failure_time")
                return True
                
    except UnboundLocalError as e:
        print(f"❌ UnboundLocalError occurred in circuit breaker scenario: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("=" * 70)
    print("Orchestrator Status UnboundLocalError Fix Test")
    print("=" * 70)
    
    tests = [
        ("No datetime shadowing", test_datetime_not_shadowed),
        ("Endpoint structure", test_endpoint_structure),
        ("Circuit breaker with last_failure", test_circuit_breaker_with_last_failure),
    ]
    
    results = {}
    for name, test_func in tests:
        try:
            results[name] = test_func()
        except Exception as e:
            print(f"\n❌ {name} failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False
    
    # Print summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    
    for name, result in results.items():
        status = "✓ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print(f"\n{passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed!")
        print("The UnboundLocalError fix is working correctly.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
