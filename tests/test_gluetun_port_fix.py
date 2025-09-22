#!/usr/bin/env python3
"""
Test to verify that when Gluetun is enabled, AceStream engines don't map ports 
to avoid "port is already allocated" errors.
"""

import sys
import os
from unittest.mock import patch, MagicMock

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_port_mapping_with_gluetun():
    """Test that engines don't map ports when Gluetun is enabled."""
    print("\n🧪 Testing port mapping behavior with Gluetun...")
    
    try:
        from app.services.provisioner import start_acestream, AceProvisionRequest
        from app.core.config import cfg
        
        # Store original values
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_map_https = cfg.ACE_MAP_HTTPS
        
        # Mock Docker client and container
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.id = "test_container_id"
        mock_container.name = "test-acestream-1"
        mock_container.reload = MagicMock()
        mock_container.attrs = {"Name": "/test-acestream-1"}
        
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        
        # Test 1: Without Gluetun (should include port mappings)
        print("   📋 Test 1: Without Gluetun (should map ports)")
        cfg.GLUETUN_CONTAINER_NAME = None
        cfg.ACE_MAP_HTTPS = True
        
        with patch('app.services.provisioner.get_client', return_value=mock_client):
            with patch('app.services.provisioner.safe', side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)):
                with patch('app.services.naming.generate_container_name', return_value="test-acestream-1"):
                    with patch('app.services.provisioner.alloc.alloc_host', side_effect=[19001, 19002]):
                        with patch('app.services.provisioner.alloc.alloc_http', return_value=6879):
                            with patch('app.services.provisioner.alloc.alloc_https', return_value=6880):
                                
                                req = AceProvisionRequest()
                                start_acestream(req)
        
        # Verify ports were passed to container creation
        call_args, call_kwargs = mock_client.containers.run.call_args
        assert "ports" in call_kwargs, "Without Gluetun, ports should be included"
        expected_ports = {"19001/tcp": 19001, "6880/tcp": 19002}
        assert call_kwargs["ports"] == expected_ports, f"Expected {expected_ports}, got {call_kwargs.get('ports')}"
        print("   ✅ Without Gluetun: Ports correctly mapped")
        
        # Reset mock
        mock_client.reset_mock()
        
        # Test 2: With Gluetun (should NOT include port mappings)
        print("   📋 Test 2: With Gluetun (should NOT map ports)")
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        # Create new mock container for Gluetun test
        mock_container_gluetun = MagicMock()
        mock_container_gluetun.status = "running"
        mock_container_gluetun.id = "test_container_gluetun_id"
        mock_container_gluetun.name = "test-acestream-2"
        mock_container_gluetun.reload = MagicMock()
        mock_container_gluetun.attrs = {"Name": "/test-acestream-2"}
        
        mock_client_gluetun = MagicMock()
        mock_client_gluetun.containers.run.return_value = mock_container_gluetun
        
        # Mock Gluetun health check
        with patch('app.services.provisioner.get_client', return_value=mock_client_gluetun):
            with patch('app.services.provisioner.safe', side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)):
                with patch('app.services.naming.generate_container_name', return_value="test-acestream-2"):
                    with patch('app.services.provisioner.alloc.alloc_host', return_value=19003):
                        with patch('app.services.provisioner.alloc.alloc_http', return_value=6879):
                            with patch('app.services.provisioner.alloc.alloc_https', return_value=6880):
                                with patch('asyncio.get_event_loop') as mock_loop:
                                    mock_monitor = MagicMock()
                                    mock_monitor.wait_for_healthy.return_value = True
                                    mock_loop.return_value.run_until_complete.return_value = True
                                    
                                    with patch('app.services.gluetun.gluetun_monitor', mock_monitor):
                                        req = AceProvisionRequest()
                                        start_acestream(req)
        
        # Verify ports were NOT passed to container creation
        call_args, call_kwargs = mock_client_gluetun.containers.run.call_args
        assert "ports" not in call_kwargs, f"With Gluetun, ports should not be included, but got: {call_kwargs.get('ports')}"
        print("   ✅ With Gluetun: Ports correctly NOT mapped")
        
        # Verify network mode is set correctly
        assert "network_mode" in call_kwargs, "With Gluetun, network_mode should be set"
        assert call_kwargs["network_mode"] == "container:gluetun", f"Expected 'container:gluetun', got {call_kwargs.get('network_mode')}"
        print("   ✅ With Gluetun: Network mode correctly set to 'container:gluetun'")
        
        # Restore original values
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        cfg.ACE_MAP_HTTPS = original_map_https
        
        print("\n🎉 Port mapping test passed!")
        print("✅ Without Gluetun: Ports are mapped for external access")
        print("✅ With Gluetun: Ports are NOT mapped (handled by Gluetun container)")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_edge_cases():
    """Test edge cases for port mapping behavior."""
    print("\n🧪 Testing edge cases...")
    
    try:
        from app.services.provisioner import start_acestream, AceProvisionRequest
        from app.core.config import cfg
        
        # Store original values
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_map_https = cfg.ACE_MAP_HTTPS
        
        # Mock Docker client and container
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.id = "test_container_id"
        mock_container.name = "test-acestream-edge"
        mock_container.reload = MagicMock()
        mock_container.attrs = {"Name": "/test-acestream-edge"}
        
        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        
        # Test: Gluetun enabled but HTTPS mapping disabled
        print("   📋 Edge case: Gluetun enabled, HTTPS mapping disabled")
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        cfg.ACE_MAP_HTTPS = False
        
        with patch('app.services.provisioner.get_client', return_value=mock_client):
            with patch('app.services.provisioner.safe', side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)):
                with patch('app.services.naming.generate_container_name', return_value="test-acestream-edge"):
                    with patch('app.services.provisioner.alloc.alloc_host', return_value=19004):
                        with patch('app.services.provisioner.alloc.alloc_http', return_value=6879):
                            with patch('asyncio.get_event_loop') as mock_loop:
                                mock_monitor = MagicMock()
                                mock_monitor.wait_for_healthy.return_value = True
                                mock_loop.return_value.run_until_complete.return_value = True
                                
                                with patch('app.services.gluetun.gluetun_monitor', mock_monitor):
                                    req = AceProvisionRequest()
                                    start_acestream(req)
        
        # Verify ports are still not included even with HTTPS disabled
        call_args, call_kwargs = mock_client.containers.run.call_args
        assert "ports" not in call_kwargs, "With Gluetun, ports should never be included regardless of HTTPS setting"
        print("   ✅ Edge case passed: No ports mapped even with HTTPS disabled")
        
        # Restore original values
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        cfg.ACE_MAP_HTTPS = original_map_https
        
        return True
        
    except Exception as e:
        print(f"❌ Edge case test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🔧 Testing Gluetun Port Mapping Fix")
    print("=" * 60)
    
    test1_result = test_port_mapping_with_gluetun()
    test2_result = test_edge_cases()
    
    if test1_result and test2_result:
        print("\n🎉 All port mapping tests passed!")
        print("🔧 Gluetun port mapping fix is working correctly!")
        sys.exit(0)
    else:
        print("\n❌ Some port mapping tests failed!")
        sys.exit(1)