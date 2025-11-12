"""
Test VPN Location Service

Tests the VPN location matching service.
"""

import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.vpn_location import vpn_location_service


async def test_vpn_location_service_fetch():
    """Test that the VPN location service can fetch and cache server data."""
    print("Test 1: Fetching server data...")
    # Force a refresh to ensure we have data
    await vpn_location_service.force_refresh()
    
    # Check that we have an IP index
    assert len(vpn_location_service._ip_index) > 0, "IP index should be populated"
    print(f"✓ Loaded {len(vpn_location_service._ip_index)} server IPs")


async def test_vpn_location_lookup():
    """Test looking up a location by IP."""
    print("\nTest 2: Looking up location by IP...")
    # Ensure we have data
    await vpn_location_service._ensure_server_data()
    
    # Get a sample IP from the index if available
    if vpn_location_service._ip_index:
        sample_ip = list(vpn_location_service._ip_index.keys())[0]
        location = await vpn_location_service.get_location_by_ip(sample_ip)
        
        assert location is not None, f"Should find location for IP {sample_ip}"
        assert 'provider' in location
        assert 'country' in location
        assert 'city' in location
        
        print(f"✓ Sample IP: {sample_ip}")
        print(f"✓ Location: {location}")


async def test_vpn_location_unknown_ip():
    """Test looking up an unknown IP."""
    print("\nTest 3: Looking up unknown IP...")
    # Use a private IP that won't be in the server list
    unknown_ip = "192.168.1.1"
    
    location = await vpn_location_service.get_location_by_ip(unknown_ip)
    assert location is None, "Should return None for unknown IP"
    print(f"✓ Correctly returned None for unknown IP {unknown_ip}")


if __name__ == "__main__":
    print("Testing VPN Location Service...")
    print("=" * 60)
    
    # Run tests
    try:
        asyncio.run(test_vpn_location_service_fetch())
        asyncio.run(test_vpn_location_lookup())
        asyncio.run(test_vpn_location_unknown_ip())
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
