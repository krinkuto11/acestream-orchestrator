"""
Test VPN forwarded port change detection and automatic forwarded engine replacement.

This test validates the scenario described in the problem statement:
- VPN restarts internally without engines becoming unhealthy
- Forwarded port changes (e.g., from 65290 to 40648)
- Forwarded engine is automatically replaced with new engine using new port
- Engine is not available via /engines endpoint during replacement
"""

import asyncio
import logging
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timezone

# Set up logging for test visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_vpn_port_change_detection():
    """Test that port changes are detected correctly."""
    from app.services.gluetun import VpnContainerMonitor
    
    monitor = VpnContainerMonitor("gluetun")
    
    # Initially no port is set
    assert monitor._last_stable_forwarded_port is None
    
    # Simulate setting initial port
    monitor._last_stable_forwarded_port = 65290
    monitor._cached_port = 65290
    monitor._port_cache_time = datetime.now(timezone.utc)
    
    logger.info("✓ Initial port set to 65290")
    
    # Verify port is tracked
    assert monitor._last_stable_forwarded_port == 65290


async def test_port_change_detection_async():
    """Test async port change detection."""
    from app.services.gluetun import VpnContainerMonitor
    from app.core.config import cfg
    from datetime import datetime, timezone, timedelta
    
    monitor = VpnContainerMonitor("gluetun")
    monitor._last_health_status = True  # VPN is healthy
    
    # Mock the fetch method to return different ports
    async def mock_fetch_port_initial():
        return 65290
    
    async def mock_fetch_port_changed():
        return 40648
    
    # First check - establish baseline
    monitor._fetch_and_cache_port = mock_fetch_port_initial
    result = await monitor.check_port_change()
    assert result is None, "First check should not detect change, just establish baseline"
    assert monitor._last_stable_forwarded_port == 65290
    logger.info("✓ Baseline port established: 65290")
    
    # Simulate time passing to bypass throttling
    monitor._last_port_check_time = datetime.now(timezone.utc) - timedelta(seconds=31)
    
    # Second check - detect port change
    monitor._fetch_and_cache_port = mock_fetch_port_changed
    result = await monitor.check_port_change()
    assert result is not None, "Port change should be detected"
    old_port, new_port = result
    assert old_port == 65290, f"Old port should be 65290, got {old_port}"
    assert new_port == 40648, f"New port should be 40648, got {new_port}"
    assert monitor._last_stable_forwarded_port == 40648, "Stable port should be updated"
    logger.info(f"✓ Port change detected: {old_port} -> {new_port}")
    
    # Simulate time passing again
    monitor._last_port_check_time = datetime.now(timezone.utc) - timedelta(seconds=31)
    
    # Third check - no change
    result = await monitor.check_port_change()
    assert result is None, "No change should be detected when port stays the same"
    logger.info("✓ No false port change detected")


async def test_port_change_triggers_engine_replacement():
    """Test that port change triggers forwarded engine replacement."""
    from app.models.schemas import EngineState
    from app.services.gluetun import GluetunMonitor
    from datetime import datetime, timezone
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
    
    # Mock the state module at import location
    mock_state = Mock()
    mock_state.get_forwarded_engine.return_value = mock_engine
    mock_state.get_forwarded_engine_for_vpn.return_value = mock_engine
    mock_state.remove_engine = Mock()
    
    # Mock the stop_container function
    mock_stop = Mock()
    
    # Create mock modules for the imports inside _handle_port_change
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
        
        # Create monitor and trigger port change
        gluetun_monitor = GluetunMonitor()
        
        # Simulate port change
        await gluetun_monitor._handle_port_change("gluetun", 65290, 40648)
        
        # Verify engine was removed from state first (to hide from /engines)
        mock_state.remove_engine.assert_called_once_with("test_engine_123")
        logger.info("✓ Engine removed from state (hidden from /engines endpoint)")
        
        # Verify container was stopped
        mock_stop.assert_called_once_with("test_engine_123")
        logger.info("✓ Engine container stopped")
        
    finally:
        # Restore original modules
        if original_state:
            sys.modules['app.services.state'] = original_state
        if original_provisioner:
            sys.modules['app.services.provisioner'] = original_provisioner


