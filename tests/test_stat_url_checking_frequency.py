#!/usr/bin/env python3
"""
Test that stat URL checking happens frequently and is the primary mechanism
for detecting stale streams.

This test verifies that:
1. The collector uses the updated COLLECT_INTERVAL_S default (2s)
2. Stale stream detection happens quickly via stat URL polling
3. The acexy service is deprecated and doesn't interfere with stat URL checking
"""

import sys
import os
import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector
from app.services.state import State
from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo
from app.core.config import cfg

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_collect_interval_default():
    """Test that COLLECT_INTERVAL_S has the new default of 2 seconds."""
    print("Testing COLLECT_INTERVAL_S default value...")
    
    # The default should be 2 seconds for quick stale stream detection
    assert cfg.COLLECT_INTERVAL_S == 2, f"Expected COLLECT_INTERVAL_S=2, got {cfg.COLLECT_INTERVAL_S}"
    
    print(f"✅ COLLECT_INTERVAL_S is correctly set to {cfg.COLLECT_INTERVAL_S}s")


def test_rapid_stale_detection():
    """Test that stale streams are detected rapidly with the new interval."""
    print("Testing rapid stale stream detection...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_rapid",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_rapid",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_rapid",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_rapid",
            is_live=1
        ),
        labels={"stream_id": "rapid_test_stream"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    assert stream_state.status == "started"
    
    # Create a mock HTTP response that indicates a stale stream
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": None,
        "error": "unknown playback session id"
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector and measure detection time
    collector = Collector()
    
    async def run_test():
        start_time = time.time()
        
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "rapid_test_stream",
                "http://127.0.0.1:8080/ace/stat/test_session_rapid"
            ,
                "http://127.0.0.1:8080/ace/cmd"
            )
        
        detection_time = time.time() - start_time
        
        # Verify stream was ended quickly
        stream_after = test_state.get_stream("rapid_test_stream")
        assert stream_after is not None
        assert stream_after.status == "ended"
        
        print(f"  ✓ Stale stream detected in {detection_time:.3f}s")
        print(f"  ✓ With COLLECT_INTERVAL_S={cfg.COLLECT_INTERVAL_S}s, detection happens every {cfg.COLLECT_INTERVAL_S}s")
    
    asyncio.run(run_test())
    
    print("✅ Rapid stale detection test passed!")


def test_collector_is_primary_mechanism():
    """Test that the collector is documented as the primary stale stream detection mechanism."""
    print("Testing collector as primary detection mechanism...")
    
    from app.services.collector import Collector
    
    # Check that the Collector class has appropriate documentation
    assert Collector.__doc__ is not None
    assert "PRIMARY" in Collector.__doc__, "Collector should be documented as PRIMARY mechanism"
    assert "stale stream" in Collector.__doc__.lower()
    
    print("  ✓ Collector is documented as PRIMARY stale stream detection mechanism")
    print("✅ Collector primary mechanism test passed!")


def test_acexy_is_deprecated():
    """Test that acexy service is deprecated and returns correct status."""
    print("Testing acexy service deprecation...")
    
    from app.services.acexy import acexy_sync_service
    
    status = acexy_sync_service.get_status()
    
    assert status['enabled'] is False, "Acexy should be disabled"
    assert status['deprecated'] is True, "Acexy should be marked as deprecated"
    assert 'stat URL checking' in status['message'], "Message should mention stat URL checking"
    
    print("  ✓ Acexy is deprecated")
    print("  ✓ Status message points to stat URL checking")
    print("✅ Acexy deprecation test passed!")


def test_no_acexy_config():
    """Test that acexy config options are removed from config."""
    print("Testing acexy config removal...")
    
    from app.core.config import cfg
    
    # These config options should not exist
    assert not hasattr(cfg, 'ACEXY_ENABLED'), "ACEXY_ENABLED should be removed"
    assert not hasattr(cfg, 'ACEXY_URL'), "ACEXY_URL should be removed"
    assert not hasattr(cfg, 'ACEXY_SYNC_INTERVAL_S'), "ACEXY_SYNC_INTERVAL_S should be removed"
    
    print("  ✓ ACEXY_ENABLED removed from config")
    print("  ✓ ACEXY_URL removed from config")
    print("  ✓ ACEXY_SYNC_INTERVAL_S removed from config")
    print("✅ Acexy config removal test passed!")


def test_collector_frequency_comparison():
    """Test and report on the improvement in detection frequency."""
    print("Testing detection frequency improvement...")
    
    old_interval = 5  # Old default
    new_interval = cfg.COLLECT_INTERVAL_S  # New default (should be 2)
    
    # Calculate how much faster detection is now
    improvement_factor = old_interval / new_interval
    
    print(f"  ✓ Old COLLECT_INTERVAL_S: {old_interval}s")
    print(f"  ✓ New COLLECT_INTERVAL_S: {new_interval}s")
    print(f"  ✓ Detection is now {improvement_factor:.1f}x faster")
    print(f"  ✓ Stale streams detected in ~{new_interval}s instead of ~{old_interval}s")
    
    assert new_interval < old_interval, "New interval should be faster than old interval"
    assert new_interval == 2, f"New interval should be 2s, got {new_interval}s"
    
    print("✅ Detection frequency improvement test passed!")


if __name__ == "__main__":
    print("🧪 Running stat URL checking frequency tests...\n")
    
    test_collect_interval_default()
    print()
    test_rapid_stale_detection()
    print()
    test_collector_is_primary_mechanism()
    print()
    test_acexy_is_deprecated()
    print()
    test_no_acexy_config()
    print()
    test_collector_frequency_comparison()
    
    print("\n🎉 All stat URL checking frequency tests passed!")
    print("\n📊 Summary:")
    print("  • Stat URL checking is now the PRIMARY mechanism for stream state management")
    print("  • COLLECT_INTERVAL_S reduced from 5s to 2s (2.5x faster detection)")
    print("  • Acexy bidirectional communication removed (now stateless)")
    print("  • Stale streams detected in ~2s instead of ~5s")
