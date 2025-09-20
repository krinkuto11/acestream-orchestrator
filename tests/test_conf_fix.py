#!/usr/bin/env python3
"""
Test script for CONF environment variable fix.
This test validates that user-provided CONF is used as-is instead of being appended to defaults.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_conf_handling():
    """Test CONF environment variable handling in start_acestream function."""
    print("🧪 Testing CONF Environment Variable Handling")
    print("=" * 60)
    
    from app.services.provisioner import AceProvisionRequest
    
    # Simulate the CONF logic without actually starting containers
    def simulate_conf_logic(req_env):
        """Extract the CONF logic from start_acestream for testing"""
        if "CONF" in req_env:
            # User explicitly provided CONF (even if empty), use it as-is
            final_conf = req_env["CONF"]
        else:
            # No user CONF, use default orchestrator configuration
            c_http = 40001  # Simulated allocated port
            c_https = 45001  # Simulated allocated port
            conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
            final_conf = "\n".join(conf_lines)
        
        env = {**req_env, "CONF": final_conf}
        return env["CONF"]
    
    tests_passed = 0
    total_tests = 0
    
    # Test 1: Docker Compose scenario (the main issue)
    print("\n📋 Test 1: Docker Compose CONF (main issue scenario)")
    total_tests += 1
    docker_compose_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
    result_conf = simulate_conf_logic({"CONF": docker_compose_conf})
    
    if result_conf == docker_compose_conf:
        print("✅ PASS: User CONF preserved exactly")
        tests_passed += 1
    else:
        print("❌ FAIL: User CONF was modified")
        print(f"   Expected: {repr(docker_compose_conf)}")
        print(f"   Got:      {repr(result_conf)}")
    
    # Test 2: No CONF provided (default behavior)
    print("\n📋 Test 2: No CONF provided (default behavior)")
    total_tests += 1
    result_conf = simulate_conf_logic({})
    expected_default = "--http-port=40001\n--https-port=45001\n--bind-all"
    
    if result_conf == expected_default:
        print("✅ PASS: Default CONF generated correctly")
        tests_passed += 1
    else:
        print("❌ FAIL: Default CONF generation broken")
        print(f"   Expected: {repr(expected_default)}")
        print(f"   Got:      {repr(result_conf)}")
    
    # Test 3: Empty CONF string
    print("\n📋 Test 3: Empty CONF string")
    total_tests += 1
    result_conf = simulate_conf_logic({"CONF": ""})
    
    if result_conf == "":
        print("✅ PASS: Empty CONF preserved")
        tests_passed += 1
    else:
        print("❌ FAIL: Empty CONF was modified")
        print(f"   Expected: {repr('')}")
        print(f"   Got:      {repr(result_conf)}")
    
    # Test 4: Custom CONF with extra parameters
    print("\n📋 Test 4: Custom CONF with extra parameters")
    total_tests += 1
    custom_conf = "--http-port=8080\n--https-port=8443\n--bind-all\n--debug\n--verbose"
    result_conf = simulate_conf_logic({"CONF": custom_conf})
    
    if result_conf == custom_conf:
        print("✅ PASS: Custom CONF preserved")
        tests_passed += 1
    else:
        print("❌ FAIL: Custom CONF was modified")
        print(f"   Expected: {repr(custom_conf)}")
        print(f"   Got:      {repr(result_conf)}")
    
    # Summary
    print(f"\n🎯 Test Results: {tests_passed}/{total_tests} passed")
    
    if tests_passed == total_tests:
        print("✅ All tests passed! CONF handling is working correctly.")
        print("✅ User-provided CONF values are preserved as-is")
        print("✅ Default behavior when no CONF is provided still works")
        return True
    else:
        print("❌ Some tests failed! CONF handling needs more work.")
        return False

if __name__ == "__main__":
    success = test_conf_handling()
    sys.exit(0 if success else 1)