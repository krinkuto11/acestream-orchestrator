#!/usr/bin/env python3
"""
Simple integration test to verify port change does NOT trigger stabilization period.

This test validates the fix for the issue where port changes incorrectly
triggered recovery stabilization periods.
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from unittest.mock import Mock, patch

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


async def test_port_change_no_stabilization():
    """Test that port change does NOT set recovery stabilization period."""
    from app.services.gluetun import GluetunMonitor, VpnContainerMonitor
    from app.core.config import cfg
    
    print("\n🧪 Testing port change does NOT trigger stabilization...")
    
    # Create a VPN monitor
    monitor = VpnContainerMonitor("gluetun")
    
    # Verify no recovery time is set initially
    assert monitor._last_recovery_time is None, "No recovery time should be set initially"
    print("   ✅ Initially no recovery time set")
    
    # Check that monitor is NOT in stabilization period
    in_stabilization = monitor.is_in_recovery_stabilization_period()
    assert in_stabilization is False, "Should not be in stabilization period initially"
    print("   ✅ Not in stabilization period initially")
    
    # Simulate a port change by directly calling the handler with mocked state
    mock_state = Mock()
    mock_state.get_forwarded_engine.return_value = None  # No forwarded engine
    mock_state.remove_engine = Mock()
    
    mock_state_module = Mock()
    mock_state_module.state = mock_state
    
    original_state = sys.modules.get('app.services.state')
    
    try:
        sys.modules['app.services.state'] = mock_state_module
        
        # Create GluetunMonitor and call port change handler
        with patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
            gluetun_monitor = GluetunMonitor()
            vpn_monitor = gluetun_monitor.get_vpn_monitor("gluetun")
            
            # Trigger port change
            await gluetun_monitor._handle_port_change("gluetun", 12345, 67890)
            
            # Verify recovery time was NOT set
            assert vpn_monitor._last_recovery_time is None, "Recovery time should NOT be set after port change"
            print("   ✅ Recovery time NOT set after port change")
            
            # Verify NOT in stabilization period
            in_stabilization = vpn_monitor.is_in_recovery_stabilization_period()
            assert in_stabilization is False, "Should NOT be in stabilization after port change"
            print("   ✅ Not in stabilization period after port change")
    
    finally:
        if original_state:
            sys.modules['app.services.state'] = original_state


async def test_health_recovery_does_set_stabilization():
    """Test that health recovery DOES set recovery stabilization period."""
    from app.services.gluetun import GluetunMonitor
    from app.core.config import cfg
    
    print("\n🧪 Testing health recovery DOES trigger stabilization...")
    
    with patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
        with patch.object(cfg, 'VPN_MODE', 'single'):
            gluetun_monitor = GluetunMonitor()
            vpn_monitor = gluetun_monitor.get_vpn_monitor("gluetun")
            
            # Initially healthy
            vpn_monitor._last_health_status = True
            
            # Verify no recovery time is set initially
            assert vpn_monitor._last_recovery_time is None, "No recovery time initially"
            print("   ✅ Initially no recovery time set")
            
            # Simulate health transition from healthy to unhealthy
            await gluetun_monitor._handle_health_transition("gluetun", True, False)
            
            # Still no recovery time (not recovered yet)
            assert vpn_monitor._last_recovery_time is None, "No recovery time when becoming unhealthy"
            print("   ✅ No recovery time when becoming unhealthy")
            
            # Simulate health transition from unhealthy to healthy (recovery)
            await gluetun_monitor._handle_health_transition("gluetun", False, True)
            
            # NOW recovery time should be set
            assert vpn_monitor._last_recovery_time is not None, "Recovery time SHOULD be set after health recovery"
            print("   ✅ Recovery time IS set after health recovery")
            
            # Verify in stabilization period
            in_stabilization = vpn_monitor.is_in_recovery_stabilization_period()
            assert in_stabilization is True, "Should be in stabilization after health recovery"
            print("   ✅ In stabilization period after health recovery")


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


if __name__ == "__main__":
    print("\n" + "="*70)
    print("Testing Port Change vs Health Recovery Stabilization")
    print("="*70)
    
    try:
        run_async_test(test_port_change_no_stabilization())
        run_async_test(test_health_recovery_does_set_stabilization())
        
        print("\n" + "="*70)
        print("✅ All tests PASSED!")
        print("="*70 + "\n")
    except AssertionError as e:
        print(f"\n❌ Test FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
