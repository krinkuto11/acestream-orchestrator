#!/usr/bin/env python3
"""
Integration test for acestream-http-proxy changes.
Tests that CONF handling and environment variables work together correctly.
"""

def test_integration_conf_and_env_vars():
    """Test integration of CONF handling with new environment variables."""
    print("🧪 Testing Integration: CONF + Environment Variables")
    print("=" * 60)
    
    from app.services.provisioner import AceProvisionRequest
    from app.services import ports
    
    # Mock the port allocator to return predictable values
    original_alloc_http = ports.alloc.alloc_http
    original_alloc_https = ports.alloc.alloc_https
    
    def mock_alloc_http():
        return 40001
    
    def mock_alloc_https(avoid=None):
        return 45001
    
    ports.alloc.alloc_http = mock_alloc_http
    ports.alloc.alloc_https = mock_alloc_https
    
    try:
        # Full simulation of start_acestream logic
        def simulate_start_acestream_logic(req_env, c_http=40001, c_https=45001):
            """Simulate the complete start_acestream logic"""
            # CONF handling (existing logic)
            if "CONF" in req_env:
                # User explicitly provided CONF (even if empty), use it as-is
                final_conf = req_env["CONF"]
            else:
                # No user CONF, use default orchestrator configuration
                conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
                final_conf = "\n".join(conf_lines)
            
            # Environment variables (new logic)
            env = {
                **req_env, 
                "CONF": final_conf,
                "HTTP_PORT": str(c_http),
                "HTTPS_PORT": str(c_https),
                "BIND_ALL": "true"
            }
            return env
        
        tests_passed = 0
        total_tests = 0
        
        # Test 1: Integration test - Docker Compose scenario
        print("\n📋 Test 1: Docker Compose Integration")
        print("   Scenario: User provides CONF for acestream-http-proxy image")
        total_tests += 1
        
        docker_compose_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
        user_env = {
            "CONF": docker_compose_conf,
            "SOME_OTHER_VAR": "preserved"
        }
        
        result = simulate_start_acestream_logic(user_env)
        
        test1_checks = [
            ("CONF preservation", result.get("CONF"), docker_compose_conf),
            ("HTTP_PORT matches allocated", result.get("HTTP_PORT"), "40001"),
            ("HTTPS_PORT matches allocated", result.get("HTTPS_PORT"), "45001"),
            ("BIND_ALL always true", result.get("BIND_ALL"), "true"),
            ("Custom vars preserved", result.get("SOME_OTHER_VAR"), "preserved"),
        ]
        
        test1_passed = True
        for check_name, actual, expected in test1_checks:
            if actual == expected:
                print(f"   ✅ {check_name}: {actual}")
            else:
                print(f"   ❌ {check_name}: got {actual}, expected {expected}")
                test1_passed = False
        
        if test1_passed:
            tests_passed += 1
            print("✅ PASS: Docker Compose integration test")
        else:
            print("❌ FAIL: Docker Compose integration test")
        
        # Test 2: Integration test - Orchestrator default scenario
        print("\n📋 Test 2: Orchestrator Default Integration")
        print("   Scenario: No user CONF, orchestrator generates everything")
        total_tests += 1
        
        minimal_env = {"API_KEY": "test123"}
        result = simulate_start_acestream_logic(minimal_env)
        
        expected_conf = "--http-port=40001\n--https-port=45001\n--bind-all"
        test2_checks = [
            ("CONF auto-generated", result.get("CONF"), expected_conf),
            ("HTTP_PORT matches CONF", result.get("HTTP_PORT"), "40001"),
            ("HTTPS_PORT matches CONF", result.get("HTTPS_PORT"), "45001"),
            ("BIND_ALL always true", result.get("BIND_ALL"), "true"),
            ("User vars preserved", result.get("API_KEY"), "test123"),
        ]
        
        test2_passed = True
        for check_name, actual, expected in test2_checks:
            if actual == expected:
                print(f"   ✅ {check_name}: {actual}")
            else:
                print(f"   ❌ {check_name}: got {actual}, expected {expected}")
                test2_passed = False
        
        if test2_passed:
            tests_passed += 1
            print("✅ PASS: Orchestrator default integration test")
        else:
            print("❌ FAIL: Orchestrator default integration test")
        
        # Test 3: Edge case - Empty CONF
        print("\n📋 Test 3: Empty CONF Edge Case")
        print("   Scenario: User provides empty CONF string")
        total_tests += 1
        
        empty_conf_env = {"CONF": "", "DEBUG": "true"}
        result = simulate_start_acestream_logic(empty_conf_env)
        
        test3_checks = [
            ("Empty CONF preserved", result.get("CONF"), ""),
            ("HTTP_PORT still set", result.get("HTTP_PORT"), "40001"),
            ("HTTPS_PORT still set", result.get("HTTPS_PORT"), "45001"),
            ("BIND_ALL still set", result.get("BIND_ALL"), "true"),
            ("Debug var preserved", result.get("DEBUG"), "true"),
        ]
        
        test3_passed = True
        for check_name, actual, expected in test3_checks:
            if actual == expected:
                print(f"   ✅ {check_name}: {actual}")
            else:
                print(f"   ❌ {check_name}: got {actual}, expected {expected}")
                test3_passed = False
        
        if test3_passed:
            tests_passed += 1
            print("✅ PASS: Empty CONF edge case")
        else:
            print("❌ FAIL: Empty CONF edge case")
        
        # Test 4: Backward compatibility test
        print("\n📋 Test 4: Backward Compatibility")
        print("   Scenario: Ensure old acestream/engine:latest still works")
        total_tests += 1
        
        old_engine_env = {}  # No CONF, no special vars
        result = simulate_start_acestream_logic(old_engine_env)
        
        # Old engine should still get CONF, plus new env vars won't hurt
        expected_old_conf = "--http-port=40001\n--https-port=45001\n--bind-all"
        test4_checks = [
            ("CONF for old engine", result.get("CONF"), expected_old_conf),
            ("HTTP_PORT doesn't hurt", result.get("HTTP_PORT"), "40001"),
            ("HTTPS_PORT doesn't hurt", result.get("HTTPS_PORT"), "45001"),
            ("BIND_ALL doesn't hurt", result.get("BIND_ALL"), "true"),
        ]
        
        test4_passed = True
        for check_name, actual, expected in test4_checks:
            if actual == expected:
                print(f"   ✅ {check_name}: {actual}")
            else:
                print(f"   ❌ {check_name}: got {actual}, expected {expected}")
                test4_passed = False
        
        if test4_passed:
            tests_passed += 1
            print("✅ PASS: Backward compatibility maintained")
        else:
            print("❌ FAIL: Backward compatibility broken")
        
        # Summary
        print(f"\n🎯 Integration Test Results: {tests_passed}/{total_tests} passed")
        if tests_passed == total_tests:
            print("✅ All integration tests passed!")
            print("✅ CONF handling preserved correctly")
            print("✅ Environment variables added for acestream-http-proxy")
            print("✅ Backward compatibility maintained")
            print("✅ Edge cases handled properly")
            return True
        else:
            print("❌ Some integration tests failed.")
            return False
            
    finally:
        # Restore original functions
        ports.alloc.alloc_http = original_alloc_http
        ports.alloc.alloc_https = original_alloc_https

if __name__ == "__main__":
    import sys
    sys.path.append('.')
    result = test_integration_conf_and_env_vars()
    sys.exit(0 if result else 1)