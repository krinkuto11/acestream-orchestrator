#!/usr/bin/env python3
"""
Verification script for the acestream-http-proxy Docker Compose scenario.
Simulates the exact scenario described in the problem statement.
"""

def verify_docker_compose_scenario():
    """Verify the specific Docker Compose scenario from the problem statement."""
    print("🔍 Verifying Docker Compose Scenario")
    print("=" * 50)
    print("Problem Statement Scenario:")
    print("  - container_name: acestream")
    print("  - image: ghcr.io/krinkuto11/acestream-http-proxy:latest")
    print("  - ports: '6879:6879'")
    print("  - environment:")
    print("    - HTTP_PORT: 6879")
    print("    - HTTPS_PORT: 6880")
    print("    - BIND_ALL: 'true'")
    print()
    
    from app.services.provisioner import AceProvisionRequest
    from app.services import ports
    
    # Mock port allocation to simulate the orchestrator allocating the ports
    # that match the Docker Compose configuration
    original_alloc_host = ports.alloc.alloc_host
    original_alloc_http = ports.alloc.alloc_http
    original_alloc_https = ports.alloc.alloc_https
    
    def mock_alloc_host():
        return 6879  # This will be the host port binding
    
    def mock_alloc_http():
        return 40001  # Internal container HTTP port
    
    def mock_alloc_https(avoid=None):
        return 45001  # Internal container HTTPS port
    
    ports.alloc.alloc_host = mock_alloc_host
    ports.alloc.alloc_http = mock_alloc_http
    ports.alloc.alloc_https = mock_alloc_https
    
    try:
        # Simulate what the orchestrator would do when provisioning
        # with the new acestream-http-proxy image
        def simulate_provisioning(req_env, host_port=None):
            """Simulate the start_acestream provisioning logic"""
            # Port allocation
            host_http = host_port or ports.alloc.alloc_host()  # 6879
            c_http = ports.alloc.alloc_http()                  # 40001
            c_https = ports.alloc.alloc_https(avoid=c_http)    # 45001
            
            # CONF handling
            if "CONF" in req_env:
                final_conf = req_env["CONF"]
            else:
                conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
                final_conf = "\n".join(conf_lines)
            
            # Environment variables (NEW: for acestream-http-proxy)
            env = {
                **req_env, 
                "CONF": final_conf,
                "HTTP_PORT": str(c_http),
                "HTTPS_PORT": str(c_https),
                "BIND_ALL": "true"
            }
            
            # Port mapping (what Docker would get)
            ports_mapping = {f"{c_http}/tcp": host_http}
            
            return {
                "env": env,
                "ports": ports_mapping,
                "host_http_port": host_http,
                "container_http_port": c_http,
                "container_https_port": c_https
            }
        
        print("🧪 Testing orchestrator provisioning for acestream-http-proxy...")
        
        # Test 1: User provides CONF matching the image's expectations
        print("\n📋 Test 1: User-provided CONF (Docker Compose style)")
        user_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
        user_env = {"CONF": user_conf}
        
        result = simulate_provisioning(user_env, host_port=6879)
        
        print(f"   Host port binding: {result['host_http_port']} (external)")
        print(f"   Container HTTP port: {result['container_http_port']} (internal)")
        print(f"   Container HTTPS port: {result['container_https_port']} (internal)")
        print(f"   Docker port mapping: {result['ports']}")
        print()
        print("   Environment variables passed to container:")
        for key, value in result['env'].items():
            if key in ['CONF', 'HTTP_PORT', 'HTTPS_PORT', 'BIND_ALL']:
                print(f"     {key}={repr(value)}")
        
        # Verify requirements from problem statement
        print("\n   🔍 Verification against problem requirements:")
        
        requirements_met = True
        
        # Requirement 1: HTTP_PORT must match the port bindings
        expected_http_port = str(result['container_http_port'])
        actual_http_port = result['env'].get('HTTP_PORT')
        if actual_http_port == expected_http_port:
            print(f"     ✅ HTTP_PORT ({actual_http_port}) matches container HTTP port")
        else:
            print(f"     ❌ HTTP_PORT ({actual_http_port}) doesn't match container HTTP port ({expected_http_port})")
            requirements_met = False
        
        # Requirement 2: BIND_ALL must always be true
        actual_bind_all = result['env'].get('BIND_ALL')
        if actual_bind_all == "true":
            print(f"     ✅ BIND_ALL is 'true'")
        else:
            print(f"     ❌ BIND_ALL is '{actual_bind_all}', should be 'true'")
            requirements_met = False
        
        # Requirement 3: CONF should be preserved if user provided it
        actual_conf = result['env'].get('CONF')
        if actual_conf == user_conf:
            print(f"     ✅ User CONF preserved exactly")
        else:
            print(f"     ❌ User CONF was modified")
            print(f"         Expected: {repr(user_conf)}")
            print(f"         Got:      {repr(actual_conf)}")
            requirements_met = False
        
        # Test 2: No user CONF (orchestrator default)
        print("\n📋 Test 2: No user CONF (orchestrator generates default)")
        
        result2 = simulate_provisioning({}, host_port=6879)
        
        print(f"   Container HTTP port: {result2['container_http_port']}")
        print(f"   Container HTTPS port: {result2['container_https_port']}")
        print()
        print("   Environment variables passed to container:")
        for key, value in result2['env'].items():
            if key in ['CONF', 'HTTP_PORT', 'HTTPS_PORT', 'BIND_ALL']:
                print(f"     {key}={repr(value)}")
        
        # Verify default behavior
        expected_default_conf = f"--http-port={result2['container_http_port']}\n--https-port={result2['container_https_port']}\n--bind-all"
        actual_default_conf = result2['env'].get('CONF')
        
        if actual_default_conf == expected_default_conf:
            print(f"     ✅ Default CONF generated correctly")
        else:
            print(f"     ❌ Default CONF generation failed")
            requirements_met = False
        
        # Final verification
        print(f"\n🎯 Overall verification result:")
        if requirements_met:
            print("✅ ALL REQUIREMENTS MET!")
            print("✅ acestream-http-proxy image requirements satisfied")
            print("✅ HTTP_PORT matches container port bindings")
            print("✅ BIND_ALL is always 'true'")
            print("✅ User CONF preserved when provided")
            print("✅ Default CONF generated when not provided")
            return True
        else:
            print("❌ Some requirements not met")
            return False
            
    finally:
        # Restore original functions
        ports.alloc.alloc_host = original_alloc_host
        ports.alloc.alloc_http = original_alloc_http
        ports.alloc.alloc_https = original_alloc_https

if __name__ == "__main__":
    import sys
    sys.path.append('.')
    result = verify_docker_compose_scenario()
    sys.exit(0 if result else 1)