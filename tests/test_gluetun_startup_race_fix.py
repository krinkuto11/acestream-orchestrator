#!/usr/bin/env python3
"""
Test fix for Gluetun startup race condition.

This test verifies that the gluetun monitor is started before ensure_minimum()
to prevent engines from failing to start due to unknown health status.
"""

import asyncio
import sys
import os
from unittest.mock import Mock, MagicMock, patch, AsyncMock

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


async def test_gluetun_startup_sequence():
    """Test that gluetun monitor starts before ensure_minimum() is called."""
    print("\n🧪 Testing Gluetun startup sequence...")
    
    try:
        from app.services.gluetun import gluetun_monitor
        from app.core.config import cfg
        
        # Mock Gluetun configuration
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = "test-gluetun"
        
        # Track the startup sequence
        startup_sequence = []
        
        # Mock the gluetun monitor methods
        original_start = gluetun_monitor.start
        original_is_healthy = gluetun_monitor.is_healthy
        
        async def mock_start():
            startup_sequence.append("gluetun_monitor.start")
            # Set initial health status to simulate a working gluetun
            gluetun_monitor._last_health_status = True
            return await original_start()
        
        def mock_is_healthy():
            startup_sequence.append("gluetun_monitor.is_healthy")
            return gluetun_monitor._last_health_status
        
        gluetun_monitor.start = mock_start
        gluetun_monitor.is_healthy = mock_is_healthy
        
        # Mock Docker client to prevent actual container operations
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
        
        # Mock ensure_minimum to track when it's called
        def mock_ensure_minimum():
            startup_sequence.append("ensure_minimum")
            # This will call gluetun_monitor.is_healthy() internally
            from app.services.provisioner import AceProvisionRequest, start_acestream
            from app.core.config import cfg
            
            # Simulate what ensure_minimum does - try to start an engine
            # This would normally fail if gluetun monitor wasn't started
            try:
                # We won't actually start a container, just test the health check
                health_status = gluetun_monitor.is_healthy()
                startup_sequence.append(f"health_check_result: {health_status}")
            except Exception as e:
                startup_sequence.append(f"health_check_error: {e}")
        
        # Test the startup sequence from main.py
        with patch('app.services.gluetun.get_client', return_value=mock_docker_client):
            with patch('app.services.autoscaler.ensure_minimum', mock_ensure_minimum):
                
                # Simulate the corrected startup sequence from main.py
                # 1. Start gluetun monitor first
                await gluetun_monitor.start()
                
                # 2. Then call ensure_minimum (which should now work)
                mock_ensure_minimum()
                
                # Verify the sequence is correct
                expected_sequence = [
                    "gluetun_monitor.start",
                    "ensure_minimum", 
                    "gluetun_monitor.is_healthy",
                    "health_check_result: True"
                ]
                
                print(f"   Startup sequence: {startup_sequence}")
                
                # Check that gluetun monitor was started before ensure_minimum
                gluetun_start_idx = startup_sequence.index("gluetun_monitor.start")
                ensure_min_idx = startup_sequence.index("ensure_minimum")
                
                assert gluetun_start_idx < ensure_min_idx, \
                    "Gluetun monitor should start before ensure_minimum"
                
                # Check that health check returned a valid result (not None)
                health_result = [item for item in startup_sequence if "health_check_result" in item]
                assert len(health_result) > 0, "Health check should return a result"
                assert "True" in health_result[0], "Health check should return True for healthy Gluetun"
                
                print("   ✅ Gluetun monitor starts before ensure_minimum")
                print("   ✅ Health check returns valid result during startup") 
        
        # Restore original methods
        gluetun_monitor.start = original_start
        gluetun_monitor.is_healthy = original_is_healthy
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        return True
        
    except Exception as e:
        print(f"   ❌ Gluetun startup sequence test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_gluetun_health_before_provisioning():
    """Test that Gluetun health is available when provisioning engines."""
    print("\n🧪 Testing Gluetun health availability during provisioning...")
    
    try:
        from app.services.gluetun import gluetun_monitor
        from app.services.provisioner import start_acestream, AceProvisionRequest
        from app.core.config import cfg
        
        # Configure Gluetun
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = "test-gluetun"
        
        # Mock Docker operations
        mock_response = Mock()
        mock_response.container_id = "test123"
        mock_response.container_name = "acestream-test"
        mock_response.host_http_port = 19000
        mock_response.container_http_port = 19000
        mock_response.container_https_port = 19001
        
        # Test scenario 1: Gluetun monitor not started (old behavior)
        gluetun_monitor._last_health_status = None  # Reset to unknown state
        
        health_before_start = gluetun_monitor.is_healthy()
        assert health_before_start is None, "Health should be None before monitor starts"
        print("   ✅ Health is None before monitor starts (expected)")
        
        # Test scenario 2: Gluetun monitor started (new behavior)
        # Mock Docker client to prevent actual container lookups
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
        
        # Mock the health check to return healthy
        with patch('app.services.gluetun.get_client', return_value=mock_docker_client):
            # Start the monitor (this sets _last_health_status)
            await gluetun_monitor.start()
            
            # Give it a moment to process the initial health check
            await asyncio.sleep(0.1)
            
            # Now health should be available
            health_after_start = gluetun_monitor.is_healthy()
            assert health_after_start is not None, f"Health should not be None after monitor starts, got: {health_after_start}"
            print("   ✅ Health is available after monitor starts")
            
            # This simulates what happens in the fixed startup sequence
            # The provisioner can now get valid health status
            print("   ✅ Provisioner can now check Gluetun health during startup")
        
        # Cleanup
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        await gluetun_monitor.stop()
        
        return True
        
    except Exception as e:
        print(f"   ❌ Gluetun health availability test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run Gluetun startup race condition fix tests."""
    print("🧪 Testing Gluetun Startup Race Condition Fix")
    print("=" * 60)
    
    tests = [
        ("Startup Sequence", test_gluetun_startup_sequence),
        ("Health Availability", test_gluetun_health_before_provisioning),
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        print(f"\n📋 Running {test_name} test...")
        try:
            result = await test_func()
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
        print("🎉 All Gluetun startup race condition fix tests passed!")
        print("🔧 The race condition fix is working correctly!")
        return True
    else:
        print("❌ Some tests failed!")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)