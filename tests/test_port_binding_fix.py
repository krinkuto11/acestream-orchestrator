#!/usr/bin/env python3
"""
Test to demonstrate and verify the docker port binding fix.

Problem: Docker port binding should match the internal HTTP port when user provides CONF.
Currently: Docker maps external:internal (e.g., 6879:40001) but HTTP_PORT=40001
Expected: Docker maps external:internal (e.g., 6879:6879) and HTTP_PORT=6879
"""

def test_docker_port_binding_consistency():
    """Test that docker port bindings match the internal HTTP_PORT from user CONF."""
    print("🧪 Testing Docker Port Binding Consistency")
    print("=" * 60)
    
    from app.services.provisioner import AceProvisionRequest
    from app.services import ports
    
    # Backup original allocation functions
    original_alloc_host = ports.alloc.alloc_host
    original_alloc_http = ports.alloc.alloc_http
    original_alloc_https = ports.alloc.alloc_https
    
    try:
        # Test Case 1: User provides CONF with specific ports
        print("\n📋 Test Case 1: User CONF with --http-port=6879")
        
        def mock_alloc_host():
            return 6879
        
        def mock_alloc_http():
            return 40001  # This will demonstrate the problem
        
        def mock_alloc_https(avoid=None):
            return 45001
        
        ports.alloc.alloc_host = mock_alloc_host
        ports.alloc.alloc_http = mock_alloc_http
        ports.alloc.alloc_https = mock_alloc_https
        
        # Simulate user CONF that specifies port 6879
        user_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
        
        def simulate_current_logic(req_env, host_port=None):
            """Simulate current provisioner logic to show the problem"""
            host_http = host_port or ports.alloc.alloc_host()  # 6879
            c_http = ports.alloc.alloc_http()                  # 40001 (problem!)
            c_https = ports.alloc.alloc_https(avoid=c_http)    # 45001
            
            # Current logic - user CONF preserved but ports don't match
            if "CONF" in req_env:
                final_conf = req_env["CONF"]
            else:
                conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
                final_conf = "\n".join(conf_lines)
            
            env = {
                **req_env, 
                "CONF": final_conf,
                "HTTP_PORT": str(c_http),  # 40001 - doesn't match CONF!
                "HTTPS_PORT": str(c_https),
                "BIND_ALL": "true"
            }
            
            ports_mapping = {f"{c_http}/tcp": host_http}  # 40001:6879 - mismatch!
            
            return {
                "env": env,
                "ports": ports_mapping,
                "host_http_port": host_http,
                "container_http_port": c_http,
                "container_https_port": c_https
            }
        
        # Test current behavior
        result_current = simulate_current_logic({"CONF": user_conf}, host_port=6879)
        
        print("   Current behavior (problematic):")
        print(f"     CONF: {repr(result_current['env']['CONF'])}")
        print(f"     HTTP_PORT: {result_current['env']['HTTP_PORT']}")
        print(f"     Docker mapping: {result_current['ports']}")
        print(f"     Host port: {result_current['host_http_port']}")
        print(f"     Container port: {result_current['container_http_port']}")
        
        # Identify the problem
        conf_has_6879 = "--http-port=6879" in result_current['env']['CONF']
        http_port_is_6879 = result_current['env']['HTTP_PORT'] == "6879"
        docker_maps_6879_to_6879 = "6879/tcp" in result_current['ports']
        
        print(f"\n   🔍 Problem Analysis:")
        print(f"     CONF has --http-port=6879: {conf_has_6879}")
        print(f"     HTTP_PORT=6879: {http_port_is_6879}")
        print(f"     Docker maps 6879:6879: {docker_maps_6879_to_6879}")
        
        if conf_has_6879 and not http_port_is_6879:
            print(f"     ❌ MISMATCH: CONF says port 6879 but HTTP_PORT is {result_current['env']['HTTP_PORT']}")
        
        if conf_has_6879 and not docker_maps_6879_to_6879:
            print(f"     ❌ MISMATCH: CONF says port 6879 but Docker maps {result_current['ports']}")
        
        print(f"\n   Expected behavior:")
        print(f"     CONF: '--http-port=6879\\n--https-port=6880\\n--bind-all'")
        print(f"     HTTP_PORT: '6879'")
        print(f"     Docker mapping: {{'6879/tcp': 6879}}")
        print(f"     Both container and host should use port 6879")
        
        return not (conf_has_6879 and http_port_is_6879 and docker_maps_6879_to_6879)
        
    finally:
        # Restore original functions
        ports.alloc.alloc_host = original_alloc_host
        ports.alloc.alloc_http = original_alloc_http
        ports.alloc.alloc_https = original_alloc_https

def parse_conf_port(conf_string, port_type="http"):
    """
    Parse a CONF string to extract port number for given type.
    
    Args:
        conf_string: String like "--http-port=6879\n--https-port=6880\n--bind-all"
        port_type: "http" or "https"
    
    Returns:
        int: Port number or None if not found
    """
    if not conf_string:
        return None
        
    lines = conf_string.split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith(f"--{port_type}-port="):
            try:
                port_str = line.split('=', 1)[1]
                return int(port_str)
            except (IndexError, ValueError):
                continue
    return None

def test_conf_parser():
    """Test the CONF parsing utility function."""
    print("\n🧪 Testing CONF Parser")
    print("-" * 30)
    
    test_cases = [
        ("--http-port=6879\n--https-port=6880\n--bind-all", "http", 6879),
        ("--http-port=6879\n--https-port=6880\n--bind-all", "https", 6880),
        ("--http-port=8080\n--bind-all", "http", 8080),
        ("--http-port=8080\n--bind-all", "https", None),
        ("--bind-all", "http", None),
        ("", "http", None),
        ("--http-port=invalid\n--bind-all", "http", None),
    ]
    
    all_passed = True
    for conf, port_type, expected in test_cases:
        result = parse_conf_port(conf, port_type)
        passed = result == expected
        all_passed = all_passed and passed
        status = "✅" if passed else "❌"
        print(f"   {status} parse_conf_port({repr(conf)}, '{port_type}') -> {result} (expected {expected})")
    
    return all_passed

if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    print("🚀 Running Port Binding Fix Tests")
    print("=" * 60)
    
    parser_passed = test_conf_parser()
    binding_problem_exists = test_docker_port_binding_consistency()
    
    print(f"\n📊 Test Results:")
    print(f"   CONF Parser: {'✅ PASSED' if parser_passed else '❌ FAILED'}")
    print(f"   Port Binding Problem Exists: {'✅ YES (need to fix)' if binding_problem_exists else '❌ NO'}")
    
    if binding_problem_exists:
        print(f"\n🎯 Next Steps:")
        print(f"   1. Implement parse_conf_port function in provisioner.py")
        print(f"   2. Modify start_acestream to use parsed ports from user CONF")
        print(f"   3. Ensure docker mapping matches internal container ports")
    
    sys.exit(0 if parser_passed else 1)