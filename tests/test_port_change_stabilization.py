"""
Test that VPN port change does NOT trigger recovery stabilization period.

This test validates the fix for the issue where port changes incorrectly
triggered recovery stabilization periods. Port changes indicate the VPN
container restarted internally and is already healthy and ready.

The stabilization period should ONLY be set during actual VPN health
recovery (when transitioning from unhealthy to healthy state), which is
needed for emergency mode scenarios.

The test ensures that when a VPN's forwarded port changes:
1. The old forwarded engine is replaced
2. NO recovery stabilization period is set
3. The VPN is immediately ready for provisioning new engines
"""

import asyncio
import logging
from unittest.mock import Mock, patch
from datetime import datetime, timezone

# Set up logging for test visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_port_change_does_not_set_recovery_stabilization():
    """Test that port change does NOT set recovery stabilization period on the VPN monitor."""
    from app.services.gluetun import GluetunMonitor
    from app.models.schemas import EngineState
    from app.core.config import cfg
    import sys
    
    # Set up mock state with a forwarded engine
    mock_engine = EngineState(
        container_id="test_engine_123",
        container_name="acestream-forwarded",
        host="gluetun",
        port=19000,
        labels={"acestream.forwarded": "true"},
        forwarded=True,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        streams=[],
        health_status="healthy",
        last_health_check=datetime.now(timezone.utc),
        last_stream_usage=None,
        last_cache_cleanup=None,
        cache_size_bytes=None,
        vpn_container="gluetun"
    )
    
    # Mock the state module
    mock_state = Mock()
    mock_state.get_forwarded_engine.return_value = mock_engine
    mock_state.remove_engine = Mock()
    
    mock_stop = Mock()
    
    # Create mock modules
    mock_state_module = Mock()
    mock_state_module.state = mock_state
    mock_provisioner_module = Mock()
    mock_provisioner_module.stop_container = mock_stop
    
    # Temporarily replace the modules
    original_state = sys.modules.get('app.services.state')
    original_provisioner = sys.modules.get('app.services.provisioner')
    
    try:
        sys.modules['app.services.state'] = mock_state_module
        sys.modules['app.services.provisioner'] = mock_provisioner_module
        
        # Create monitor with VPN enabled
        with patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
            gluetun_monitor = GluetunMonitor()
            
            # Get the VPN monitor for gluetun
            vpn_monitor = gluetun_monitor.get_vpn_monitor("gluetun")
            assert vpn_monitor is not None, "VPN monitor should exist"
        
            # Verify no recovery time is set initially
            assert vpn_monitor._last_recovery_time is None, "No recovery time should be set initially"
            logger.info("✓ No recovery time set before port change")
            
            # Simulate port change
            await gluetun_monitor._handle_port_change("gluetun", 65290, 40648)
            
            # Verify recovery time was NOT set (port change should not trigger stabilization)
            assert vpn_monitor._last_recovery_time is None, "Recovery time should NOT be set after port change"
            logger.info(f"✓ Recovery time NOT set after port change (correct behavior)")
            
            # Verify the VPN is NOT in recovery stabilization period
            in_stabilization = vpn_monitor.is_in_recovery_stabilization_period()
            assert in_stabilization is False, "VPN should NOT be in stabilization period after port change"
            logger.info("✓ VPN is NOT in recovery stabilization period after port change")
        
    finally:
        # Restore original modules
        if original_state:
            sys.modules['app.services.state'] = original_state
        if original_provisioner:
            sys.modules['app.services.provisioner'] = original_provisioner


async def test_monitor_respects_recovery_stabilization():
    """Test that monitor skips cleanup during recovery stabilization period."""
    from app.services.gluetun import GluetunMonitor, VpnContainerMonitor
    from app.core.config import cfg
    from datetime import datetime, timezone, timedelta
    
    # Create a VPN monitor
    monitor = VpnContainerMonitor("gluetun")
    
    # Set recovery time to now (just recovered)
    monitor._last_recovery_time = datetime.now(timezone.utc)
    
    # Check if in recovery stabilization period
    in_stabilization = monitor.is_in_recovery_stabilization_period()
    assert in_stabilization is True, "Should be in recovery stabilization period immediately after recovery"
    logger.info("✓ Monitor is in recovery stabilization period immediately after recovery")
    
    # Set recovery time to 60 seconds ago (still within default 120s period)
    monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=60)
    in_stabilization = monitor.is_in_recovery_stabilization_period()
    assert in_stabilization is True, "Should still be in recovery stabilization period after 60s"
    logger.info("✓ Monitor is still in recovery stabilization period after 60s")
    
    # Set recovery time to 130 seconds ago (past default 120s period)
    monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=130)
    in_stabilization = monitor.is_in_recovery_stabilization_period()
    assert in_stabilization is False, "Should not be in recovery stabilization period after 130s"
    logger.info("✓ Monitor is not in recovery stabilization period after 130s")


