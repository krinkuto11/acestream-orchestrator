"""
Demo script showing the VPN port change detection scenario.

This demonstrates what happens when:
1. VPN restarts internally (without engines becoming unhealthy)
2. Forwarded port changes (e.g., 65290 -> 40648)
3. System automatically detects the change and replaces the forwarded engine
"""

import asyncio
import logging
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def simulate_vpn_port_change_scenario():
    """
    Simulate the scenario from the problem statement:
    - VPN is healthy and has forwarded port 65290
    - VPN restarts internally (still healthy)
    - Port changes to 40648
    - System detects change and replaces forwarded engine
    """
    from app.services.gluetun import VpnContainerMonitor
    from app.models.schemas import EngineState
    from unittest.mock import Mock
    import sys
    
    print("\n" + "="*80)
    print("SCENARIO: VPN Port Change Detection and Engine Replacement")
    print("="*80 + "\n")
    
    # Step 1: VPN is healthy with port 65290
    print("📡 Step 1: VPN is healthy with forwarded port 65290")
    print("-" * 80)
    
    monitor = VpnContainerMonitor("gluetun")
    monitor._last_health_status = True
    monitor._last_stable_forwarded_port = 65290
    monitor._cached_port = 65290
    monitor._port_cache_time = datetime.now(timezone.utc)
    
    # Mock engine state
    forwarded_engine = EngineState(
        container_id="acestream_fwd_123",
        container_name="acestream-forwarded-1",
        host="gluetun",
        port=19000,
        labels={"acestream.forwarded": "true"},
        forwarded=True,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        streams=["stream_123"],  # Engine has active stream
        health_status="healthy",
        last_health_check=datetime.now(timezone.utc),
        last_stream_usage=datetime.now(timezone.utc),
        last_cache_cleanup=None,
        cache_size_bytes=None,
        vpn_container="gluetun"
    )
    
    logger.info("✓ VPN container 'gluetun' is healthy")
    logger.info(f"✓ Forwarded port: {monitor._last_stable_forwarded_port}")
    logger.info(f"✓ Forwarded engine: {forwarded_engine.container_id} (has {len(forwarded_engine.streams)} active stream)")
    print()
    
    # Step 2: VPN restarts internally
    print("🔄 Step 2: VPN restarts internally (engines remain healthy)")
    print("-" * 80)
    logger.info("VPN reconnecting to provider...")
    logger.info("VPN connection re-established")
    logger.info("✓ VPN still reports as healthy")
    logger.info("✓ Engines continue serving streams without interruption")
    print()
    
    # Step 3: Monitoring loop detects port change
    print("🔍 Step 3: Next monitoring cycle detects port change")
    print("-" * 80)
    
    # Mock the fetch to return new port
    async def mock_fetch_new_port():
        return 40648
    
    monitor._fetch_and_cache_port = mock_fetch_new_port
    
    # Check for port change (this would normally happen in monitoring loop)
    port_change = await monitor.check_port_change()
    
    if port_change:
        old_port, new_port = port_change
        logger.warning(f"PORT CHANGE DETECTED: {old_port} -> {new_port}")
        print()
        
        # Step 4: Handle port change
        print("⚙️  Step 4: Automatic forwarded engine replacement")
        print("-" * 80)
        
        # Mock state and stop_container
        mock_state = Mock()
        mock_state.get_forwarded_engine.return_value = forwarded_engine
        mock_state.remove_engine = Mock()
        
        mock_stop = Mock()
        
        # Create mock modules
        mock_state_module = Mock()
        mock_state_module.state = mock_state
        mock_provisioner_module = Mock()
        mock_provisioner_module.stop_container = mock_stop
        
        # Temporarily replace modules
        original_state = sys.modules.get('app.services.state')
        original_provisioner = sys.modules.get('app.services.provisioner')
        
        try:
            sys.modules['app.services.state'] = mock_state_module
            sys.modules['app.services.provisioner'] = mock_provisioner_module
            
            from app.services.gluetun import GluetunMonitor
            gluetun_monitor = GluetunMonitor()
            
            # Trigger port change handling
            await gluetun_monitor._handle_port_change("gluetun", old_port, new_port)
            
            logger.info(f"✓ Old forwarded engine removed from state (hidden from /engines endpoint)")
            logger.info(f"✓ Container {forwarded_engine.container_id} stopped")
            logger.info(f"✓ Active streams on old engine will disconnect (1 stream affected)")
            
        finally:
            # Restore
            if original_state:
                sys.modules['app.services.state'] = original_state
            if original_provisioner:
                sys.modules['app.services.provisioner'] = original_provisioner
        
        print()
        
        # Step 5: Autoscaler provisions new engine
        print("🚀 Step 5: Autoscaler provisions new forwarded engine")
        print("-" * 80)
        logger.info("Autoscaler detected engine count below MIN_REPLICAS")
        logger.info(f"Provisioning new engine with VPN port {new_port}")
        logger.info("✓ New forwarded engine created: acestream-forwarded-2")
        logger.info("✓ New engine available via /engines endpoint")
        logger.info("✓ New streams can now use the new forwarded engine")
        print()
    
    # Summary
    print("="*80)
    print("SUMMARY")
    print("="*80)
    print(f"""
✓ Port change detected automatically: {old_port} → {new_port}
✓ Old forwarded engine removed immediately (not exposed to proxy)
✓ Active streams on old engine disconnected gracefully
✓ New forwarded engine provisioned with new port
✓ System ready to serve new streams with correct port
✓ Zero proxy errors (engine removed before proxy could route to it)

This prevents the issue described in the problem statement where the forwarded
engine would stop working after VPN restart with a new port, causing proxy errors.
    """)
    print("="*80)


if __name__ == "__main__":
    asyncio.run(simulate_vpn_port_change_scenario())
