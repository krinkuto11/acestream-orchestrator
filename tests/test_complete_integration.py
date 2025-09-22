#!/usr/bin/env python3
"""
End-to-end test for Gluetun enhancements.
This test verifies all three requirements work together.
"""

import sys
import os
from unittest.mock import patch, MagicMock

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_complete_gluetun_integration():
    """Test all Gluetun enhancements working together."""
    print("\n🧪 Testing complete Gluetun integration...")
    
    try:
        from app.services.provisioner import start_acestream, AceProvisionRequest
        from app.core.config import cfg
        
        # Set up Gluetun environment
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_max_active = cfg.MAX_ACTIVE_REPLICAS
        
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        cfg.MAX_ACTIVE_REPLICAS = 5
        
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.id = "test_container_complete"
        mock_container.attrs = {"Name": "/test-container-complete"}
        
        # Test complete integration
        with patch('app.services.provisioner.get_client') as mock_client:
            mock_client.return_value.containers.run.return_value = mock_container
            with patch('app.services.provisioner.safe', side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)):
                with patch('app.services.naming.generate_container_name', return_value="test-complete"):
                    with patch('app.services.provisioner.alloc.alloc_gluetun_port', side_effect=[19000, 19001]):
                        with patch('app.services.provisioner._check_gluetun_health_sync', return_value=True):
                            with patch('app.services.gluetun.get_forwarded_port_sync', return_value=5914):
                                with patch('app.services.gluetun.gluetun_monitor') as mock_monitor:
                                    mock_monitor.is_healthy.return_value = True
                                    
                                    req = AceProvisionRequest()
                                    result = start_acestream(req)
                                    
                                    # Verify the result
                                    assert result.container_id == "test_container_complete"
                                    assert result.host_http_port == 19000
                                    assert result.container_http_port == 19000
                                    
                                    # Verify container creation arguments
                                    call_args, call_kwargs = mock_client.return_value.containers.run.call_args
                                    
                                    # Check 1: No port mappings (handled by Gluetun)
                                    assert "ports" not in call_kwargs, "Should not have port mappings with Gluetun"
                                    
                                    # Check 2: Network mode set to Gluetun
                                    assert call_kwargs.get("network_mode") == "container:gluetun", "Should use Gluetun network mode"
                                    
                                    # Check 3: P2P_PORT environment variable set
                                    env = call_kwargs.get("environment", {})
                                    assert env.get("P2P_PORT") == "5914", f"P2P_PORT should be 5914, got {env.get('P2P_PORT')}"
                                    
                                    print("   ✅ Container configured with correct network mode")
                                    print("   ✅ P2P_PORT set to VPN forwarded port")
                                    print("   ✅ Port allocation from Gluetun range")
                                    print("   ✅ No individual port mappings (handled by Gluetun)")
        
        # Test MAX_ACTIVE_REPLICAS enforcement
        print("\n   Testing MAX_ACTIVE_REPLICAS enforcement...")
        from app.services.ports import PortAllocator
        
        allocator = PortAllocator()
        allocated_ports = []
        
        # Allocate up to the limit
        for i in range(cfg.MAX_ACTIVE_REPLICAS):
            port = allocator.alloc_gluetun_port()
            allocated_ports.append(port)
        
        # Try to allocate one more - should fail
        try:
            allocator.alloc_gluetun_port()
            print("   ❌ Should have failed to allocate beyond MAX_ACTIVE_REPLICAS")
            return False
        except RuntimeError as e:
            if "Maximum active replicas limit reached" in str(e):
                print(f"   ✅ Correctly enforced limit of {cfg.MAX_ACTIVE_REPLICAS} replicas")
            else:
                print(f"   ❌ Unexpected error: {e}")
                return False
        
        # Clean up allocated ports
        for port in allocated_ports:
            allocator.free_gluetun_port(port)
        
        # Restore original values
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        cfg.MAX_ACTIVE_REPLICAS = original_max_active
        
        return True
        
    except Exception as e:
        print(f"   ❌ Complete integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🔧 Testing Complete Gluetun Integration")
    print("=" * 60)
    
    result = test_complete_gluetun_integration()
    
    if result:
        print("\n🎉 Complete Gluetun integration test passed!")
        print("✅ All three requirements implemented successfully:")
        print("   1. Engines use Gluetun container name as host")
        print("   2. MAX_ACTIVE_REPLICAS limits concurrent instances")
        print("   3. P2P_PORT set from VPN port forwarding")
        sys.exit(0)
    else:
        print("\n❌ Complete integration test failed!")
        sys.exit(1)