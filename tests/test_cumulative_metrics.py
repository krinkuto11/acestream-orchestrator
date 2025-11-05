#!/usr/bin/env python3
"""
Test cumulative metrics behavior across stream lifecycle.

This test validates that downloaded/uploaded byte metrics are cumulative
across all streams (active and ended), not just currently active streams.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Initialize database
from app.models.db_models import Base
from app.services.db import engine
Base.metadata.create_all(bind=engine)

def test_cumulative_bytes_across_stream_lifecycle():
    """Test that cumulative bytes persist when streams end."""
    from app.services.metrics import (
        update_custom_metrics,
        on_stream_stat_update,
        orch_total_uploaded_bytes,
        orch_total_downloaded_bytes,
        orch_total_streams
    )
    from app.services.state import state
    from app.models.schemas import (
        StreamStartedEvent, StreamEndedEvent, EngineAddress, 
        StreamKey, SessionInfo, StreamStatSnapshot
    )
    from datetime import datetime, timezone
    
    # Clear state to start fresh
    state.clear_state()
    
    # Scenario: Two streams that download data, then one ends
    # The total should include both streams' data even after one ends
    
    # Stream 1 starts
    evt1 = StreamStartedEvent(
        container_id="test_container_1",
        engine=EngineAddress(host="localhost", port=19001),
        stream=StreamKey(key_type="content_id", key="stream_1_key"),
        session=SessionInfo(
            playback_session_id="session_1",
            stat_url="http://localhost:19001/stat",
            command_url="http://localhost:19001/command",
            is_live=1
        ),
        labels={"test": "stream1"}
    )
    state.on_stream_started(evt1)
    stream_id_1 = f"{evt1.stream.key}|{evt1.session.playback_session_id}"
    
    # Stream 1: First stat update - downloaded 1000 bytes, uploaded 500 bytes
    snap1_1 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=100000,
        speed_up=50000,
        downloaded=1000,
        uploaded=500,
        status="active"
    )
    state.append_stat(stream_id_1, snap1_1)
    on_stream_stat_update(stream_id_1, snap1_1.uploaded, snap1_1.downloaded)
    
    update_custom_metrics()
    assert orch_total_streams._value.get() == 1, "Should have 1 active stream"
    assert orch_total_downloaded_bytes._value.get() == 1000, f"Expected 1000, got {orch_total_downloaded_bytes._value.get()}"
    assert orch_total_uploaded_bytes._value.get() == 500, f"Expected 500, got {orch_total_uploaded_bytes._value.get()}"
    print("✓ Stream 1 initial stats: 1000 downloaded, 500 uploaded")
    
    # Stream 1: Second stat update - downloaded 2000 total, uploaded 1000 total (delta: +1000, +500)
    snap1_2 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=100000,
        speed_up=50000,
        downloaded=2000,
        uploaded=1000,
        status="active"
    )
    state.append_stat(stream_id_1, snap1_2)
    on_stream_stat_update(stream_id_1, snap1_2.uploaded, snap1_2.downloaded)
    
    update_custom_metrics()
    assert orch_total_downloaded_bytes._value.get() == 2000, f"Expected 2000, got {orch_total_downloaded_bytes._value.get()}"
    assert orch_total_uploaded_bytes._value.get() == 1000, f"Expected 1000, got {orch_total_uploaded_bytes._value.get()}"
    print("✓ Stream 1 updated: 2000 downloaded, 1000 uploaded")
    
    # Stream 2 starts
    evt2 = StreamStartedEvent(
        container_id="test_container_2",
        engine=EngineAddress(host="localhost", port=19002),
        stream=StreamKey(key_type="content_id", key="stream_2_key"),
        session=SessionInfo(
            playback_session_id="session_2",
            stat_url="http://localhost:19002/stat",
            command_url="http://localhost:19002/command",
            is_live=1
        ),
        labels={"test": "stream2"}
    )
    state.on_stream_started(evt2)
    stream_id_2 = f"{evt2.stream.key}|{evt2.session.playback_session_id}"
    
    # Stream 2: First stat update - downloaded 3000 bytes, uploaded 1500 bytes
    snap2_1 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=8,
        speed_down=150000,
        speed_up=75000,
        downloaded=3000,
        uploaded=1500,
        status="active"
    )
    state.append_stat(stream_id_2, snap2_1)
    on_stream_stat_update(stream_id_2, snap2_1.uploaded, snap2_1.downloaded)
    
    update_custom_metrics()
    assert orch_total_streams._value.get() == 2, "Should have 2 active streams"
    # Total should be: Stream1(2000) + Stream2(3000) = 5000
    assert orch_total_downloaded_bytes._value.get() == 5000, f"Expected 5000, got {orch_total_downloaded_bytes._value.get()}"
    # Total should be: Stream1(1000) + Stream2(1500) = 2500
    assert orch_total_uploaded_bytes._value.get() == 2500, f"Expected 2500, got {orch_total_uploaded_bytes._value.get()}"
    print("✓ Stream 2 added: Total 5000 downloaded, 2500 uploaded")
    
    # Stream 1 ends (this is the critical test - data should persist!)
    end_evt1 = StreamEndedEvent(
        container_id="test_container_1",
        stream_id=stream_id_1,
        reason="test_ended"
    )
    state.on_stream_ended(end_evt1)
    
    update_custom_metrics()
    assert orch_total_streams._value.get() == 1, "Should have 1 active stream after ending stream 1"
    # CRITICAL: Total should still be 5000 (Stream1's 2000 + Stream2's 3000)
    # This is the bug fix - previously it would have been only 3000 (Stream2)
    assert orch_total_downloaded_bytes._value.get() == 5000, f"Expected 5000, got {orch_total_downloaded_bytes._value.get()}"
    assert orch_total_uploaded_bytes._value.get() == 2500, f"Expected 2500, got {orch_total_uploaded_bytes._value.get()}"
    print("✓ Stream 1 ended: Total still 5000 downloaded, 2500 uploaded (CUMULATIVE WORKING!)")
    
    # Stream 2: Second stat update - downloaded 4000 total, uploaded 2000 total (delta: +1000, +500)
    snap2_2 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=8,
        speed_down=150000,
        speed_up=75000,
        downloaded=4000,
        uploaded=2000,
        status="active"
    )
    state.append_stat(stream_id_2, snap2_2)
    on_stream_stat_update(stream_id_2, snap2_2.uploaded, snap2_2.downloaded)
    
    update_custom_metrics()
    # Total should be: Previous(5000) + Delta(1000) = 6000
    assert orch_total_downloaded_bytes._value.get() == 6000, f"Expected 6000, got {orch_total_downloaded_bytes._value.get()}"
    # Total should be: Previous(2500) + Delta(500) = 3000
    assert orch_total_uploaded_bytes._value.get() == 3000, f"Expected 3000, got {orch_total_uploaded_bytes._value.get()}"
    print("✓ Stream 2 continues: Total 6000 downloaded, 3000 uploaded")
    
    # Stream 2 ends
    end_evt2 = StreamEndedEvent(
        container_id="test_container_2",
        stream_id=stream_id_2,
        reason="test_ended"
    )
    state.on_stream_ended(end_evt2)
    
    update_custom_metrics()
    assert orch_total_streams._value.get() == 0, "Should have 0 active streams"
    # Total should still be 6000 and 3000 - cumulative across all streams
    assert orch_total_downloaded_bytes._value.get() == 6000, f"Expected 6000, got {orch_total_downloaded_bytes._value.get()}"
    assert orch_total_uploaded_bytes._value.get() == 3000, f"Expected 3000, got {orch_total_uploaded_bytes._value.get()}"
    print("✓ Stream 2 ended: Total still 6000 downloaded, 3000 uploaded (ALL CUMULATIVE!)")
    
    # Clean up
    state.clear_state()
    
    print("✅ Cumulative bytes across stream lifecycle test passed!")

def test_speed_metrics_are_instantaneous():
    """Test that speed metrics only reflect currently active streams."""
    from app.services.metrics import (
        update_custom_metrics,
        on_stream_stat_update,
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps
    )
    from app.services.state import state
    from app.models.schemas import (
        StreamStartedEvent, StreamEndedEvent, EngineAddress, 
        StreamKey, SessionInfo, StreamStatSnapshot
    )
    from datetime import datetime, timezone
    
    # Clear state to start fresh
    state.clear_state()
    
    # Stream 1 starts with 1 MB/s download speed
    evt1 = StreamStartedEvent(
        container_id="test_container_1",
        engine=EngineAddress(host="localhost", port=19001),
        stream=StreamKey(key_type="content_id", key="stream_1_key"),
        session=SessionInfo(
            playback_session_id="session_1",
            stat_url="http://localhost:19001/stat",
            command_url="http://localhost:19001/command",
            is_live=1
        ),
        labels={"test": "stream1"}
    )
    state.on_stream_started(evt1)
    stream_id_1 = f"{evt1.stream.key}|{evt1.session.playback_session_id}"
    
    snap1 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=1024,     # 1 MB/s = 1024 KB/s (AceStream API returns speeds in KB/s)
        speed_up=512,        # 0.5 MB/s = 512 KB/s (AceStream API returns speeds in KB/s)
        downloaded=1000,
        uploaded=500,
        status="active"
    )
    state.append_stat(stream_id_1, snap1)
    on_stream_stat_update(stream_id_1, snap1.uploaded, snap1.downloaded)
    
    update_custom_metrics()
    assert orch_total_download_speed_mbps._value.get() == 1.0, "Expected 1.0 MB/s"
    assert orch_total_upload_speed_mbps._value.get() == 0.5, "Expected 0.5 MB/s"
    print("✓ Stream 1 speed: 1.0 MB/s down, 0.5 MB/s up")
    
    # Stream 2 starts with 2 MB/s download speed
    evt2 = StreamStartedEvent(
        container_id="test_container_2",
        engine=EngineAddress(host="localhost", port=19002),
        stream=StreamKey(key_type="content_id", key="stream_2_key"),
        session=SessionInfo(
            playback_session_id="session_2",
            stat_url="http://localhost:19002/stat",
            command_url="http://localhost:19002/command",
            is_live=1
        ),
        labels={"test": "stream2"}
    )
    state.on_stream_started(evt2)
    stream_id_2 = f"{evt2.stream.key}|{evt2.session.playback_session_id}"
    
    snap2 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=8,
        speed_down=2048,     # 2 MB/s = 2048 KB/s (AceStream API returns speeds in KB/s)
        speed_up=1024,       # 1 MB/s = 1024 KB/s (AceStream API returns speeds in KB/s)
        downloaded=3000,
        uploaded=1500,
        status="active"
    )
    state.append_stat(stream_id_2, snap2)
    on_stream_stat_update(stream_id_2, snap2.uploaded, snap2.downloaded)
    
    update_custom_metrics()
    # Total speed should be sum of active streams: 1 + 2 = 3 MB/s
    assert orch_total_download_speed_mbps._value.get() == 3.0, f"Expected 3.0 MB/s, got {orch_total_download_speed_mbps._value.get()}"
    assert orch_total_upload_speed_mbps._value.get() == 1.5, f"Expected 1.5 MB/s, got {orch_total_upload_speed_mbps._value.get()}"
    print("✓ Both streams: 3.0 MB/s down, 1.5 MB/s up")
    
    # Stream 1 ends - speed should drop to only Stream 2's speed
    end_evt1 = StreamEndedEvent(
        container_id="test_container_1",
        stream_id=stream_id_1,
        reason="test_ended"
    )
    state.on_stream_ended(end_evt1)
    
    update_custom_metrics()
    # Speed should now be only Stream 2's speed: 2 MB/s
    assert orch_total_download_speed_mbps._value.get() == 2.0, f"Expected 2.0 MB/s, got {orch_total_download_speed_mbps._value.get()}"
    assert orch_total_upload_speed_mbps._value.get() == 1.0, f"Expected 1.0 MB/s, got {orch_total_upload_speed_mbps._value.get()}"
    print("✓ Stream 1 ended: Speed dropped to 2.0 MB/s down, 1.0 MB/s up (INSTANTANEOUS!)")
    
    # Clean up
    state.clear_state()
    
    print("✅ Speed metrics are instantaneous test passed!")

if __name__ == "__main__":
    test_cumulative_bytes_across_stream_lifecycle()
    test_speed_metrics_are_instantaneous()
    print("\n✅ All cumulative metrics tests passed!")
