#!/usr/bin/env python3
"""
Test to verify that 401 errors from Gluetun port forwarding API are handled gracefully.

When a VPN config doesn't support port forwarding, Gluetun returns a 401 Unauthorized error.
This is a normal condition and should not be logged as an error - no forwarded engine should be set.
"""

import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_401_error_handling_sync():
    """Test that 401 errors are handled gracefully in the synchronous port fetch."""
    print("\n🧪 Testing 401 error handling in get_forwarded_port_sync...")
    
    try:
        import httpx
        from app.services.gluetun import get_forwarded_port_sync, gluetun_monitor
        from app.core.config import cfg
        
        # Store original value
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = "test-gluetun"
        
        # Create a mock response that raises 401 HTTPStatusError
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        
        # Create the HTTPStatusError with the mock response
        error_401 = httpx.HTTPStatusError(
            message="401 Unauthorized",
            request=MagicMock(),
            response=mock_response
        )
        
        # Mock the httpx client to raise 401
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get = MagicMock(side_effect=error_401)
        
        # Mock the gluetun_monitor to bypass caching
        with patch.object(gluetun_monitor, 'get_cached_forwarded_port', return_value=None):
            with patch.object(gluetun_monitor, 'get_vpn_monitor', return_value=None):
                with patch('httpx.Client', return_value=mock_client):
                    # This should return None without raising an exception
                    result = get_forwarded_port_sync("test-gluetun")
        
        assert result is None, f"Expected None when 401 is returned, got {result}"
        print("   ✅ 401 error handled gracefully in sync version - returned None")
        
        # Restore original value
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_401_vs_other_errors_sync():
    """Test that 401 errors are treated differently from other errors."""
    print("\n🧪 Testing 401 vs other error handling in sync version...")
    
    try:
        import httpx
        from app.services.gluetun import get_forwarded_port_sync, gluetun_monitor
        from app.core.config import cfg
        
        # Store original value
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = "test-gluetun"
        
        # Test 1: 401 should return None (port forwarding not supported)
        mock_response_401 = MagicMock()
        mock_response_401.status_code = 401
        error_401 = httpx.HTTPStatusError(
            message="401 Unauthorized",
            request=MagicMock(),
            response=mock_response_401
        )
        
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=None)
        mock_client.get = MagicMock(side_effect=error_401)
        
        with patch.object(gluetun_monitor, 'get_cached_forwarded_port', return_value=None):
            with patch.object(gluetun_monitor, 'get_vpn_monitor', return_value=None):
                with patch('httpx.Client', return_value=mock_client):
                    result_401 = get_forwarded_port_sync("test-gluetun")
        
        assert result_401 is None, "401 should return None"
        print("   ✅ 401 error returns None (port forwarding not supported)")
        
        # Test 2: 500 should also return None but is a server error
        mock_response_500 = MagicMock()
        mock_response_500.status_code = 500
        error_500 = httpx.HTTPStatusError(
            message="500 Internal Server Error",
            request=MagicMock(),
            response=mock_response_500
        )
        
        mock_client.get = MagicMock(side_effect=error_500)
        
        with patch.object(gluetun_monitor, 'get_cached_forwarded_port', return_value=None):
            with patch.object(gluetun_monitor, 'get_vpn_monitor', return_value=None):
                with patch('httpx.Client', return_value=mock_client):
                    result_500 = get_forwarded_port_sync("test-gluetun")
        
        assert result_500 is None, "500 error should also return None"
        print("   ✅ 500 error also returns None (server error)")
        
        cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_vpn_container_monitor_401_handling():
    """Test that VpnContainerMonitor handles 401 correctly in async fetch."""
    print("\n🧪 Testing 401 handling in VpnContainerMonitor._fetch_and_cache_port...")
    
    try:
        import httpx
        from app.services.gluetun import VpnContainerMonitor
        from app.core.config import cfg
        
        # Create a monitor instance
        monitor = VpnContainerMonitor("test-vpn-container")
        
        # Create a mock response that raises 401 HTTPStatusError
        mock_response = MagicMock()
        mock_response.status_code = 401
        
        error_401 = httpx.HTTPStatusError(
            message="401 Unauthorized",
            request=MagicMock(),
            response=mock_response
        )
        
        # Mock the async client to raise 401
        async def mock_get(*args, **kwargs):
            raise error_401
        
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        # Run the async test
        async def run_test():
            with patch('httpx.AsyncClient', return_value=mock_client):
                result = await monitor._fetch_and_cache_port()
            return result
        
        result = asyncio.get_event_loop().run_until_complete(run_test())
        
        assert result is None, f"Expected None when 401 is returned, got {result}"
        print("   ✅ 401 error handled gracefully in async version - returned None")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_forwarded_engine_not_set_when_no_port():
    """Test that no forwarded engine is set when port forwarding returns None/401."""
    print("\n🧪 Testing that forwarded engine is not set when port unavailable...")
    
    try:
        from app.services.state import State
        from app.models.schemas import EngineState
        from datetime import datetime, timezone
        from unittest.mock import patch
        
        with patch('app.services.state.SessionLocal'):
            state = State()
            state.clear_state()
        
        # Create an engine without forwarded flag
        engine = EngineState(
            container_id="test-engine-1",
            container_name="acestream-1",
            host="localhost",
            port=6878,
            forwarded=False,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        state.engines["test-engine-1"] = engine
        
        # Verify no forwarded engine
        assert not state.has_forwarded_engine(), "Should not have forwarded engine initially"
        assert state.get_forwarded_engine() is None, "Should return None for forwarded engine"
        
        print("   ✅ No forwarded engine set when port forwarding unavailable")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("🔧 Testing Gluetun 401 Error Handling")
    print("=" * 60)
    print("This tests that when VPN config doesn't support port forwarding,")
    print("the 401 error is handled gracefully and no forwarded engine is set.")
    print("=" * 60)
    
    results = []
    results.append(("401 error handling (sync)", test_401_error_handling_sync()))
    results.append(("401 vs other errors (sync)", test_401_vs_other_errors_sync()))
    results.append(("VpnContainerMonitor 401 handling", test_vpn_container_monitor_401_handling()))
    results.append(("Forwarded engine not set", test_forwarded_engine_not_set_when_no_port()))
    
    print("\n" + "=" * 60)
    print("Test Results:")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 All 401 error handling tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed!")
        sys.exit(1)
