#!/usr/bin/env python3
"""
Tests for Acexy proxy integration - DEPRECATED.

The acexy proxy is now stateless and only sends stream started events.
These tests verify that:
1. The deprecated AcexySyncService is a no-op
2. The service returns correct deprecated status
3. Stream state is managed via stat URL checking (tested in test_stale_stream_detection.py)
"""

import sys
import os
import asyncio

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.acexy import AcexySyncService

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_acexy_sync_service_deprecated():
    """Test that AcexySyncService is properly deprecated."""
    print("Testing AcexySyncService deprecation status...")
    
    service = AcexySyncService()
    
    # Test start/stop are no-ops
    async def run_test():
        await service.start()
        await service.stop()
    
    asyncio.run(run_test())
    
    # Test status shows deprecated
    status = service.get_status()
    assert status["enabled"] is False
    assert status["deprecated"] is True
    assert "stat URL checking" in status["message"]
    assert status["url"] is None
    assert status["healthy"] is None
    assert status["sync_interval_seconds"] is None
    
    print("✅ AcexySyncService deprecation test passed!")


def test_acexy_backward_compatibility():
    """Test that the deprecated service maintains backwards compatibility."""
    print("Testing backwards compatibility...")
    
    service = AcexySyncService()
    
    # The service can be started and stopped without errors
    async def run_test():
        # Should not raise any errors
        await service.start()
        status = service.get_status()
        assert isinstance(status, dict)
        await service.stop()
    
    asyncio.run(run_test())
    
    print("✅ Backwards compatibility test passed!")


if __name__ == "__main__":
    print("🧪 Running Acexy integration tests (deprecated service)...\n")
    
    test_acexy_sync_service_deprecated()
    print()
    test_acexy_backward_compatibility()
    
    print("\n🎉 All Acexy integration tests passed!")
    print("\nNote: Stream state is now managed via stat URL checking.")
    print("See test_stale_stream_detection.py for stat URL checking tests.")
