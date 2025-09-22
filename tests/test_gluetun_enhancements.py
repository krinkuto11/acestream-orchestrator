#!/usr/bin/env python3
"""
Test the new Gluetun enhancements:
1. Engines use Gluetun container name as host
2. MAX_ACTIVE_REPLICAS limits instances  
3. P2P_PORT is set from Gluetun port forwarding
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_host_configuration():
    """Test that engines use Gluetun container name as host when Gluetun is enabled."""
    print("\n🧪 Testing host configuration...")
    
    try:
        from app.services.reindex import reindex_existing
        from app.core.config import cfg
        from app.services.state import state
        
        # Test without Gluetun
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = None
        
        # Mock a container without Gluetun
        mock_container = MagicMock()
        mock_container.id = "test_container_id"
        mock_container.status = "running"
        mock_container.labels = {"acestream.http_port": "19001", "host.http_port": "19001"}
        mock_container.attrs = {
            "Name": "/test-container",
            "NetworkSettings": {"Ports": {"19001/tcp": [{"HostPort": "19001"}]}}
        }
        
        with patch('app.services.health.list_managed', return_value=[mock_container]):
            with patch('app.services.inspect.get_container_name', return_value="test-container"):
                with patch('app.services.ports.alloc.reserve_http'):
                    with patch('app.services.ports.alloc.reserve_host'):
                        with patch('app.services.state.state.now', return_value=datetime.now()):
                            # Clear existing state
                            if not hasattr(state, 'engines') or state.engines is None:
                                state.engines = {}
                            state.engines.clear()
                            print(f"   Debug: Before reindex, engines: {state.engines}")
                            print(f"   Debug: Mock container: {mock_container.id}, status: {mock_container.status}")
                            reindex_existing()
                            print(f"   Debug: After reindex, engines: {state.engines}")
                            
                            engine = state.engines.get("test_container_id")
                            if engine is None:
                                print(f"   Debug: Available keys: {list(state.engines.keys())}")
                            assert engine is not None, "Engine should be in state"
                            assert engine.host == "test-container", f"Expected host 'test-container', got '{engine.host}'"
                            print("   ✅ Without Gluetun: Engine uses container name as host")
        
        # Test with Gluetun
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        with patch('app.services.health.list_managed', return_value=[mock_container]):
            with patch('app.services.inspect.get_container_name', return_value="test-container"):
                with patch('app.services.ports.alloc.reserve_gluetun_port'):
                    # Clear existing state
                    if not hasattr(state, 'engines') or state.engines is None:
                        state.engines = {}
                    state.engines.clear()
                    reindex_existing()
                    
                    engine = state.engines.get("test_container_id")
                    assert engine is not None, "Engine should be in state"
                    assert engine.host == "gluetun", f"Expected host 'gluetun', got '{engine.host}'"
                    print("   ✅ With Gluetun: Engine uses Gluetun container name as host")
        
        # Restore original value
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        return True
        
    except Exception as e:
        print(f"   ❌ Host configuration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_max_active_replicas():
    """Test MAX_ACTIVE_REPLICAS configuration and port allocation."""
    print("\n🧪 Testing MAX_ACTIVE_REPLICAS...")
    
    try:
        from app.core.config import cfg
        from app.services.ports import PortAllocator
        
        # Test configuration validation
        original_max_active = cfg.MAX_ACTIVE_REPLICAS
        
        # Create a new allocator to test port limits
        allocator = PortAllocator()
        
        # Set a low limit for testing
        cfg.MAX_ACTIVE_REPLICAS = 3
        
        # Allocate up to the limit
        ports = []
        for i in range(3):
            port = allocator.alloc_gluetun_port()
            ports.append(port)
            assert 19000 <= port < 19000 + cfg.MAX_ACTIVE_REPLICAS, f"Port {port} not in expected range"
        
        print(f"   ✅ Allocated {len(ports)} ports: {ports}")
        
        # Try to allocate one more - should fail
        try:
            allocator.alloc_gluetun_port()
            print("   ❌ Should have failed to allocate beyond limit")
            return False
        except RuntimeError as e:
            if "Maximum active replicas limit reached" in str(e):
                print("   ✅ Correctly rejected allocation beyond MAX_ACTIVE_REPLICAS")
            else:
                print(f"   ❌ Unexpected error: {e}")
                return False
        
        # Free a port and try again
        allocator.free_gluetun_port(ports[0])
        new_port = allocator.alloc_gluetun_port()
        print(f"   ✅ Successfully allocated port {new_port} after freeing one")
        
        # Restore original value
        cfg.MAX_ACTIVE_REPLICAS = original_max_active
        
        return True
        
    except Exception as e:
        print(f"   ❌ MAX_ACTIVE_REPLICAS test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_p2p_port_integration():
    """Test P2P_PORT environment variable integration with Gluetun."""
    print("\n🧪 Testing P2P_PORT integration...")
    
    try:
        from app.services.provisioner import start_acestream, AceProvisionRequest
        from app.core.config import cfg
        
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        
        # Test without Gluetun - should not have P2P_PORT
        cfg.GLUETUN_CONTAINER_NAME = None
        
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.id = "test_container_no_gluetun"
        mock_container.attrs = {"Name": "/test-container-no-gluetun"}
        
        with patch('app.services.provisioner.get_client') as mock_client:
            mock_client.return_value.containers.run.return_value = mock_container
            with patch('app.services.provisioner.safe', side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)):
                with patch('app.services.naming.generate_container_name', return_value="test-acestream"):
                    with patch('app.services.provisioner.alloc.alloc_host', return_value=19001):
                        with patch('app.services.provisioner.alloc.alloc_https', return_value=19002):
                            req = AceProvisionRequest()
                            start_acestream(req)
                            
                            # Check that P2P_PORT was not set
                            call_args, call_kwargs = mock_client.return_value.containers.run.call_args
                            env = call_kwargs.get('environment', {})
                            assert 'P2P_PORT' not in env, f"P2P_PORT should not be set without Gluetun, but env is: {env}"
                            print("   ✅ Without Gluetun: P2P_PORT not set")
        
        # Test with Gluetun - should have P2P_PORT
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        mock_container_gluetun = MagicMock()
        mock_container_gluetun.status = "running"
        mock_container_gluetun.id = "test_container_gluetun"
        mock_container_gluetun.attrs = {"Name": "/test-container-gluetun"}
        
        with patch('app.services.provisioner.get_client') as mock_client:
            mock_client.return_value.containers.run.return_value = mock_container_gluetun
            with patch('app.services.provisioner.safe', side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)):
                with patch('app.services.naming.generate_container_name', return_value="test-acestream-gluetun"):
                    with patch('app.services.provisioner.alloc.alloc_gluetun_port', side_effect=[19001, 19002]):
                        with patch('app.services.provisioner._check_gluetun_health_sync', return_value=True):
                            with patch('app.services.gluetun.get_forwarded_port_sync', return_value=5914):
                                with patch('app.services.gluetun.gluetun_monitor') as mock_monitor:
                                    mock_monitor.is_healthy.return_value = True
                                    req = AceProvisionRequest()
                                    start_acestream(req)
                                    
                                    # Check that P2P_PORT was set
                                    call_args, call_kwargs = mock_client.return_value.containers.run.call_args
                                    env = call_kwargs.get('environment', {})
                                    assert 'P2P_PORT' in env, f"P2P_PORT should be set with Gluetun, but env is: {env}"
                                    assert env['P2P_PORT'] == '5914', f"Expected P2P_PORT=5914, got {env.get('P2P_PORT')}"
                                    print("   ✅ With Gluetun: P2P_PORT correctly set to 5914")
        
        # Restore original value
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        return True
        
    except Exception as e:
        print(f"   ❌ P2P_PORT integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🔧 Testing Gluetun Enhancements")
    print("=" * 60)
    
    test1_result = test_host_configuration()
    test2_result = test_max_active_replicas()
    test3_result = test_p2p_port_integration()
    
    if test1_result and test2_result and test3_result:
        print("\n🎉 All Gluetun enhancement tests passed!")
        print("✅ Host configuration works correctly")
        print("✅ MAX_ACTIVE_REPLICAS limits work correctly")
        print("✅ P2P_PORT integration works correctly")
        sys.exit(0)
    else:
        print("\n❌ Some Gluetun enhancement tests failed!")
        sys.exit(1)