#!/usr/bin/env python3
"""
Test that prometheus metrics correctly convert speeds from KB/s to MB/s.

This test verifies the fix for the issue where total download and upload speed
metrics were not working correctly because the code wasn't accounting for
AceStream API returning speeds in KB/s instead of bytes/s.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Initialize database
from app.models.db_models import Base
from app.services.db import engine
Base.metadata.create_all(bind=engine)

def test_speed_conversion_with_kb_values():
    """Test that speeds in KB/s are correctly converted to MB/s in metrics."""
    from app.services.metrics import update_custom_metrics, reset_cumulative_metrics
    from app.services.metrics import (
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from datetime import datetime, timezone
    
    # Clear state
    state.clear_state()
    reset_cumulative_metrics()
    
    # Create a mock stream
    evt = StreamStartedEvent(
        container_id="test_container_speed",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_speed_key"),
        session=SessionInfo(
            playback_session_id="test_speed_session",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "speed"}
    )
    state.on_stream_started(evt)
    
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    
    # Add stats with speeds in KB/s (as returned by AceStream API)
    # Let's say 1024 KB/s download and 512 KB/s upload
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=1024,  # 1024 KB/s = 1 MB/s
        speed_up=512,     # 512 KB/s = 0.5 MB/s
        downloaded=1048576,
        uploaded=524288,
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify that speeds are correctly converted to MB/s
    # 1024 KB/s * 1024 bytes/KB / (1024 * 1024) bytes/MB = 1.0 MB/s
    download_speed = orch_total_download_speed_mbps._value.get()
    upload_speed = orch_total_upload_speed_mbps._value.get()
    
    print(f"  Download speed metric: {download_speed} MB/s (expected: 1.0)")
    print(f"  Upload speed metric: {upload_speed} MB/s (expected: 0.5)")
    
    assert download_speed == 1.0, f"Expected 1.0 MB/s but got {download_speed}"
    assert upload_speed == 0.5, f"Expected 0.5 MB/s but got {upload_speed}"
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    print("✅ Speed conversion test passed!")

def test_multiple_streams_speed_aggregation():
    """Test that speeds from multiple streams are correctly aggregated."""
    from app.services.metrics import update_custom_metrics, reset_cumulative_metrics
    from app.services.metrics import (
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from datetime import datetime, timezone
    
    # Clear state
    state.clear_state()
    reset_cumulative_metrics()
    
    # Create two mock streams
    for i in range(2):
        evt = StreamStartedEvent(
            container_id=f"test_container_{i}",
            engine=EngineAddress(host="localhost", port=19000 + i),
            stream=StreamKey(key_type="content_id", key=f"test_key_{i}"),
            session=SessionInfo(
                playback_session_id=f"test_session_{i}",
                stat_url=f"http://localhost:{19000 + i}/stat",
                command_url=f"http://localhost:{19000 + i}/command",
                is_live=1
            ),
            labels={"test": f"stream_{i}"}
        )
        state.on_stream_started(evt)
        
        stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
        
        # Add stats: Stream 0 has 512 KB/s down/up, Stream 1 has 512 KB/s down/up
        # Total should be 1024 KB/s = 1.0 MB/s for each direction
        snap = StreamStatSnapshot(
            ts=datetime.now(timezone.utc),
            peers=3,
            speed_down=512,  # 512 KB/s
            speed_up=512,    # 512 KB/s
            downloaded=1048576,
            uploaded=524288,
            status="active"
        )
        state.append_stat(stream_id, snap)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify aggregated speeds
    # 2 streams * 512 KB/s = 1024 KB/s = 1.0 MB/s
    download_speed = orch_total_download_speed_mbps._value.get()
    upload_speed = orch_total_upload_speed_mbps._value.get()
    
    print(f"  Total download speed: {download_speed} MB/s (expected: 1.0)")
    print(f"  Total upload speed: {upload_speed} MB/s (expected: 1.0)")
    
    assert download_speed == 1.0, f"Expected 1.0 MB/s but got {download_speed}"
    assert upload_speed == 1.0, f"Expected 1.0 MB/s but got {upload_speed}"
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    print("✅ Multiple streams aggregation test passed!")

def test_zero_speed_handling():
    """Test that zero speeds are correctly handled."""
    from app.services.metrics import update_custom_metrics, reset_cumulative_metrics
    from app.services.metrics import (
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from datetime import datetime, timezone
    
    # Clear state
    state.clear_state()
    reset_cumulative_metrics()
    
    # Create a mock stream with zero speeds
    evt = StreamStartedEvent(
        container_id="test_container_zero",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_zero_key"),
        session=SessionInfo(
            playback_session_id="test_zero_session",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "zero"}
    )
    state.on_stream_started(evt)
    
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    
    # Add stats with zero speeds
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=0,
        speed_down=0,
        speed_up=0,
        downloaded=0,
        uploaded=0,
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify zero speeds
    download_speed = orch_total_download_speed_mbps._value.get()
    upload_speed = orch_total_upload_speed_mbps._value.get()
    
    print(f"  Download speed with zeros: {download_speed} MB/s (expected: 0.0)")
    print(f"  Upload speed with zeros: {upload_speed} MB/s (expected: 0.0)")
    
    assert download_speed == 0.0, f"Expected 0.0 MB/s but got {download_speed}"
    assert upload_speed == 0.0, f"Expected 0.0 MB/s but got {upload_speed}"
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    print("✅ Zero speed handling test passed!")

if __name__ == "__main__":
    print("Testing prometheus metrics speed conversion fix...\n")
    test_speed_conversion_with_kb_values()
    test_multiple_streams_speed_aggregation()
    test_zero_speed_handling()
    print("\n✅ All speed conversion tests passed!")
