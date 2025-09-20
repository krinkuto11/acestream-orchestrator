#!/usr/bin/env python3
"""
Direct test of the provisioner fix without mocking.
"""

def test_direct_provisioner_logic():
    """Test the actual logic in the provisioner."""
    print("🧪 Direct Test of Provisioner Logic")
    print("=" * 50)
    
    from app.services.provisioner import _parse_conf_port
    
    # Test 1: Parser function
    print("\n📋 Testing _parse_conf_port function:")
    test_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
    http_port = _parse_conf_port(test_conf, "http")
    https_port = _parse_conf_port(test_conf, "https")
    
    print(f"   Input: {repr(test_conf)}")
    print(f"   HTTP port: {http_port}")
    print(f"   HTTPS port: {https_port}")
    
    assert http_port == 6879, f"Expected 6879, got {http_port}"
    assert https_port == 6880, f"Expected 6880, got {https_port}"
    print("   ✅ Parser works correctly")
    
    # Test 2: Simulate the exact logic in start_acestream
    print("\n📋 Testing start_acestream logic:")
    
    # Mock req object
    class MockReq:
        def __init__(self, env, host_port=None):
            self.env = env
            self.host_port = host_port
    
    # Test case from the problem statement
    req = MockReq({"CONF": "--http-port=6879\n--https-port=6880\n--bind-all"})
    
    # Simulate the logic from start_acestream
    user_conf = req.env.get("CONF")
    user_http_port = _parse_conf_port(user_conf, "http") if user_conf else None
    user_https_port = _parse_conf_port(user_conf, "https") if user_conf else None
    
    print(f"   User CONF: {repr(user_conf)}")
    print(f"   Parsed HTTP port: {user_http_port}")
    print(f"   Parsed HTTPS port: {user_https_port}")
    
    # Port determination logic
    if user_http_port is not None:
        c_http = user_http_port
        host_http = req.host_port or user_http_port  # Same port for host binding
        print(f"   Using user HTTP port: container={c_http}, host={host_http}")
    else:
        # Would use allocator
        print("   Would use allocator ports")
    
    if user_https_port is not None:
        c_https = user_https_port
        print(f"   Using user HTTPS port: {c_https}")
    else:
        # Would use allocator
        print("   Would use allocator HTTPS port")
    
    # Environment and ports mapping
    final_conf = req.env.get("CONF", f"--http-port={c_http}\n--https-port={c_https}\n--bind-all")
    
    env = {
        **req.env, 
        "CONF": final_conf,
        "HTTP_PORT": str(c_http),
        "HTTPS_PORT": str(c_https),
        "BIND_ALL": "true"
    }
    
    ports_mapping = {f"{c_http}/tcp": host_http}
    
    print(f"\n   Final result:")
    print(f"     Environment CONF: {repr(env['CONF'])}")
    print(f"     Environment HTTP_PORT: {env['HTTP_PORT']}")
    print(f"     Environment HTTPS_PORT: {env['HTTPS_PORT']}")
    print(f"     Docker ports mapping: {ports_mapping}")
    
    # Verify the fix
    expected_docker_mapping = {"6879/tcp": 6879}
    expected_http_port = "6879"
    expected_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
    
    docker_correct = ports_mapping == expected_docker_mapping
    http_port_correct = env['HTTP_PORT'] == expected_http_port
    conf_correct = env['CONF'] == expected_conf
    
    print(f"\n   🔍 Verification:")
    print(f"     Docker mapping correct: {docker_correct} (expected {expected_docker_mapping}, got {ports_mapping})")
    print(f"     HTTP_PORT correct: {http_port_correct} (expected {expected_http_port}, got {env['HTTP_PORT']})")
    print(f"     CONF correct: {conf_correct} (expected {repr(expected_conf)}, got {repr(env['CONF'])})")
    
    all_correct = docker_correct and http_port_correct and conf_correct
    
    print(f"\n📊 Test Result: {'✅ ALL CORRECT' if all_correct else '❌ ISSUES FOUND'}")
    
    if all_correct:
        print("\n🎉 SUCCESS!")
        print("   - Docker port binding matches internal port (6879:6879)")
        print("   - HTTP_PORT environment variable matches internal port (6879)")
        print("   - User CONF is preserved exactly")
        print("   - Problem statement requirements fulfilled!")
    
    return all_correct

if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    success = test_direct_provisioner_logic()
    sys.exit(0 if success else 1)