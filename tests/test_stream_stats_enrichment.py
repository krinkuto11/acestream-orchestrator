#!/usr/bin/env python3
"""
Test stream stats enrichment functionality.

Tests that the /streams endpoint properly enriches stream objects
with the latest stats from stream_stats.
"""

import sys
import os
from datetime import datetime, timezone

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.state import State
from app.models.schemas import StreamState, StreamStartedEvent, StreamStatSnapshot, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_stream_enrichment_with_stats():
    """Test that streams can be enriched with latest stats."""
    print("Testing stream stats enrichment...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_123",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_key"),
        session=SessionInfo(
            playback_session_id="test_session_123",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_123",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_123",
            is_live=1
        ),
        labels={"stream_id": "test_stream_1"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Verify stream was created without stats
    streams = test_state.list_streams(status="started")
    assert len(streams) == 1
    assert streams[0].id == stream_id
    assert streams[0].peers is None
    assert streams[0].speed_down is None
    assert streams[0].speed_up is None
    assert streams[0].downloaded is None
    assert streams[0].uploaded is None
    
    # Add some stats snapshots
    stat1 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=3,
        speed_down=500,
        speed_up=50,
        downloaded=1000000,
        uploaded=100000,
        status="dl"
    )
    test_state.append_stat(stream_id, stat1)
    
    stat2 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=1071,
        speed_up=58,
        downloaded=168460288,
        uploaded=5292032,
        status="dl"
    )
    test_state.append_stat(stream_id, stat2)
    
    # Get streams and enrich with latest stats (simulating the endpoint behavior)
    streams = test_state.list_streams(status="started")
    for stream in streams:
        stats = test_state.get_stream_stats(stream.id)
        if stats:
            latest_stat = stats[-1]  # Get the most recent stat
            stream.peers = latest_stat.peers
            stream.speed_down = latest_stat.speed_down
            stream.speed_up = latest_stat.speed_up
            stream.downloaded = latest_stat.downloaded
            stream.uploaded = latest_stat.uploaded
    
    # Verify enrichment with latest stats (stat2)
    assert len(streams) == 1
    enriched_stream = streams[0]
    assert enriched_stream.peers == 5
    assert enriched_stream.speed_down == 1071
    assert enriched_stream.speed_up == 58
    assert enriched_stream.downloaded == 168460288
    assert enriched_stream.uploaded == 5292032
    
    print("✅ Stream enrichment with stats test passed!")
    
    # Clean up
    test_state.clear_state()


def test_stream_without_stats():
    """Test that streams without stats still work correctly."""
    print("Testing stream without stats...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_456",
        engine=EngineAddress(host="127.0.0.1", port=8081),
        stream=StreamKey(key_type="infohash", key="abc123"),
        session=SessionInfo(
            playback_session_id="test_session_456",
            stat_url="http://127.0.0.1:8081/ace/stat/test_session_456",
            command_url="http://127.0.0.1:8081/ace/cmd/test_session_456",
            is_live=0
        )
    )
    
    stream_state = test_state.on_stream_started(evt)
    
    # Get streams and try to enrich (simulating the endpoint behavior)
    streams = test_state.list_streams(status="started")
    for stream in streams:
        stats = test_state.get_stream_stats(stream.id)
        if stats:
            latest_stat = stats[-1]
            stream.peers = latest_stat.peers
            stream.speed_down = latest_stat.speed_down
            stream.speed_up = latest_stat.speed_up
            stream.downloaded = latest_stat.downloaded
            stream.uploaded = latest_stat.uploaded
    
    # Verify stream exists but stats remain None
    assert len(streams) == 1
    assert streams[0].peers is None
    assert streams[0].speed_down is None
    assert streams[0].speed_up is None
    assert streams[0].downloaded is None
    assert streams[0].uploaded is None
    
    print("✅ Stream without stats test passed!")
    
    # Clean up
    test_state.clear_state()


if __name__ == "__main__":
    test_stream_enrichment_with_stats()
    test_stream_without_stats()
    print("\n✅ All tests passed!")
