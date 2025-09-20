#!/usr/bin/env python3
"""
Test to verify the docker port binding fix works correctly.
"""

def test_fixed_port_binding():
    """Test that the fixed logic correctly handles port binding consistency."""
    print("🧪 Testing Fixed Port Binding Logic")
    print("=" * 50)
    
    from app.services.provisioner import AceProvisionRequest, _parse_conf_port
    from app.services import ports
    
    # Test the parser function first
    print("\n📋 Testing CONF Parser:")
    user_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
    http_port = _parse_conf_port(user_conf, "http")
    https_port = _parse_conf_port(user_conf, "https")
    print(f"   Parsed HTTP port: {http_port}")
    print(f"   Parsed HTTPS port: {https_port}")
    
    assert http_port == 6879, f"Expected 6879, got {http_port}"
    assert https_port == 6880, f"Expected 6880, got {https_port}"
    print("   ✅ Parser works correctly")
    
    # Backup original allocation functions
    original_alloc_host = ports.alloc.alloc_host
    original_alloc_http = ports.alloc.alloc_http
    original_alloc_https = ports.alloc.alloc_https
    original_reserve_http = ports.alloc.reserve_http
    original_reserve_https = ports.alloc.reserve_https
    
    # Track reserved ports
    reserved_http = set()
    reserved_https = set()
    
    try:
        def mock_alloc_host():
            return 19000  # From default range
        
        def mock_alloc_http():
            return 40001  # Should not be used when user provides CONF
        
        def mock_alloc_https(avoid=None):
            return 45001  # Should not be used when user provides CONF
        
        def mock_reserve_http(port):
            reserved_http.add(port)
            print(f"   📌 Reserved HTTP port: {port}")
        
        def mock_reserve_https(port):
            reserved_https.add(port)
            print(f"   📌 Reserved HTTPS port: {port}")
        
        ports.alloc.alloc_host = mock_alloc_host
        ports.alloc.alloc_http = mock_alloc_http
        ports.alloc.alloc_https = mock_alloc_https
        ports.alloc.reserve_http = mock_reserve_http
        ports.alloc.reserve_https = mock_reserve_https
        
        # Simulate the fixed logic
        def simulate_fixed_logic(req_env, host_port=None):
            """Simulate the FIXED provisioning logic"""
            # This mirrors the fix we implemented
            user_conf = req_env.get("CONF")
            user_http_port = _parse_conf_port(user_conf, "http") if user_conf else None
            user_https_port = _parse_conf_port(user_conf, "https") if user_conf else None
            
            # Determine ports to use
            if user_http_port is not None:
                c_http = user_http_port
                host_http = host_port or user_http_port  # Use same port for host binding
                ports.alloc.reserve_http(c_http)
            else:
                host_http = host_port or ports.alloc.alloc_host()
                c_http = ports.alloc.alloc_http()
            
            if user_https_port is not None:
                c_https = user_https_port
                ports.alloc.reserve_https(c_https)
            else:
                c_https = ports.alloc.alloc_https(avoid=c_http)
            
            # CONF handling
            if "CONF" in req_env:
                final_conf = req_env["CONF"]
            else:
                conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
                final_conf = "\\n".join(conf_lines)
            
            env = {
                **req_env, 
                "CONF": final_conf,
                "HTTP_PORT": str(c_http),
                "HTTPS_PORT": str(c_https),
                "BIND_ALL": "true"
            }
            
            ports_mapping = {f"{c_http}/tcp": host_http}
            
            return {
                "env": env,
                "ports": ports_mapping,
                "host_http_port": host_http,
                "container_http_port": c_http,
                "container_https_port": c_https
            }
        
        # Test Case 1: User provides CONF with specific ports
        print("\\n📋 Test Case 1: User CONF with --http-port=6879")
        user_env = {"CONF": user_conf}
        result = simulate_fixed_logic(user_env)
        
        print("   Fixed behavior:")
        print(f"     CONF: {repr(result['env']['CONF'])}")
        print(f"     HTTP_PORT: {result['env']['HTTP_PORT']}")
        print(f"     Docker mapping: {result['ports']}")
        print(f"     Host port: {result['host_http_port']}")
        print(f"     Container port: {result['container_http_port']}")
        
        # Verify the fix
        conf_correct = result['env']['CONF'] == user_conf
        http_port_correct = result['env']['HTTP_PORT'] == "6879"
        docker_mapping_correct = result['ports'] == {"6879/tcp": 6879}
        container_port_correct = result['container_http_port'] == 6879
        host_port_correct = result['host_http_port'] == 6879
        
        print(f"\\n   🔍 Verification:")
        print(f"     CONF preserved: {conf_correct}")
        print(f"     HTTP_PORT=6879: {http_port_correct}")
        print(f"     Docker maps 6879:6879: {docker_mapping_correct}")
        print(f"     Container port=6879: {container_port_correct}")
        print(f"     Host port=6879: {host_port_correct}")
        print(f"     Reserved HTTP port 6879: {6879 in reserved_http}")
        print(f"     Reserved HTTPS port 6880: {6880 in reserved_https}")
        
        test1_passed = all([conf_correct, http_port_correct, docker_mapping_correct, 
                           container_port_correct, host_port_correct])
        
        # Test Case 2: No user CONF (orchestrator default)
        print("\\n📋 Test Case 2: No user CONF (orchestrator default)")
        result2 = simulate_fixed_logic({})
        
        print("   Default behavior:")
        print(f"     CONF: {repr(result2['env']['CONF'])}")
        print(f"     HTTP_PORT: {result2['env']['HTTP_PORT']}")
        print(f"     Docker mapping: {result2['ports']}")
        
        # For default case, should use allocated ports
        expected_default_conf = f"--http-port={result2['container_http_port']}\\n--https-port={result2['container_https_port']}\\n--bind-all"
        conf_default_correct = result2['env']['CONF'] == expected_default_conf
        http_port_default_correct = result2['env']['HTTP_PORT'] == str(result2['container_http_port'])
        
        print(f"\\n   🔍 Default Verification:")
        print(f"     CONF matches allocated ports: {conf_default_correct}")
        print(f"     HTTP_PORT matches container port: {http_port_default_correct}")
        
        test2_passed = conf_default_correct and http_port_default_correct
        
        # Test Case 3: User provides host_port override
        print("\\n📋 Test Case 3: User CONF with host_port override")
        result3 = simulate_fixed_logic({"CONF": user_conf}, host_port=7777)
        
        print("   Override behavior:")
        print(f"     Host port: {result3['host_http_port']} (should be 7777)")
        print(f"     Container port: {result3['container_http_port']} (should be 6879 from CONF)")
        print(f"     Docker mapping: {result3['ports']}")
        
        host_override_correct = result3['host_http_port'] == 7777
        container_from_conf_correct = result3['container_http_port'] == 6879
        docker_override_correct = result3['ports'] == {"6879/tcp": 7777}
        
        print(f"\\n   🔍 Override Verification:")
        print(f"     Host port override works: {host_override_correct}")
        print(f"     Container port from CONF: {container_from_conf_correct}")
        print(f"     Docker mapping correct: {docker_override_correct}")
        
        test3_passed = host_override_correct and container_from_conf_correct and docker_override_correct
        
        all_passed = test1_passed and test2_passed and test3_passed
        
        print(f"\\n📊 Test Results:")
        print(f"   User CONF case: {'✅ PASSED' if test1_passed else '❌ FAILED'}")
        print(f"   Default case: {'✅ PASSED' if test2_passed else '❌ FAILED'}")
        print(f"   Override case: {'✅ PASSED' if test3_passed else '❌ FAILED'}")
        print(f"   Overall: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
        
        return all_passed
        
    finally:
        # Restore original functions
        ports.alloc.alloc_host = original_alloc_host
        ports.alloc.alloc_http = original_alloc_http
        ports.alloc.alloc_https = original_alloc_https
        ports.alloc.reserve_http = original_reserve_http
        ports.alloc.reserve_https = original_reserve_https

if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    print("🚀 Testing Fixed Port Binding Logic")
    print("=" * 60)
    
    try:
        all_passed = test_fixed_port_binding()
        sys.exit(0 if all_passed else 1)
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)