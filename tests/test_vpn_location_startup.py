"""
Test VPN Location Service Startup Initialization

Tests that the VPN location service can be initialized at startup
with verbose logging of server statistics.
"""

import asyncio
import sys
import os
import logging

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.vpn_location import vpn_location_service

# Configure logging to see INFO level logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)


async def test_startup_initialization():
    """Test that the startup initialization method works with verbose logging."""
    print("Test: VPN location service startup initialization")
    print("=" * 80)
    
    # Clear any existing cache to force a fresh fetch
    vpn_location_service._servers_cache = None
    vpn_location_service._cache_timestamp = None
    vpn_location_service._ip_index.clear()
    
    # Call the startup initialization method
    await vpn_location_service.initialize_at_startup()
    
    # Verify the service is ready
    assert vpn_location_service.is_ready(), "Service should be ready after initialization"
    assert len(vpn_location_service._ip_index) > 0, "IP index should be populated"
    
    print("=" * 80)
    print(f"✓ Service initialized successfully with {len(vpn_location_service._ip_index)} server IPs")
    print(f"✓ Service ready status: {vpn_location_service.is_ready()}")


if __name__ == "__main__":
    try:
        asyncio.run(test_startup_initialization())
        print("\n✓ All tests passed!")
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
