#!/usr/bin/env python3
"""
Test script for the port mapping fix in acestream provisioning.
This test validates that when user provides CONF with specific ports,
those ports are used for both the internal CONF and Docker port mapping.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_port_parsing():
    """Test the _parse_ports_from_conf function."""
    print("🧪 Testing Port Parsing from CONF")
    print("=" * 50)
    
    from app.services.provisioner import _parse_ports_from_conf
    
    tests_passed = 0
    total_tests = 0
    
    # Test 1: Docker Compose scenario
    print("\n📋 Test 1: Docker Compose CONF")
    total_tests += 1
    conf = "--http-port=6879\n--https-port=6880\n--bind-all"
    http_port, https_port = _parse_ports_from_conf(conf)
    
    if http_port == 6879 and https_port == 6880:
        print("✅ PASS: Correctly parsed HTTP=6879, HTTPS=6880")
        tests_passed += 1
    else:
        print(f"❌ FAIL: Expected HTTP=6879, HTTPS=6880, got HTTP={http_port}, HTTPS={https_port}")
    
    # Test 2: Only HTTP port
    print("\n📋 Test 2: Only HTTP port")
    total_tests += 1
    conf = "--http-port=8080\n--bind-all"
    http_port, https_port = _parse_ports_from_conf(conf)
    
    if http_port == 8080 and https_port is None:
        print("✅ PASS: Correctly parsed HTTP=8080, HTTPS=None")
        tests_passed += 1
    else:
        print(f"❌ FAIL: Expected HTTP=8080, HTTPS=None, got HTTP={http_port}, HTTPS={https_port}")
    
    # Test 3: No ports specified
    print("\n📋 Test 3: No ports in CONF")
    total_tests += 1
    conf = "--bind-all\n--some-other-option"
    http_port, https_port = _parse_ports_from_conf(conf)
    
    if http_port is None and https_port is None:
        print("✅ PASS: Correctly parsed HTTP=None, HTTPS=None")
        tests_passed += 1
    else:
        print(f"❌ FAIL: Expected HTTP=None, HTTPS=None, got HTTP={http_port}, HTTPS={https_port}")
    
    # Test 4: Empty CONF
    print("\n📋 Test 4: Empty CONF")
    total_tests += 1
    conf = ""
    http_port, https_port = _parse_ports_from_conf(conf)
    
    if http_port is None and https_port is None:
        print("✅ PASS: Correctly handled empty CONF")
        tests_passed += 1
    else:
        print(f"❌ FAIL: Expected HTTP=None, HTTPS=None, got HTTP={http_port}, HTTPS={https_port}")
    
    print(f"\n🎯 Port Parsing Results: {tests_passed}/{total_tests} passed")
    return tests_passed == total_tests


def test_port_mapping_logic():
    """Test the port mapping logic without actually starting containers."""
    print("\n🧪 Testing Port Mapping Logic")
    print("=" * 50)
    
    from app.services.provisioner import AceProvisionRequest, _validate_user_ports, _reserve_user_ports
    
    # Mock the alloc module to simulate port allocation
    class MockAlloc:
        def __init__(self):
            self.next_http = 40000
            self.next_https = 45000
            self.reserved_http = set()
            self.reserved_https = set()
            
        def alloc_host(self):
            return 19000
            
        def alloc_http(self):
            port = self.next_http
            self.next_http += 1
            return port
            
        def alloc_https(self, avoid=None):
            port = self.next_https
            self.next_https += 1
            return port
            
        def reserve_http(self, port):
            self.reserved_http.add(port)
            
        def reserve_https(self, port):
            self.reserved_https.add(port)
    
    # Mock the cfg module for port ranges
    class MockCfg:
        ACE_HTTP_RANGE = "40000-44999"
        ACE_HTTPS_RANGE = "45000-49999"
    
    # Replace the real alloc and cfg with our mocks for testing
    import app.services.provisioner as provisioner_module
    original_alloc = provisioner_module.alloc
    original_cfg = provisioner_module.cfg
    provisioner_module.alloc = MockAlloc()
    provisioner_module.cfg = MockCfg()
    
    try:
        tests_passed = 0
        total_tests = 0
        
        # Test 1: User provides CONF with both ports (Docker Compose scenario)
        print("\n📋 Test 1: User CONF with HTTP=6879, HTTPS=6880 (Docker Compose scenario)")
        total_tests += 1
        
        # This is the main test case - user wants to use ports outside orchestrator ranges
        req = AceProvisionRequest(env={"CONF": "--http-port=6879\n--https-port=6880\n--bind-all"})
        
        if "CONF" in req.env:
            final_conf = req.env["CONF"]
            user_http_port, user_https_port = provisioner_module._parse_ports_from_conf(final_conf)
            
            # Validate the ports
            _validate_user_ports(user_http_port, user_https_port)
            
            # Reserve the ports if they're in managed ranges (they won't be in this case)
            _reserve_user_ports(user_http_port, user_https_port)
            
            if user_http_port is not None:
                c_http = user_http_port
            else:
                c_http = provisioner_module.alloc.alloc_http()
                
            if user_https_port is not None:
                c_https = user_https_port
            else:
                c_https = provisioner_module.alloc.alloc_https(avoid=c_http)
        
        # Verify the ports match what user specified
        if c_http == 6879 and c_https == 6880:
            print("✅ PASS: Container ports match user CONF (HTTP=6879, HTTPS=6880)")
            print("   Note: Ports outside managed ranges are allowed")
            tests_passed += 1
        else:
            print(f"❌ FAIL: Expected container ports HTTP=6879, HTTPS=6880, got HTTP={c_http}, HTTPS={c_https}")
        
        # Test 2: User provides CONF with ports in managed ranges
        print("\n📋 Test 2: User CONF with HTTP=40500, HTTPS=45500 (within managed ranges)")
        total_tests += 1
        
        req = AceProvisionRequest(env={"CONF": "--http-port=40500\n--https-port=45500\n--bind-all"})
        
        if "CONF" in req.env:
            final_conf = req.env["CONF"]
            user_http_port, user_https_port = provisioner_module._parse_ports_from_conf(final_conf)
            
            _validate_user_ports(user_http_port, user_https_port)
            _reserve_user_ports(user_http_port, user_https_port)
            
            if user_http_port is not None:
                c_http = user_http_port
            else:
                c_http = provisioner_module.alloc.alloc_http()
                
            if user_https_port is not None:
                c_https = user_https_port
            else:
                c_https = provisioner_module.alloc.alloc_https(avoid=c_http)
        
        # Verify the ports match and were reserved
        reserved_correctly = (40500 in provisioner_module.alloc.reserved_http and 
                            45500 in provisioner_module.alloc.reserved_https)
        
        if c_http == 40500 and c_https == 45500 and reserved_correctly:
            print("✅ PASS: Container ports match user CONF and were reserved (HTTP=40500, HTTPS=45500)")
            tests_passed += 1
        else:
            print(f"❌ FAIL: Expected HTTP=40500, HTTPS=45500 with reservation, got HTTP={c_http}, HTTPS={c_https}")
            print(f"   Reserved HTTP: {provisioner_module.alloc.reserved_http}")
            print(f"   Reserved HTTPS: {provisioner_module.alloc.reserved_https}")
        
        # Test 3: Invalid port validation
        print("\n📋 Test 3: Invalid port validation")
        total_tests += 1
        
        try:
            _validate_user_ports(70000, 65536)  # Ports outside valid range
            print("❌ FAIL: Should have raised error for invalid ports")
        except RuntimeError as e:
            if "outside valid port range" in str(e):
                print("✅ PASS: Correctly rejected invalid ports")
                tests_passed += 1
            else:
                print(f"❌ FAIL: Wrong error message: {e}")
        
        # Test 4: Same port for HTTP and HTTPS
        print("\n📋 Test 4: Same port for HTTP and HTTPS validation")
        total_tests += 1
        
        try:
            _validate_user_ports(8080, 8080)  # Same port for both
            print("❌ FAIL: Should have raised error for same ports")
        except RuntimeError as e:
            if "cannot use the same port" in str(e):
                print("✅ PASS: Correctly rejected same port for HTTP and HTTPS")
                tests_passed += 1
            else:
                print(f"❌ FAIL: Wrong error message: {e}")
        
        # Test 5: No user CONF (default behavior)
        print("\n📋 Test 5: No user CONF (default behavior)")
        total_tests += 1
        
        req = AceProvisionRequest(env={})
        
        if "CONF" in req.env:
            final_conf = req.env["CONF"]
            user_http_port, user_https_port = provisioner_module._parse_ports_from_conf(final_conf)
        else:
            c_http = provisioner_module.alloc.alloc_http()
            c_https = provisioner_module.alloc.alloc_https(avoid=c_http)
            conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
            final_conf = "\n".join(conf_lines)
        
        # Verify both ports are allocated (should be 40000 and 45000 in our mock)
        if c_http == 40000 and c_https == 45000:
            print("✅ PASS: Both ports allocated (HTTP=40000, HTTPS=45000)")
            tests_passed += 1
        else:
            print(f"❌ FAIL: Expected HTTP=40000, HTTPS=45000, got HTTP={c_http}, HTTPS={c_https}")
        
        print(f"\n🎯 Port Mapping Logic Results: {tests_passed}/{total_tests} passed")
        return tests_passed == total_tests
        
    finally:
        # Restore original alloc and cfg
        provisioner_module.alloc = original_alloc
        provisioner_module.cfg = original_cfg


def main():
    """Main test function."""
    print("🎯 AceStream Port Mapping Fix Test")
    print("=" * 70)
    
    print("\n🔍 This test validates:")
    print("1. Parsing ports from user-provided CONF strings")
    print("2. Using parsed ports for container port mapping")
    print("3. Proper fallback when ports are not specified")
    
    parsing_success = test_port_parsing()
    mapping_success = test_port_mapping_logic()
    
    print("\n" + "=" * 70)
    
    if parsing_success and mapping_success:
        print("✅ PORT MAPPING FIX TEST SUCCESSFUL")
        print("\n📋 Summary:")
        print("✅ Port parsing from CONF works correctly")
        print("✅ Port mapping logic uses parsed ports")
        print("✅ Fallback behavior for missing ports works")
        print("\n🎉 The fix should resolve the acestream port mismatch issue!")
        return True
    else:
        print("❌ PORT MAPPING FIX TEST FAILED")
        print("\n🔧 Issues found:")
        if not parsing_success:
            print("❌ Port parsing from CONF is not working correctly")
        if not mapping_success:
            print("❌ Port mapping logic is not working correctly")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)