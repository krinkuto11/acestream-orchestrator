#!/usr/bin/env python3
"""
Quick validation script for AceStream Proxy implementation.

This script validates that:
1. All modules import correctly
2. All endpoints are registered
3. Proxy manager starts/stops cleanly
4. Core functionality works
"""

import sys
import asyncio
from datetime import datetime, timezone


def test_imports():
    """Test that all modules import correctly."""
    print("Testing imports...")
    
    try:
        from app.main import app
        from app.services.proxy import ProxyManager, StreamSession, ClientManager, EngineSelector
        from app.services.proxy.config import (
            STREAM_IDLE_TIMEOUT,
            ENGINE_CACHE_TTL,
            MAX_STREAMS_PER_ENGINE,
        )
        print("✓ All modules import successfully")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False


def test_endpoints():
    """Test that proxy endpoints are registered."""
    print("\nTesting endpoint registration...")
    
    try:
        from app.main import app
        
        routes = [route.path for route in app.routes]
        expected_routes = [
            "/ace/getstream",
            "/ace/manifest.m3u8",
            "/proxy/status",
            "/proxy/sessions",
            "/proxy/sessions/{ace_id}",
        ]
        
        for route in expected_routes:
            if route in routes:
                print(f"✓ {route} registered")
            else:
                print(f"✗ {route} NOT registered")
                return False
        
        return True
    except Exception as e:
        print(f"✗ Endpoint test failed: {e}")
        return False


async def test_proxy_manager():
    """Test proxy manager lifecycle."""
    print("\nTesting ProxyManager lifecycle...")
    
    try:
        from app.services.proxy import ProxyManager
        
        # Get instance
        manager = ProxyManager.get_instance()
        print("✓ ProxyManager instance created")
        
        # Start manager
        await manager.start()
        print("✓ ProxyManager started")
        
        # Check status
        status = await manager.get_status()
        assert status["running"] == True
        assert status["total_sessions"] == 0
        print("✓ ProxyManager status correct")
        
        # Stop manager
        await manager.stop()
        print("✓ ProxyManager stopped cleanly")
        
        return True
    except Exception as e:
        print(f"✗ ProxyManager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_engine_selector():
    """Test engine selector with mock engines."""
    print("\nTesting EngineSelector...")
    
    try:
        from app.services.proxy import EngineSelector
        from app.services.state import state
        from app.models.schemas import EngineState
        
        # Clear state
        state.engines.clear()
        
        # Add test engine
        now = datetime.now(timezone.utc)
        state.engines["test_engine"] = EngineState(
            container_id="test_engine",
            host="127.0.0.1",
            port=19001,
            labels={"acestream.forwarded": "true"},
            health_status="healthy",
            first_seen=now,
            last_seen=now,
        )
        
        # Test selection
        selector = EngineSelector()
        engine = await selector.select_best_engine()
        
        assert engine is not None
        assert engine["container_id"] == "test_engine"
        assert engine["is_forwarded"] == True
        print("✓ EngineSelector works correctly")
        
        # Cleanup
        state.engines.clear()
        
        return True
    except Exception as e:
        print(f"✗ EngineSelector test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_client_manager():
    """Test client manager."""
    print("\nTesting ClientManager...")
    
    try:
        from app.services.proxy import ClientManager
        
        manager = ClientManager("test_stream")
        
        # Add clients
        count = await manager.add_client("client1")
        assert count == 1
        
        count = await manager.add_client("client2")
        assert count == 2
        print("✓ ClientManager add_client works")
        
        # Remove client
        count = await manager.remove_client("client1")
        assert count == 1
        print("✓ ClientManager remove_client works")
        
        # Check status
        assert manager.has_clients() == True
        assert manager.get_client_count() == 1
        print("✓ ClientManager status methods work")
        
        return True
    except Exception as e:
        print(f"✗ ClientManager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_validation():
    """Run all validation tests."""
    print("=" * 60)
    print("AceStream Proxy Validation")
    print("=" * 60)
    
    results = []
    
    # Synchronous tests
    results.append(("Imports", test_imports()))
    results.append(("Endpoints", test_endpoints()))
    
    # Asynchronous tests
    async def run_async_tests():
        r = []
        r.append(("ProxyManager", await test_proxy_manager()))
        r.append(("EngineSelector", await test_engine_selector()))
        r.append(("ClientManager", await test_client_manager()))
        return r
    
    async_results = asyncio.run(run_async_tests())
    results.extend(async_results)
    
    # Summary
    print("\n" + "=" * 60)
    print("Validation Summary")
    print("=" * 60)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{name:20s} {status}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All validation tests PASSED! Proxy is ready for production.")
        return 0
    else:
        print(f"\n❌ {total - passed} test(s) FAILED. Please review errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(run_validation())