async def test_no_port_change_when_vpn_unhealthy():
    """Test that port changes are not checked when VPN is unhealthy."""
    from app.services.gluetun import VpnContainerMonitor
    
    monitor = VpnContainerMonitor("gluetun")
    monitor._last_health_status = False  # VPN is unhealthy
    monitor._last_stable_forwarded_port = 65290
    
    # Mock fetch to return a different port
    async def mock_fetch_different_port():
        return 40648
    
    monitor._fetch_and_cache_port = mock_fetch_different_port
    
    # Check for port change - should return None because VPN is unhealthy
    result = await monitor.check_port_change()
    assert result is None, "Port changes should not be checked when VPN is unhealthy"
    logger.info("✓ Port change check skipped when VPN unhealthy")


async def test_redundant_mode_port_change():
    """Test port change handling in redundant VPN mode."""
    from app.models.schemas import EngineState
    from app.core.config import cfg
    from app.services.gluetun import GluetunMonitor
    from datetime import datetime, timezone
    import sys
    
    # Set up mock state with forwarded engines for each VPN
    mock_engine_vpn1 = EngineState(
        container_id="engine_vpn1_123",
        container_name="acestream-forwarded-vpn1",
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
    
    mock_engine_vpn2 = EngineState(
        container_id="engine_vpn2_456",
        container_name="acestream-forwarded-vpn2",
        host="gluetun2",
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
        vpn_container="gluetun2"
    )
    
    # Mock state to return appropriate engine based on VPN
    mock_state = Mock()
    def get_forwarded_for_vpn(vpn_name):
        if vpn_name == "gluetun":
            return mock_engine_vpn1
        elif vpn_name == "gluetun2":
            return mock_engine_vpn2
        return None
    
    mock_state.get_forwarded_engine_for_vpn.side_effect = get_forwarded_for_vpn
    mock_state.remove_engine = Mock()
    
    mock_stop = Mock()
    
    # Create mock modules for the imports inside _handle_port_change
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
            gluetun_monitor = GluetunMonitor()
            
            # Port change on VPN1 should only affect VPN1's engine
            await gluetun_monitor._handle_port_change("gluetun", 65290, 40648)
            
            mock_state.remove_engine.assert_called_with("engine_vpn1_123")
            mock_stop.assert_called_with("engine_vpn1_123")
            logger.info("✓ Only VPN1's forwarded engine replaced in redundant mode")
    
    finally:
        # Restore original modules
        if original_state:
            sys.modules['app.services.state'] = original_state
        if original_provisioner:
            sys.modules['app.services.provisioner'] = original_provisioner


def test_engines_endpoint_filtering_during_replacement():
    """Test that engines are properly filtered from /engines during replacement."""
    import sys
    
    # Clear any mock modules before importing State
    sys.modules.pop('app.services.state', None)
    
    from app.services.state import State
    from app.models.schemas import EngineState
    from datetime import datetime, timezone
    
    # Create test state
    test_state = State()
    
    # Add a forwarded engine
    engine = EngineState(
        container_id="test_engine",
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
    
    test_state.engines["test_engine"] = engine
    
    # Verify engine is in list
    engines = test_state.list_engines()
    assert len(engines) == 1
    assert engines[0].container_id == "test_engine"
    logger.info("✓ Engine visible before removal")
    
    # Remove engine (simulating port change handling)
    removed = test_state.remove_engine("test_engine")
    assert removed is not None
    assert removed.container_id == "test_engine"
    
    # Verify engine is no longer in list
    engines = test_state.list_engines()
    assert len(engines) == 0
    logger.info("✓ Engine hidden after removal (not in /engines endpoint)")


def run_async_test(coro):
    """Helper to run async tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


if __name__ == "__main__":
    print("\n" + "="*70)
    print("Testing VPN Port Change Detection and Forwarded Engine Replacement")
    print("="*70 + "\n")
    
    # Run sync tests
    print("Test 1: VPN port change detection (sync)")
    print("-" * 70)
    test_vpn_port_change_detection()
    print()
    
    # Run async tests
    print("Test 2: Port change detection (async)")
    print("-" * 70)
    run_async_test(test_port_change_detection_async())
    print()
    
    print("Test 3: Port change triggers engine replacement")
    print("-" * 70)
    run_async_test(test_port_change_triggers_engine_replacement())
    print()
    
    print("Test 4: No port change check when VPN unhealthy")
    print("-" * 70)
    run_async_test(test_no_port_change_when_vpn_unhealthy())
    print()
    
    print("Test 5: Redundant mode port change handling")
    print("-" * 70)
    run_async_test(test_redundant_mode_port_change())
    print()
    
    print("Test 6: Engine filtering during replacement")
    print("-" * 70)
    test_engines_endpoint_filtering_during_replacement()
    print()
    
    print("="*70)
    print("All tests passed! ✓")
    print("="*70)
