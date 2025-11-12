"""
Test Engine Info Service

Tests fetching engine version information.
"""

import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.engine_info import get_engine_version_info, get_engine_version_info_sync


async def test_engine_version_info_mock():
    """Test that the service can handle invalid engine gracefully."""
    print("Test 1: Testing with invalid engine (should return None)...")
    
    # Test with non-existent engine
    version_info = await get_engine_version_info("invalid_host", 12345)
    
    assert version_info is None, "Should return None for invalid engine"
    print("✓ Correctly returned None for invalid engine")


def test_engine_version_info_sync_mock():
    """Test the synchronous version with invalid engine."""
    print("\nTest 2: Testing sync version with invalid engine (should return None)...")
    
    # Test with non-existent engine
    version_info = get_engine_version_info_sync("invalid_host", 12345)
    
    assert version_info is None, "Should return None for invalid engine"
    print("✓ Correctly returned None for invalid engine (sync)")


if __name__ == "__main__":
    print("Testing Engine Info Service...")
    print("=" * 60)
    
    # Run tests
    try:
        asyncio.run(test_engine_version_info_mock())
        test_engine_version_info_sync_mock()
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("\nNote: To test with a real engine, run an AceStream engine")
        print("and update the test with the correct host and port.")
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
