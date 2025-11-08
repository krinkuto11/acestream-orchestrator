#!/usr/bin/env python3
"""
Test VPN recovery stabilization period to prevent premature engine provisioning.

This test validates the fix for the race condition where engines were provisioned
immediately after emergency mode exit, before the VPN forwarded port was available.
"""

import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock, patch

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_health_manager_waits_for_vpn_stabilization():
    """
    Test that health manager respects VPN recovery stabilization period.
    
    Scenario:
    1. VPN recovers from failure and exits emergency mode
    2. VPN is in recovery stabilization period (grace time to get forwarded port)
    3. Health manager should NOT provision engines during this period
    4. Health manager should wait for stabilization period to end
    """
    print("\n🧪 Testing VPN recovery stabilization period...")
    
    try:
        from app.services.health_manager import HealthManager
        from app.core.config import cfg
        
        # Mock redundant VPN mode
        original_vpn_mode = cfg.VPN_MODE
        original_vpn1 = cfg.GLUETUN_CONTAINER_NAME
        original_vpn2 = cfg.GLUETUN_CONTAINER_NAME_2
        
        cfg.VPN_MODE = 'redundant'
        cfg.GLUETUN_CONTAINER_NAME = 'gluetun1'
        cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun2'
        
        # Create health manager instance
        health_manager = HealthManager(check_interval=20)
        
        # Mock gluetun_monitor (imported inside the method)
        with patch('app.services.gluetun.gluetun_monitor') as mock_gluetun:
            # Create mock VPN monitors
            vpn1_monitor = Mock()
            vpn2_monitor = Mock()
            
            # VPN1 is healthy but in recovery stabilization period
            vpn1_monitor.is_in_recovery_stabilization_period.return_value = True
            # VPN2 is healthy and stable
            vpn2_monitor.is_in_recovery_stabilization_period.return_value = False
            
            mock_gluetun.is_healthy.side_effect = lambda vpn: True  # Both VPNs are healthy
            mock_gluetun.get_vpn_monitor.side_effect = lambda vpn: (
                vpn1_monitor if vpn == 'gluetun1' else vpn2_monitor
            )
            
            # Simulate healthy engines list (fewer than MIN_REPLICAS)
            healthy_engines = [Mock() for _ in range(5)]  # Simulate 5 healthy engines
            
            # Test: Should wait for VPN1 stabilization
            should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
            
            assert should_wait is True, (
                "Health manager should wait when VPN is in recovery stabilization period"
            )
            print("   ✅ Health manager correctly waits for VPN1 stabilization")
            
            # Now test with VPN2 in stabilization period
            vpn1_monitor.is_in_recovery_stabilization_period.return_value = False
            vpn2_monitor.is_in_recovery_stabilization_period.return_value = True
            
            should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
            
            assert should_wait is True, (
                "Health manager should wait when VPN2 is in recovery stabilization period"
            )
            print("   ✅ Health manager correctly waits for VPN2 stabilization")
            
            # Test: Both VPNs stable - should NOT wait
            vpn1_monitor.is_in_recovery_stabilization_period.return_value = False
            vpn2_monitor.is_in_recovery_stabilization_period.return_value = False
            
            should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
            
            assert should_wait is False, (
                "Health manager should proceed when both VPNs are stable"
            )
            print("   ✅ Health manager correctly proceeds when VPNs are stable")
        
        # Restore original config
        cfg.VPN_MODE = original_vpn_mode
        cfg.GLUETUN_CONTAINER_NAME = original_vpn1
        cfg.GLUETUN_CONTAINER_NAME_2 = original_vpn2
        
        return True
        
    except Exception as e:
        print(f"   ❌ VPN recovery stabilization test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_vpn_monitor_recovery_period_tracking():
    """
    Test that VPN monitor correctly tracks recovery stabilization period.
    """
    print("\n🧪 Testing VPN monitor recovery period tracking...")
    
    try:
        from app.services.gluetun import VpnContainerMonitor
        from datetime import datetime, timezone, timedelta
        
        # Create a VPN monitor
        monitor = VpnContainerMonitor("test-vpn")
        
        # Initially, should not be in recovery period
        assert not monitor.is_in_recovery_stabilization_period(), (
            "Should not be in recovery period initially"
        )
        print("   ✅ Initially not in recovery period")
        
        # Simulate VPN recovery by setting recovery time
        monitor._last_recovery_time = datetime.now(timezone.utc)
        
        # Should now be in recovery period
        assert monitor.is_in_recovery_stabilization_period(), (
            "Should be in recovery period immediately after recovery"
        )
        print("   ✅ Correctly detects recovery period")
        
        # Simulate time passing (less than stabilization period)
        monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert monitor.is_in_recovery_stabilization_period(), (
            "Should still be in recovery period after 60s"
        )
        print("   ✅ Still in recovery period after 60s")
        
        # Simulate stabilization period expiring (default is 120s)
        monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=121)
        assert not monitor.is_in_recovery_stabilization_period(), (
            "Should not be in recovery period after 121s"
        )
        print("   ✅ Recovery period correctly expires after 121s")
        
        return True
        
    except Exception as e:
        print(f"   ❌ VPN monitor recovery tracking test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_integration_scenario():
    """
    Test the complete integration scenario from the bug report.
    
    Scenario from vpn_exit.log:
    1. VPN exits emergency mode at 00:23:06.436
    2. Health manager should wait for VPN stabilization
    3. Port forwarding established at 00:23:19.348 (~13s later)
    4. Only then should engines be provisioned
    """
    print("\n🧪 Testing complete integration scenario...")
    
    try:
        from app.services.gluetun import VpnContainerMonitor
        from app.services.health_manager import HealthManager
        from app.core.config import cfg
        
        # Mock redundant VPN mode
        original_vpn_mode = cfg.VPN_MODE
        original_vpn1 = cfg.GLUETUN_CONTAINER_NAME
        original_vpn2 = cfg.GLUETUN_CONTAINER_NAME_2
        
        cfg.VPN_MODE = 'redundant'
        cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
        cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun2'
        
        # Create VPN monitor and mark it as just recovered
        vpn_monitor = VpnContainerMonitor('gluetun')
        vpn_monitor._last_recovery_time = datetime.now(timezone.utc)
        
        # Create health manager
        health_manager = HealthManager(check_interval=20)
        
        with patch('app.services.gluetun.gluetun_monitor') as mock_gluetun:
            # Mock both VPNs as healthy
            mock_gluetun.is_healthy.return_value = True
            mock_gluetun.get_vpn_monitor.return_value = vpn_monitor
            
            # Simulate 5 healthy engines (less than MIN_REPLICAS of 10)
            healthy_engines = [Mock() for _ in range(5)]
            
            # Test: Immediately after recovery, should wait
            should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
            
            assert should_wait is True, (
                "Should wait immediately after VPN recovery"
            )
            print("   ✅ Health manager waits immediately after VPN recovery")
            
            # Simulate time passing to just before stabilization period ends
            vpn_monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=119)
            should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
            
            assert should_wait is True, (
                "Should still wait at 119s (1s before period ends)"
            )
            print("   ✅ Health manager continues waiting near end of period")
            
            # Simulate stabilization period ending
            vpn_monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=121)
            should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
            
            assert should_wait is False, (
                "Should proceed after stabilization period ends"
            )
            print("   ✅ Health manager proceeds after stabilization period")
        
        # Restore original config
        cfg.VPN_MODE = original_vpn_mode
        cfg.GLUETUN_CONTAINER_NAME = original_vpn1
        cfg.GLUETUN_CONTAINER_NAME_2 = original_vpn2
        
        print("   ✅ Integration scenario validated successfully")
        return True
        
    except Exception as e:
        print(f"   ❌ Integration scenario test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print("=" * 70)
    print("VPN Recovery Stabilization Period Tests")
    print("=" * 70)
    
    results = []
    results.append(("VPN Recovery Stabilization", test_health_manager_waits_for_vpn_stabilization()))
    results.append(("VPN Monitor Recovery Tracking", test_vpn_monitor_recovery_period_tracking()))
    results.append(("Integration Scenario", test_integration_scenario()))
    
    print("\n" + "=" * 70)
    print("Test Summary:")
    print("=" * 70)
    
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")
    
    all_passed = all(passed for _, passed in results)
    
    if all_passed:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)
