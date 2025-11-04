#!/usr/bin/env python3
"""
Test that metrics correctly handle zero values.

This test specifically validates the fix for the issue where metrics
were not being aggregated when values were 0 (due to using truthiness
checks instead of explicit None checks).
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_metrics_with_zero_values():
    """Test that metrics correctly aggregate zero values."""
    from app.services.metrics import update_custom_metrics
    from app.services.metrics import (
        orch_total_uploaded_bytes,
        orch_total_downloaded_bytes,
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
        orch_total_peers,
        orch_total_streams
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from app.services.db import engine
    from app.models.db_models import Base
    from datetime import datetime, timezone
    
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    
    # Clear state
    state.clear_state()
    
    # Add a mock engine and stream with ZERO values for speeds and upload
    evt = StreamStartedEvent(
        container_id="test_container_zero",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_key_zero"),
        session=SessionInfo(
            playback_session_id="test_session_zero",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt)
    
    # Add stats with ZERO values for some fields
    # This is a realistic scenario: downloading but not uploading (0 upload speed/bytes)
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=512,        # 0.5 MB/s = 512 KB/s (AceStream API returns speeds in KB/s)
        speed_up=0,            # ZERO upload speed (not uploading)
        downloaded=10485760,   # 10 MB downloaded
        uploaded=0,            # ZERO bytes uploaded
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Update cumulative byte metrics (simulating what collector does)
    from app.services.metrics import on_stream_stat_update
    on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify metrics reflect the data INCLUDING zero values
    assert orch_total_streams._value.get() == 1, "Should count 1 stream"
    assert orch_total_peers._value.get() == 5, "Should count 5 peers"
    assert orch_total_downloaded_bytes._value.get() == 10485760, "Should count downloaded bytes"
    
    # These are the critical assertions - zero values should be included!
    assert orch_total_uploaded_bytes._value.get() == 0, "Should include 0 uploaded bytes (not skip it!)"
    assert orch_total_upload_speed_mbps._value.get() == 0.0, "Should include 0 upload speed (not skip it!)"
    
    # Download speed should be calculated correctly (512 KB/s = 0.5 MB/s)
    expected_download_speed = 0.5  # 0.5 MB/s
    assert orch_total_download_speed_mbps._value.get() == expected_download_speed, \
        f"Should calculate download speed correctly: expected {expected_download_speed}, got {orch_total_download_speed_mbps._value.get()}"
    
    # Clean up
    state.clear_state()
    
    print("✅ Metrics with zero values test passed!")

def test_metrics_with_multiple_streams_mixed_zeros():
    """Test metrics aggregation with multiple streams having mixed zero/non-zero values."""
    from app.services.metrics import update_custom_metrics, on_stream_stat_update
    from app.services.metrics import (
        orch_total_uploaded_bytes,
        orch_total_downloaded_bytes,
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
        orch_total_peers,
        orch_total_streams
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from app.services.db import engine
    from app.models.db_models import Base
    from datetime import datetime, timezone
    
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    
    # Clear state
    state.clear_state()
    
    # Stream 1: Has upload activity
    evt1 = StreamStartedEvent(
        container_id="test_container_1",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_key_1"),
        session=SessionInfo(
            playback_session_id="test_session_1",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt1)
    stream_id1 = f"{evt1.stream.key}|{evt1.session.playback_session_id}"
    snap1 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=3,
        speed_down=1024,       # 1 MB/s = 1024 KB/s (AceStream API returns speeds in KB/s)
        speed_up=512,          # 0.5 MB/s = 512 KB/s (AceStream API returns speeds in KB/s)
        downloaded=5242880,    # 5 MB
        uploaded=2621440,      # 2.5 MB
        status="active"
    )
    state.append_stat(stream_id1, snap1)
    on_stream_stat_update(stream_id1, snap1.uploaded, snap1.downloaded)
    
    # Stream 2: Has ZERO upload (but should still be counted!)
    evt2 = StreamStartedEvent(
        container_id="test_container_2",
        engine=EngineAddress(host="localhost", port=19001),
        stream=StreamKey(key_type="content_id", key="test_key_2"),
        session=SessionInfo(
            playback_session_id="test_session_2",
            stat_url="http://localhost:19001/stat",
            command_url="http://localhost:19001/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt2)
    stream_id2 = f"{evt2.stream.key}|{evt2.session.playback_session_id}"
    snap2 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=0,               # ZERO peers (leecher only)
        speed_down=2048,       # 2 MB/s = 2048 KB/s (AceStream API returns speeds in KB/s)
        speed_up=0,            # ZERO upload
        downloaded=10485760,   # 10 MB
        uploaded=0,            # ZERO uploaded
        status="active"
    )
    state.append_stat(stream_id2, snap2)
    on_stream_stat_update(stream_id2, snap2.uploaded, snap2.downloaded)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify aggregated metrics
    assert orch_total_streams._value.get() == 2, "Should count 2 streams"
    
    # Peers: 3 + 0 = 3 (zero should be included in aggregation)
    assert orch_total_peers._value.get() == 3, "Should aggregate peers correctly (3 + 0 = 3)"
    
    # Downloaded: 5MB + 10MB = 15MB
    assert orch_total_downloaded_bytes._value.get() == 15728640, "Should aggregate downloaded bytes"
    
    # Uploaded: 2.5MB + 0MB = 2.5MB (zero should be included!)
    assert orch_total_uploaded_bytes._value.get() == 2621440, "Should aggregate uploaded bytes including zeros"
    
    # Speed calculations (1024 KB/s + 2048 KB/s = 3.0 MB/s, 512 KB/s + 0 KB/s = 0.5 MB/s)
    expected_download_speed = 3.0  # 3.0 MB/s
    expected_upload_speed = 0.5    # 0.5 MB/s
    
    assert orch_total_download_speed_mbps._value.get() == expected_download_speed, \
        f"Should aggregate download speeds: expected {expected_download_speed}, got {orch_total_download_speed_mbps._value.get()}"
    assert orch_total_upload_speed_mbps._value.get() == expected_upload_speed, \
        f"Should aggregate upload speeds including zeros: expected {expected_upload_speed}, got {orch_total_upload_speed_mbps._value.get()}"
    
    # Clean up
    state.clear_state()
    
    print("✅ Metrics with multiple streams and mixed zero values test passed!")

def test_metrics_with_all_zeros():
    """Test that metrics work correctly when all values are zero."""
    from app.services.metrics import update_custom_metrics
    from app.services.metrics import (
        orch_total_uploaded_bytes,
        orch_total_downloaded_bytes,
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
        orch_total_peers,
        orch_total_streams
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from app.services.db import engine
    from app.models.db_models import Base
    from datetime import datetime, timezone
    
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    
    # Clear state
    state.clear_state()
    
    # Add a stream that just started (all zeros)
    evt = StreamStartedEvent(
        container_id="test_container_all_zero",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_key_all_zero"),
        session=SessionInfo(
            playback_session_id="test_session_all_zero",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt)
    
    # Add stats with ALL zero values
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=0,
        speed_down=0,
        speed_up=0,
        downloaded=0,
        uploaded=0,
        status="starting"
    )
    state.append_stat(stream_id, snap)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify all zero values are properly set
    assert orch_total_streams._value.get() == 1, "Should count 1 stream"
    assert orch_total_peers._value.get() == 0, "Should be 0 peers"
    assert orch_total_uploaded_bytes._value.get() == 0, "Should be 0 uploaded bytes"
    assert orch_total_downloaded_bytes._value.get() == 0, "Should be 0 downloaded bytes"
    assert orch_total_upload_speed_mbps._value.get() == 0.0, "Should be 0.0 upload speed"
    assert orch_total_download_speed_mbps._value.get() == 0.0, "Should be 0.0 download speed"
    
    # Clean up
    state.clear_state()
    
    print("✅ Metrics with all zero values test passed!")

if __name__ == "__main__":
    test_metrics_with_zero_values()
    test_metrics_with_multiple_streams_mixed_zeros()
    test_metrics_with_all_zeros()
    print("\n✅ All zero value metrics tests passed!")
