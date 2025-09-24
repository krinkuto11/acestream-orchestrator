#!/usr/bin/env python3
"""
Test to validate the Gluetun startup fix that prevents container restart cycles.

This test ensures that the fix for the issue described in the logs works correctly:
- Prevents engine restarts during initial Gluetun health transitions
- Allows restarts only for legitimate VPN reconnections after stable operation
- Reduces container churn and maintains system stability during startup
"""

import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_gluetun_startup_grace_period():
    """Test that the startup grace period logic works correctly."""
    from app.services.gluetun import GluetunMonitor
    
    monitor = GluetunMonitor()
    now = datetime.now(timezone.utc)
    
    # Test 1: No first healthy time (initial startup)
    should_restart = monitor._should_restart_engines_on_reconnection(now)
    assert not should_restart, "Should not restart during initial startup"
    
    # Test 2: Within grace period
    monitor._first_healthy_time = now - timedelta(seconds=30)  # 30 seconds ago
    monitor._consecutive_healthy_count = 10
    should_restart = monitor._should_restart_engines_on_reconnection(now)
    assert not should_restart, "Should not restart within grace period"
    
    # Test 3: After grace period but insufficient stability
    monitor._first_healthy_time = now - timedelta(seconds=120)  # 2 minutes ago
    monitor._consecutive_healthy_count = 2  # Only 2 healthy checks
    should_restart = monitor._should_restart_engines_on_reconnection(now)
    assert not should_restart, "Should not restart with insufficient stability"
    
    # Test 4: After grace period with sufficient stability
    monitor._first_healthy_time = now - timedelta(seconds=120)  # 2 minutes ago
    monitor._consecutive_healthy_count = 10  # 10 healthy checks
    should_restart = monitor._should_restart_engines_on_reconnection(now)
    assert should_restart, "Should restart after grace period with stability"
    
    print("✅ All Gluetun grace period logic tests passed!")

async def test_gluetun_prevents_startup_restarts():
    """
    Integration test that simulates startup scenario and validates no restarts occur.
    """
    from app.services.gluetun import GluetunMonitor
    from app.core.config import cfg
    
    # Configure for test
    original_gluetun_container = cfg.GLUETUN_CONTAINER_NAME
    original_restart_engines = cfg.VPN_RESTART_ENGINES_ON_RECONNECT
    original_health_interval = cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S
    
    cfg.GLUETUN_CONTAINER_NAME = "gluetun"
    cfg.VPN_RESTART_ENGINES_ON_RECONNECT = True
    cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S = 1
    
    try:
        restart_calls = []
        
        def mock_restart_engines():
            restart_calls.append(datetime.now(timezone.utc))
        
        # Simulate problematic startup sequence
        health_checks = 0
        def mock_gluetun_container():
            nonlocal health_checks
            health_checks += 1
            
            mock_container = Mock()
            mock_container.status = "running"
            
            # Simulate: unhealthy -> healthy -> unhealthy -> healthy (startup transitions)
            if health_checks <= 2:
                health_status = "unhealthy"
            elif health_checks == 3:
                health_status = "healthy"  # First healthy - used to trigger restart
            elif health_checks == 4:
                health_status = "unhealthy"  # Brief disconnection
            else:
                health_status = "healthy"  # Recovery - used to trigger restart
            
            mock_container.attrs = {"State": {"Health": {"Status": health_status}}}
            return mock_container
        
        monitor = GluetunMonitor()
        
        with patch('app.services.gluetun.get_client') as mock_get_client:
            with patch.object(monitor, '_restart_acestream_engines', side_effect=mock_restart_engines):
                
                mock_cli = Mock()
                mock_cli.containers.get.side_effect = lambda name: mock_gluetun_container()
                mock_get_client.return_value = mock_cli
                
                # Run monitoring for startup period
                await monitor.start()
                await asyncio.sleep(6)  # Let health transitions occur
                await monitor.stop()
                
                # Validate fix effectiveness
                assert len(restart_calls) == 0, f"Expected 0 restarts during startup, got {len(restart_calls)}"
                assert health_checks >= 5, "Should have performed multiple health checks"
                
        print("✅ Gluetun startup restart prevention test passed!")
        
    finally:
        # Restore configuration
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun_container
        cfg.VPN_RESTART_ENGINES_ON_RECONNECT = original_restart_engines
        cfg.GLUETUN_HEALTH_CHECK_INTERVAL_S = original_health_interval

if __name__ == "__main__":
    print("🧪 Running Gluetun startup fix tests...")
    
    # Test the logic
    test_gluetun_startup_grace_period()
    
    # Test the integration
    asyncio.run(test_gluetun_prevents_startup_restarts())
    
    print("🎉 All Gluetun startup fix tests passed!")