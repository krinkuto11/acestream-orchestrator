"""
Test immediate autoscaling when VPN forwarded port changes.

This test verifies that when a VPN port change is detected:
1. The old forwarded engine is stopped immediately
2. The autoscaler is triggered immediately (not waiting for next cycle)
3. A new forwarded engine is provisioned with the new port

This addresses the issue where the system was left without forwarded engines
for up to 1-2 minutes while waiting for the next autoscaler cycle.
"""

import asyncio
import logging
from unittest.mock import Mock, AsyncMock, patch, call
from datetime import datetime, timezone

# Set up logging for test visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_immediate_autoscale_on_port_change():
    """Test that port change triggers immediate autoscaling."""
    from app.services.gluetun import GluetunMonitor
    from app.models.schemas import EngineState
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
    mock_state.get_forwarded_engine_for_vpn.return_value = mock_engine
    mock_state.remove_engine = Mock()
    
    # Mock the stop_container function
    mock_stop = Mock()
    
    # Mock the ensure_minimum function to verify it's called
    mock_ensure_minimum = Mock()
    
    # Create mock modules for the imports
    mock_state_module = Mock()
    mock_state_module.state = mock_state
    mock_provisioner_module = Mock()
    mock_provisioner_module.stop_container = mock_stop
    mock_autoscaler_module = Mock()
    mock_autoscaler_module.ensure_minimum = mock_ensure_minimum
    
    # Temporarily replace the modules
    original_state = sys.modules.get('app.services.state')
    original_provisioner = sys.modules.get('app.services.provisioner')
    original_autoscaler = sys.modules.get('app.services.autoscaler')
    
    try:
        sys.modules['app.services.state'] = mock_state_module
        sys.modules['app.services.provisioner'] = mock_provisioner_module
        sys.modules['app.services.autoscaler'] = mock_autoscaler_module
        
        # Create monitor and trigger port change
        gluetun_monitor = GluetunMonitor()
        
        # Simulate port change
        await gluetun_monitor._handle_port_change("gluetun", 43437, 57611)
        
        # Verify engine was removed from state
        mock_state.remove_engine.assert_called_once_with("test_engine_123")
        logger.info("✓ Engine removed from state")
        
        # Verify container was stopped
        mock_stop.assert_called_once_with("test_engine_123")
        logger.info("✓ Engine container stopped")
        
        # Verify ensure_minimum was called immediately with initial_startup=False
        mock_ensure_minimum.assert_called_once_with(False)
        logger.info("✓ Immediate autoscaling triggered (ensure_minimum called)")
        
        print("\n" + "="*70)
        print("✓ Test passed: Immediate autoscaling is triggered on port change")
        print("="*70)
        
    finally:
        # Restore original modules
        if original_state:
            sys.modules['app.services.state'] = original_state
        if original_provisioner:
            sys.modules['app.services.provisioner'] = original_provisioner
        if original_autoscaler:
            sys.modules['app.services.autoscaler'] = original_autoscaler


async def test_autoscaler_failure_does_not_break_port_change():
    """Test that autoscaler failures don't prevent port change handling."""
    from app.services.gluetun import GluetunMonitor
    from app.models.schemas import EngineState
    import sys
    
    # Set up mock state with a forwarded engine
    mock_engine = EngineState(
        container_id="test_engine_456",
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
    mock_state.get_forwarded_engine_for_vpn.return_value = mock_engine  # Add VPN mode support
    mock_state.remove_engine = Mock()
    
    # Mock the stop_container function
    mock_stop = Mock()
    
    # Mock ensure_minimum to raise an exception
    mock_ensure_minimum = Mock(side_effect=Exception("Autoscaler failed"))
    
    # Create mock modules
    mock_state_module = Mock()
    mock_state_module.state = mock_state
    mock_provisioner_module = Mock()
    mock_provisioner_module.stop_container = mock_stop
    mock_autoscaler_module = Mock()
    mock_autoscaler_module.ensure_minimum = mock_ensure_minimum
    
    # Temporarily replace the modules
    original_state = sys.modules.get('app.services.state')
    original_provisioner = sys.modules.get('app.services.provisioner')
    original_autoscaler = sys.modules.get('app.services.autoscaler')
    
    try:
        sys.modules['app.services.state'] = mock_state_module
        sys.modules['app.services.provisioner'] = mock_provisioner_module
        sys.modules['app.services.autoscaler'] = mock_autoscaler_module
        
        # Create monitor and trigger port change
        gluetun_monitor = GluetunMonitor()
        
        # Simulate port change - should not raise exception despite autoscaler failure
        await gluetun_monitor._handle_port_change("gluetun", 43437, 57611)
        
        # Verify engine was still removed and stopped despite autoscaler failure
        mock_state.remove_engine.assert_called_once_with("test_engine_456")
        mock_stop.assert_called_once_with("test_engine_456")
        logger.info("✓ Port change handling completed despite autoscaler failure")
        
        print("\n" + "="*70)
        print("✓ Test passed: Autoscaler failure doesn't break port change handling")
        print("="*70)
        
    finally:
        # Restore original modules
        if original_state:
            sys.modules['app.services.state'] = original_state
        if original_provisioner:
            sys.modules['app.services.provisioner'] = original_provisioner
        if original_autoscaler:
            sys.modules['app.services.autoscaler'] = original_autoscaler


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


if __name__ == "__main__":
    print("\n" + "="*70)
    print("Testing Immediate Autoscaling on VPN Port Change")
    print("="*70 + "\n")
    
    print("Test 1: Immediate autoscaling on port change")
    print("-" * 70)
    run_async_test(test_immediate_autoscale_on_port_change())
    print()
    
    print("Test 2: Autoscaler failure doesn't break port change")
    print("-" * 70)
    run_async_test(test_autoscaler_failure_does_not_break_port_change())
    print()
    
    print("="*70)
    print("All tests passed! ✓")
    print("="*70)
