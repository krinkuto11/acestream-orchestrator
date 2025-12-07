#!/usr/bin/env python3
"""
Integration test for the stream cleanup fixes.

This test simulates the full lifecycle:
1. Start a stream
2. Detect it as stale
3. Verify it's ended
4. Verify cleanup removes it after time passes
"""

import sys
import os
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector
from app.services.state import State
from app.services.stream_cleanup import StreamCleanup
from app.models.schemas import StreamState, StreamStartedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


async def test_full_lifecycle():
    """Test the complete stream lifecycle with stale detection and cleanup."""
    print("🧪 Testing full stream lifecycle...\n")
    
    # Create a fresh state
    test_state = State()
    
    # Step 1: Start a stream
    print("1. Starting a stream...")
    evt = StreamStartedEvent(
        container_id="test_container_full",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_full",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_full",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_full",
            is_live=1
        ),
        labels={"stream_id": "test_stream_full"}
    )
    
    stream = test_state.on_stream_started(evt)
    assert stream.status == "started"
    print("   ✓ Stream started")
    
    # Step 2: Get streams (should show the started stream)
    print("\n2. Getting started streams...")
    started_streams = test_state.list_streams_with_stats(status="started")
    assert len(started_streams) == 1
    assert started_streams[0].id == "test_stream_full"
    print("   ✓ Stream appears in /streams?status=started")
    
    # Step 3: Collector detects stream as stale
    print("\n3. Collector detects stream as stale...")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": None,
        "error": "unknown playback session id"
    }
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    collector = Collector()
    
    with patch('app.services.collector.state', test_state):
        await collector._collect_one(
            mock_client,
            "test_stream_full",
            "http://127.0.0.1:8080/ace/stat/test_session_full"
        ,
                "http://127.0.0.1:8080/ace/cmd"
            )
    
    # Verify stream was ended
    stream = test_state.get_stream("test_stream_full")
    assert stream.status == "ended"
    assert stream.ended_at is not None
    print("   ✓ Stream automatically ended by collector")
    
    # Step 4: Verify ended stream doesn't show in default /streams
    print("\n4. Verifying ended stream doesn't show in default /streams...")
    started_streams = test_state.list_streams_with_stats(status="started")
    assert len(started_streams) == 0
    print("   ✓ Ended stream not in /streams?status=started")
    
    # But it does show in ended streams
    ended_streams = test_state.list_streams_with_stats(status="ended")
    assert len(ended_streams) == 1
    assert ended_streams[0].id == "test_stream_full"
    print("   ✓ Ended stream appears in /streams?status=ended")
    
    # Step 5: Fast-forward time and cleanup
    print("\n5. Fast-forwarding time and running cleanup...")
    stream.ended_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    removed_count = test_state.cleanup_ended_streams(max_age_seconds=3600)
    assert removed_count == 1
    print(f"   ✓ Cleanup removed {removed_count} old stream(s)")
    
    # Step 6: Verify stream is completely gone
    print("\n6. Verifying stream is completely removed...")
    stream = test_state.get_stream("test_stream_full")
    assert stream is None
    
    all_streams = test_state.list_streams_with_stats(status=None)
    assert len(all_streams) == 0
    print("   ✓ Stream completely removed from memory")
    
    print("\n✅ Full lifecycle test passed!")


async def test_cleanup_service_starts():
    """Test that the cleanup service can start and stop."""
    print("\n🧪 Testing cleanup service lifecycle...\n")
    
    print("1. Creating cleanup service...")
    cleanup = StreamCleanup()
    print("   ✓ Service created")
    
    print("\n2. Starting service...")
    await cleanup.start()
    print("   ✓ Service started")
    
    print("\n3. Stopping service...")
    await cleanup.stop()
    print("   ✓ Service stopped")
    
    print("\n✅ Cleanup service lifecycle test passed!")


if __name__ == "__main__":
    print("=" * 60)
    print("INTEGRATION TEST: Stream Cleanup")
    print("=" * 60)
    
    asyncio.run(test_full_lifecycle())
    asyncio.run(test_cleanup_service_starts())
    
    print("\n" + "=" * 60)
    print("🎉 All integration tests passed!")
    print("=" * 60)