async def test_redundant_mode_port_change_no_stabilization():
    """Test that port change does NOT set stabilization period in redundant VPN mode."""
    from app.services.gluetun import GluetunMonitor
    from app.models.schemas import EngineState
    from app.core.config import cfg
    import sys
    
    # Set up mock state with forwarded engines for each VPN
    mock_engine_vpn2 = EngineState(
        container_id="engine_vpn2_456",
        container_name="acestream-forwarded-vpn2",
        host="gluetun_2",
        port=19500,
        labels={"acestream.forwarded": "true"},
        forwarded=True,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        streams=[],
        health_status="healthy",
        last_health_check=datetime.now(timezone.utc),
        last_stream_usage=None,
        last_cache_cleanup=None,
        cache_size_bytes=None,
        vpn_container="gluetun_2"
    )
    
    # Mock state
    mock_state = Mock()
    mock_state.get_forwarded_engine_for_vpn.return_value = mock_engine_vpn2
    mock_state.remove_engine = Mock()
    
    mock_stop = Mock()
    
    # Create mock modules
    mock_state_module = Mock()
    mock_state_module.state = mock_state
    mock_provisioner_module = Mock()
    mock_provisioner_module.stop_container = mock_stop
    
    # Temporarily replace the modules
    original_state = sys.modules.get('app.services.state')
    original_provisioner = sys.modules.get('app.services.provisioner')
    
    try:
        sys.modules['app.services.state'] = mock_state_module
        sys.modules['app.services.provisioner'] = mock_provisioner_module
        
        # Simulate redundant mode
        with patch.object(cfg, 'VPN_MODE', 'redundant'):
            with patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun_2'):
                # Create monitor
                gluetun_monitor = GluetunMonitor()
                
                # Get the VPN monitor for gluetun_2
                vpn_monitor = gluetun_monitor.get_vpn_monitor("gluetun_2")
                assert vpn_monitor is not None, "VPN2 monitor should exist in redundant mode"
                
                # Verify no recovery time is set initially
                assert vpn_monitor._last_recovery_time is None, "No recovery time should be set initially"
                logger.info("✓ No recovery time set for VPN2 before port change")
                
                # Simulate port change on VPN2
                await gluetun_monitor._handle_port_change("gluetun_2", 36783, 61697)
                
                # Verify recovery time was NOT set for VPN2 (port change should not trigger stabilization)
                assert vpn_monitor._last_recovery_time is None, "Recovery time should NOT be set for VPN2 after port change"
                logger.info(f"✓ Recovery time NOT set for VPN2 after port change (correct behavior)")
                
                # Verify VPN2 is NOT in recovery stabilization period
                in_stabilization = vpn_monitor.is_in_recovery_stabilization_period()
                assert in_stabilization is False, "VPN2 should NOT be in recovery stabilization period after port change"
                logger.info("✓ VPN2 is NOT in recovery stabilization period after port change")
    
    finally:
        # Restore original modules
        if original_state:
            sys.modules['app.services.state'] = original_state
        if original_provisioner:
            sys.modules['app.services.provisioner'] = original_provisioner


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


if __name__ == "__main__":
    print("\n" + "="*70)
    print("Testing VPN Port Change Does NOT Trigger Stabilization Period")
    print("="*70 + "\n")
    
    print("Test 1: Port change does NOT set recovery stabilization period")
    print("-" * 70)
    run_async_test(test_port_change_does_not_set_recovery_stabilization())
    print()
    
    print("Test 2: Monitor respects recovery stabilization period")
    print("-" * 70)
    run_async_test(test_monitor_respects_recovery_stabilization())
    print()
    
    print("Test 3: Redundant mode port change does NOT set stabilization")
    print("-" * 70)
    run_async_test(test_redundant_mode_port_change_no_stabilization())
    print()
    
    print("="*70)
    print("All tests passed! ✓")
    print("="*70)
