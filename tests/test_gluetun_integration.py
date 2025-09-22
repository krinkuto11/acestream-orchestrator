#!/usr/bin/env python3
"""
Test Gluetun VPN integration functionality.
"""

import asyncio
import sys
import os
import time
from unittest.mock import Mock, MagicMock, patch

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_gluetun_configuration():
    """Test Gluetun configuration options."""
    print("\n🧪 Testing Gluetun configuration...")
    
    try:
        from app.core.config import cfg
        
        # Test that the new configuration options exist
        hasattr_gluetun_name = hasattr(cfg, 'GLUETUN_CONTAINER_NAME')
        hasattr_health_interval = hasattr(cfg, 'GLUETUN_HEALTH_CHECK_INTERVAL_S')
        hasattr_restart_engines = hasattr(cfg, 'VPN_RESTART_ENGINES_ON_RECONNECT')
        
        assert hasattr_gluetun_name, "GLUETUN_CONTAINER_NAME attribute should exist"
        assert hasattr_health_interval, "GLUETUN_HEALTH_CHECK_INTERVAL_S attribute should exist"
        assert hasattr_restart_engines, "VPN_RESTART_ENGINES_ON_RECONNECT attribute should exist"
        
        print("   ✅ All Gluetun configuration attributes exist")
        
        # Test default values
        assert cfg.GLUETUN_CONTAINER_NAME is None, "Default GLUETUN_CONTAINER_NAME should be None"
        assert cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S == 5, "Default health check interval should be 5s"
        assert cfg.VPN_RESTART_ENGINES_ON_RECONNECT == True, "Default restart engines should be True"
        
        print("   ✅ Configuration defaults are correct")
        
        # Test that validator is applied to the new timeout field
        try:
            from app.core.config import Cfg
            # This should work
            import os
            os.environ['GLUETUN_HEALTH_CHECK_INTERVAL_S'] = '10'
            test_cfg = Cfg()
            del os.environ['GLUETUN_HEALTH_CHECK_INTERVAL_S']
            print("   ✅ Valid timeout configuration works")
        except Exception as e:
            print(f"   ❌ Timeout validation failed: {e}")
            return False
        
        return True
        
    except Exception as e:
        print(f"   ❌ Configuration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_network_configuration():
    """Test network configuration logic for Gluetun."""
    print("\n🧪 Testing network configuration...")
    
    try:
        from app.services.provisioner import _get_network_config
        from app.core.config import cfg
        
        # Test without Gluetun (default behavior)
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        original_network = cfg.DOCKER_NETWORK
        
        cfg.GLUETUN_CONTAINER_NAME = None
        cfg.DOCKER_NETWORK = None
        
        config = _get_network_config()
        expected = {"network": None}
        assert config == expected, f"Expected {expected}, got {config}"
        print("   ✅ Default network configuration works")
        
        # Test with Docker network but no Gluetun
        cfg.DOCKER_NETWORK = "test-network"
        config = _get_network_config()
        expected = {"network": "test-network"}
        assert config == expected, f"Expected {expected}, got {config}"
        print("   ✅ Docker network configuration works")
        
        # Test with Gluetun (should override Docker network)
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        config = _get_network_config()
        expected = {"network_mode": "container:gluetun"}
        assert config == expected, f"Expected {expected}, got {config}"
        print("   ✅ Gluetun network mode configuration works")
        
        # Restore original values
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        cfg.DOCKER_NETWORK = original_network
        
        return True
        
    except Exception as e:
        print(f"   ❌ Network configuration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_gluetun_monitor():
    """Test Gluetun monitoring functionality."""
    print("\n🧪 Testing Gluetun monitor...")
    
    try:
        from app.services.gluetun import GluetunMonitor
        from app.core.config import cfg
        
        # Mock Docker client
        mock_docker_client = Mock()
        mock_container = Mock()
        mock_container.status = "running"
        mock_container.attrs = {
            "State": {
                "Health": {
                    "Status": "healthy"
                }
            }
        }
        mock_docker_client.containers.get.return_value = mock_container
        
        # Create monitor instance
        monitor = GluetunMonitor()
        
        # Test health check with mocked Gluetun
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = "test-gluetun"
        
        with patch('app.services.gluetun.get_client', return_value=mock_docker_client):
            health = await monitor._check_gluetun_health()
            assert health == True, "Healthy container should return True"
            print("   ✅ Healthy container check works")
            
            # Test unhealthy container
            mock_container.attrs["State"]["Health"]["Status"] = "unhealthy"
            health = await monitor._check_gluetun_health()
            assert health == False, "Unhealthy container should return False"
            print("   ✅ Unhealthy container check works")
            
            # Test container not running
            mock_container.status = "stopped"
            mock_container.attrs["State"]["Health"]["Status"] = "healthy"
            health = await monitor._check_gluetun_health()
            assert health == False, "Stopped container should return False"
            print("   ✅ Stopped container check works")
        
        # Test without Gluetun configured
        cfg.GLUETUN_CONTAINER_NAME = None
        health = await monitor.wait_for_healthy(timeout=1)
        assert health == True, "Should return True when no Gluetun configured"
        print("   ✅ No Gluetun configuration works")
        
        # Restore original value
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        return True
        
    except Exception as e:
        print(f"   ❌ Gluetun monitor test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_health_transition_callbacks():
    """Test health transition callback functionality."""
    print("\n🧪 Testing health transition callbacks...")
    
    try:
        from app.services.gluetun import GluetunMonitor
        
        monitor = GluetunMonitor()
        callback_calls = []
        
        def test_callback(old_status, new_status):
            callback_calls.append((old_status, new_status))
        
        async def test_async_callback(old_status, new_status):
            callback_calls.append(('async', old_status, new_status))
        
        monitor.add_health_transition_callback(test_callback)
        monitor.add_health_transition_callback(test_async_callback)
        
        # Simulate health transitions
        await monitor._handle_health_transition(True, False)
        await monitor._handle_health_transition(False, True)
        
        # Check callback calls
        expected_calls = [
            (True, False),
            ('async', True, False),
            (False, True),
            ('async', False, True)
        ]
        
        assert len(callback_calls) == 4, f"Expected 4 callback calls, got {len(callback_calls)}"
        print("   ✅ Health transition callbacks work")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Health transition callback test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_provisioner_gluetun_integration():
    """Test provisioner integration with Gluetun health checks."""
    print("\n🧪 Testing provisioner Gluetun integration...")
    
    try:
        # This test is more complex as it involves the actual provisioner
        # For now, we'll test the network configuration part
        from app.services.provisioner import _get_network_config
        from app.core.config import cfg
        
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        
        # Test that network configuration is applied correctly
        cfg.GLUETUN_CONTAINER_NAME = "test-gluetun"
        config = _get_network_config()
        
        assert "network_mode" in config, "Gluetun configuration should include network_mode"
        assert config["network_mode"] == "container:test-gluetun", "Network mode should reference Gluetun container"
        
        print("   ✅ Provisioner network configuration integration works")
        
        # Restore original value
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        return True
        
    except Exception as e:
        print(f"   ❌ Provisioner integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all Gluetun integration tests."""
    print("🧪 Testing Gluetun VPN Integration")
    print("=" * 60)
    
    tests = [
        ("Configuration", test_gluetun_configuration),
        ("Network Configuration", test_network_configuration),
        ("Gluetun Monitor", test_gluetun_monitor),
        ("Health Transitions", test_health_transition_callbacks),
        ("Provisioner Integration", test_provisioner_gluetun_integration),
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        print(f"\n📋 Running {test_name} test...")
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()
            results[test_name] = result
        except Exception as e:
            print(f"   💥 {test_name} test crashed: {e}")
            results[test_name] = False
    
    # Summary
    print(f"\n📊 Test Results Summary:")
    print("=" * 40)
    
    passed = sum(1 for result in results.values() if result)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"   {test_name}: {status}")
    
    print(f"\n🎯 Overall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All Gluetun integration tests passed!")
        print("🔧 Gluetun VPN integration is working correctly!")
        return True
    else:
        print("❌ Some Gluetun integration tests failed!")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)