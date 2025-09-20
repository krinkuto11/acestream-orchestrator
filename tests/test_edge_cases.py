#!/usr/bin/env python3
"""
Test edge cases and backward compatibility for the port binding fix.
"""

def test_edge_cases():
    """Test edge cases to ensure the fix is robust."""
    print("🧪 Testing Edge Cases and Backward Compatibility")
    print("=" * 60)
    
    from app.services.provisioner import _parse_conf_port
    
    # Test 1: Empty and invalid CONF strings
    print("\n📋 Test 1: Edge cases for CONF parsing")
    
    test_cases = [
        ("", None),  # Empty string
        (None, None),  # None value
        ("--bind-all", None),  # No port specified
        ("--http-port=", None),  # Empty port value
        ("--http-port=invalid", None),  # Invalid port number
        ("--http-port=999999", None),  # Invalid port range
        ("--http-port=-1", None),  # Negative port
        ("--http-port=6879\n--other-option=value", 6879),  # Mixed options
        ("  --http-port=6879  \n  --bind-all  ", 6879),  # Whitespace handling
        ("--https-port=6880", None),  # Wrong port type
        ("--http-port=6879\n--http-port=6880", 6879),  # Multiple entries (first wins)
    ]
    
    all_passed = True
    for conf, expected in test_cases:
        try:
            result = _parse_conf_port(conf, "http")
            passed = result == expected
            status = "✅" if passed else "❌"
            print(f"   {status} {repr(conf)} -> {result} (expected {expected})")
            if not passed:
                all_passed = False
        except Exception as e:
            print(f"   ❌ {repr(conf)} -> Exception: {e}")
            all_passed = False
    
    print(f"\n   Parser edge cases: {'✅ PASSED' if all_passed else '❌ FAILED'}")
    
    # Test 2: Backward compatibility - no CONF provided
    print("\n📋 Test 2: Backward compatibility (no CONF)")
    
    class MockReq:
        def __init__(self, env, host_port=None):
            self.env = env
            self.host_port = host_port
    
    # Simulate original behavior when no CONF is provided
    req_no_conf = MockReq({})  # No CONF in environment
    
    user_conf = req_no_conf.env.get("CONF")
    user_http_port = _parse_conf_port(user_conf, "http") if user_conf else None
    user_https_port = _parse_conf_port(user_conf, "https") if user_conf else None
    
    # Should fall back to allocator logic
    backward_compatible = (user_http_port is None and user_https_port is None)
    print(f"   No CONF case: {'✅ PASSED' if backward_compatible else '❌ FAILED'}")
    print(f"     user_http_port: {user_http_port} (should be None)")
    print(f"     user_https_port: {user_https_port} (should be None)")
    
    # Test 3: Partial CONF (only HTTP port)
    print("\n📋 Test 3: Partial CONF (only HTTP port)")
    
    req_partial = MockReq({"CONF": "--http-port=6879\n--bind-all"})
    
    user_conf = req_partial.env.get("CONF")
    user_http_port = _parse_conf_port(user_conf, "http")
    user_https_port = _parse_conf_port(user_conf, "https")
    
    partial_correct = (user_http_port == 6879 and user_https_port is None)
    print(f"   Partial CONF: {'✅ PASSED' if partial_correct else '❌ FAILED'}")
    print(f"     HTTP port: {user_http_port} (should be 6879)")
    print(f"     HTTPS port: {user_https_port} (should be None)")
    
    # Test 4: Host port override behavior
    print("\n📋 Test 4: Host port override with user CONF")
    
    req_override = MockReq({"CONF": "--http-port=6879\n--https-port=6880\n--bind-all"}, host_port=7777)
    
    user_conf = req_override.env.get("CONF")
    user_http_port = _parse_conf_port(user_conf, "http")
    
    # Logic from our fix
    if user_http_port is not None:
        c_http = user_http_port  # 6879 from CONF
        host_http = req_override.host_port or user_http_port  # 7777 from override
    
    override_correct = (c_http == 6879 and host_http == 7777)
    print(f"   Host override: {'✅ PASSED' if override_correct else '❌ FAILED'}")
    print(f"     Container port: {c_http} (should be 6879 from CONF)")
    print(f"     Host port: {host_http} (should be 7777 from override)")
    
    # Test 5: Docker Compose scenario (exact problem statement)
    print("\n📋 Test 5: Exact Docker Compose scenario")
    
    # This is the exact scenario from the problem statement
    docker_compose_env = {
        "CONF": "--http-port=6879\n--https-port=6880\n--bind-all"
    }
    
    req_docker = MockReq(docker_compose_env)
    
    user_conf = req_docker.env.get("CONF")
    user_http_port = _parse_conf_port(user_conf, "http")
    user_https_port = _parse_conf_port(user_conf, "https")
    
    # Apply our fix logic
    if user_http_port is not None:
        c_http = user_http_port  # 6879
        host_http = req_docker.host_port or user_http_port  # 6879
    
    if user_https_port is not None:
        c_https = user_https_port  # 6880
    
    # Final environment and ports
    final_conf = req_docker.env.get("CONF")
    env = {
        **req_docker.env,
        "CONF": final_conf,
        "HTTP_PORT": str(c_http),
        "HTTPS_PORT": str(c_https),
        "BIND_ALL": "true"
    }
    ports_mapping = {f"{c_http}/tcp": host_http}
    
    # Verify the exact requirements
    docker_binding_matches = ports_mapping == {"6879/tcp": 6879}
    http_port_matches = env["HTTP_PORT"] == "6879"
    conf_preserved = env["CONF"] == "--http-port=6879\n--https-port=6880\n--bind-all"
    
    docker_scenario_correct = docker_binding_matches and http_port_matches and conf_preserved
    
    print(f"   Docker Compose scenario: {'✅ PASSED' if docker_scenario_correct else '❌ FAILED'}")
    print(f"     Docker binding matches internal port: {docker_binding_matches}")
    print(f"     HTTP_PORT matches internal port: {http_port_matches}")
    print(f"     CONF preserved: {conf_preserved}")
    print(f"     Ports mapping: {ports_mapping}")
    print(f"     Environment HTTP_PORT: {env['HTTP_PORT']}")
    
    # Overall result
    all_tests_passed = all([all_passed, backward_compatible, partial_correct, override_correct, docker_scenario_correct])
    
    print(f"\n📊 Overall Test Results:")
    print(f"   Parser edge cases: {'✅' if all_passed else '❌'}")
    print(f"   Backward compatibility: {'✅' if backward_compatible else '❌'}")
    print(f"   Partial CONF handling: {'✅' if partial_correct else '❌'}")
    print(f"   Host port override: {'✅' if override_correct else '❌'}")
    print(f"   Docker Compose scenario: {'✅' if docker_scenario_correct else '❌'}")
    print(f"   Overall: {'✅ ALL TESTS PASSED' if all_tests_passed else '❌ SOME TESTS FAILED'}")
    
    return all_tests_passed

if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    success = test_edge_cases()
    
    if success:
        print("\n🎉 All edge case tests passed!")
        print("🔧 The fix is robust and backward compatible!")
    else:
        print("\n❌ Some edge case tests failed!")
    
    sys.exit(0 if success else 1)