#!/usr/bin/env python3
"""
Test to verify that when Gluetun is enabled, engines use 'localhost' as host
instead of container name for communication.
"""

import sys
import os

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_host_configuration_with_gluetun():
    """Test that engines use localhost when Gluetun is enabled."""
    print("🧪 Testing host configuration with Gluetun VPN...")
    
    try:
        from app.services.reindex import reindex_existing
        from app.services.state import state
        from app.core.config import cfg
        from unittest.mock import Mock, patch
        
        # Clear existing state
        state.engines.clear()
        
        # Mock a Docker container
        mock_container = Mock()
        mock_container.id = "test_container_123"
        mock_container.status = "running"
        mock_container.labels = {
            "orchestrator.managed": "acestream",
            "acestream.http_port": "40001",
            "host.http_port": "19001"
        }
        mock_container.attrs = {
            "NetworkSettings": {
                "Ports": {
                    "40001/tcp": [{"HostPort": "19001"}]
                }
            }
        }
        
        # Test WITHOUT Gluetun
        print("\n📋 Test 1: Without Gluetun (should use container name as host)")
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = None
        
        with patch('app.services.reindex.list_managed', return_value=[mock_container]):
            with patch('app.services.reindex.get_container_name', return_value="test-container-name"):
                reindex_existing()
        
        # Check that engine was created with container name as host
        assert len(state.engines) == 1, f"Expected 1 engine, got {len(state.engines)}"
        engine = list(state.engines.values())[0]
        expected_host_without_gluetun = "test-container-name"
        assert engine.host == expected_host_without_gluetun, f"Without Gluetun, expected host '{expected_host_without_gluetun}', got '{engine.host}'"
        print(f"   ✅ Host without Gluetun: '{engine.host}' (correctly uses container name)")
        
        # Clear state for next test
        state.engines.clear()
        
        # Test WITH Gluetun
        print("\n📋 Test 2: With Gluetun (should use localhost as host)")
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        with patch('app.services.reindex.list_managed', return_value=[mock_container]):
            with patch('app.services.reindex.get_container_name', return_value="test-container-name"):
                reindex_existing()
        
        # Check that engine was created with localhost as host
        assert len(state.engines) == 1, f"Expected 1 engine, got {len(state.engines)}"
        engine = list(state.engines.values())[0]
        expected_host_with_gluetun = "localhost"
        assert engine.host == expected_host_with_gluetun, f"With Gluetun, expected host '{expected_host_with_gluetun}', got '{engine.host}'"
        print(f"   ✅ Host with Gluetun: '{engine.host}' (correctly uses localhost)")
        
        # Restore original value
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        print("\n🎉 Host configuration test passed!")
        print("✅ Without Gluetun: Uses container name for inter-container communication")
        print("✅ With Gluetun: Uses localhost for containers sharing VPN network stack")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_edge_cases():
    """Test edge cases for host configuration."""
    print("\n🧪 Testing edge cases for host configuration...")
    
    try:
        from app.services.reindex import reindex_existing
        from app.services.state import state
        from app.core.config import cfg
        from unittest.mock import Mock, patch
        
        # Clear existing state
        state.engines.clear()
        
        # Mock a container without a retrievable name
        mock_container = Mock()
        mock_container.id = "test_container_456"
        mock_container.status = "running"
        mock_container.labels = {
            "orchestrator.managed": "acestream",
            "acestream.http_port": "40002",
            "host.http_port": "19002"
        }
        mock_container.attrs = {
            "NetworkSettings": {
                "Ports": {
                    "40002/tcp": [{"HostPort": "19002"}]
                }
            }
        }
        
        # Test WITH Gluetun but no container name available
        print("\n📋 Test 3: With Gluetun, no container name (should still use localhost)")
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        with patch('app.services.reindex.list_managed', return_value=[mock_container]):
            with patch('app.services.reindex.get_container_name', return_value=None):  # No name available
                reindex_existing()
        
        # Check that engine was created with localhost as host even without container name
        assert len(state.engines) == 1, f"Expected 1 engine, got {len(state.engines)}"
        engine = list(state.engines.values())[0]
        expected_host = "localhost"
        assert engine.host == expected_host, f"With Gluetun (no container name), expected host '{expected_host}', got '{engine.host}'"
        print(f"   ✅ Host with Gluetun (no container name): '{engine.host}' (correctly uses localhost)")
        
        # Clear state
        state.engines.clear()
        
        # Test WITHOUT Gluetun and no container name (should fallback to truncated container ID)
        print("\n📋 Test 4: Without Gluetun, no container name (should use truncated container ID)")
        cfg.GLUETUN_CONTAINER_NAME = None
        
        with patch('app.services.reindex.list_managed', return_value=[mock_container]):
            with patch('app.services.reindex.get_container_name', return_value=None):  # No name available
                reindex_existing()
        
        # Check fallback behavior
        assert len(state.engines) == 1, f"Expected 1 engine, got {len(state.engines)}"
        engine = list(state.engines.values())[0]
        expected_host = "container-test_contain"  # Truncated container ID is used as name and then host
        assert engine.host == expected_host, f"Without Gluetun (no container name), expected host '{expected_host}', got '{engine.host}'"
        print(f"   ✅ Host without Gluetun (no container name): '{engine.host}' (correctly uses truncated container ID)")
        
        # Restore original value
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        print("\n🎉 Edge case tests passed!")
        
        return True
        
    except Exception as e:
        print(f"❌ Edge case test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🔧 Testing Gluetun Host Configuration Fix")
    print("=" * 60)
    
    test1_result = test_host_configuration_with_gluetun()
    test2_result = test_edge_cases()
    
    if test1_result and test2_result:
        print("\n🎉 All host configuration tests passed!")
        print("🔧 Gluetun host configuration fix is working correctly!")
        sys.exit(0)
    else:
        print("\n❌ Some host configuration tests failed!")
        sys.exit(1)