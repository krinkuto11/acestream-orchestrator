#!/usr/bin/env python3
"""
Test livepos data collection and enrichment functionality.

Tests that the collector properly extracts livepos data from stat URLs
and that the /streams endpoint includes it in the response.
"""

import sys
import os
from datetime import datetime, timezone

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.state import State
from app.models.schemas import (
    StreamState, 
    StreamStartedEvent, 
    StreamStatSnapshot, 
    EngineAddress, 
    StreamKey, 
    SessionInfo,
    LivePosData
)

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_livepos_enrichment():
    """Test that streams can be enriched with livepos data."""
    print("Testing livepos data enrichment...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a live stream
    evt = StreamStartedEvent(
        container_id="test_container_livepos",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_live_stream"),
        session=SessionInfo(
            playback_session_id="test_session_livepos",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_livepos",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_livepos",
            is_live=1
        ),
        labels={"stream_id": "test_live_stream_1"}
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Verify stream was created without livepos
    streams = test_state.list_streams(status="started")
    assert len(streams) == 1
    assert streams[0].id == stream_id
    assert streams[0].livepos is None
    
    # Create livepos data (simulating what would come from AceStream API)
    livepos = LivePosData(
        pos="1767629806",
        live_first="1767628008",
        live_last="1767629808",
        first_ts="1767628008",
        last_ts="1767629808",
        buffer_pieces="15"
    )
    
    # Add stats snapshot with livepos
    stat = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=15,
        speed_down=158,
        speed_up=6,
        downloaded=72613888,
        uploaded=2506752,
        status="dl",
        livepos=livepos
    )
    test_state.append_stat(stream_id, stat)
    
    # Get streams with stats (simulating the endpoint behavior)
    streams = test_state.list_streams_with_stats(status="started")
    
    # Verify enrichment with livepos data
    assert len(streams) == 1
    enriched_stream = streams[0]
    assert enriched_stream.livepos is not None
    assert enriched_stream.livepos.pos == "1767629806"
    assert enriched_stream.livepos.live_first == "1767628008"
    assert enriched_stream.livepos.live_last == "1767629808"
    assert enriched_stream.livepos.buffer_pieces == "15"
    
    print("✅ LivePos enrichment test passed!")
    
    # Clean up
    test_state.clear_state()


def test_stream_without_livepos():
    """Test that non-live streams work correctly without livepos."""
    print("Testing non-live stream without livepos...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a non-live stream (VOD)
    evt = StreamStartedEvent(
        container_id="test_container_vod",
        engine=EngineAddress(host="127.0.0.1", port=8081),
        stream=StreamKey(key_type="infohash", key="test_vod_stream"),
        session=SessionInfo(
            playback_session_id="test_session_vod",
            stat_url="http://127.0.0.1:8081/ace/stat/test_session_vod",
            command_url="http://127.0.0.1:8081/ace/cmd/test_session_vod",
            is_live=0
        )
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Add stats without livepos (typical for VOD)
    stat = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=10,
        speed_down=500,
        speed_up=50,
        downloaded=1000000,
        uploaded=100000,
        status="dl",
        livepos=None  # No livepos for VOD
    )
    test_state.append_stat(stream_id, stat)
    
    # Get streams with stats
    streams = test_state.list_streams_with_stats(status="started")
    
    # Verify stream exists but livepos is None
    assert len(streams) == 1
    assert streams[0].livepos is None
    assert streams[0].peers == 10
    
    print("✅ Non-live stream without livepos test passed!")
    
    # Clean up
    test_state.clear_state()


def test_livepos_update():
    """Test that livepos data updates correctly."""
    print("Testing livepos data update...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a live stream
    evt = StreamStartedEvent(
        container_id="test_container_update",
        engine=EngineAddress(host="127.0.0.1", port=8082),
        stream=StreamKey(key_type="content_id", key="test_update_stream"),
        session=SessionInfo(
            playback_session_id="test_session_update",
            stat_url="http://127.0.0.1:8082/ace/stat/test_session_update",
            command_url="http://127.0.0.1:8082/ace/cmd/test_session_update",
            is_live=1
        )
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    
    # Add first livepos snapshot
    livepos1 = LivePosData(
        pos="1767629800",
        live_first="1767628000",
        live_last="1767629800",
        buffer_pieces="10"
    )
    stat1 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=10,
        speed_down=100,
        speed_up=10,
        downloaded=1000000,
        uploaded=100000,
        status="dl",
        livepos=livepos1
    )
    test_state.append_stat(stream_id, stat1)
    
    # Add second livepos snapshot (simulating update after 1 second)
    livepos2 = LivePosData(
        pos="1767629801",
        live_first="1767628000",
        live_last="1767629801",
        buffer_pieces="12"
    )
    stat2 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=12,
        speed_down=120,
        speed_up=12,
        downloaded=1200000,
        uploaded=120000,
        status="dl",
        livepos=livepos2
    )
    test_state.append_stat(stream_id, stat2)
    
    # Get streams with latest stats
    streams = test_state.list_streams_with_stats(status="started")
    
    # Verify latest livepos is returned
    assert len(streams) == 1
    enriched_stream = streams[0]
    assert enriched_stream.livepos is not None
    assert enriched_stream.livepos.pos == "1767629801"  # Updated position
    assert enriched_stream.livepos.buffer_pieces == "12"  # Updated buffer
    
    print("✅ LivePos update test passed!")
    
    # Clean up
    test_state.clear_state()


if __name__ == "__main__":
    test_livepos_enrichment()
    test_stream_without_livepos()
    test_livepos_update()
    print("\n✅ All livepos tests passed!")
