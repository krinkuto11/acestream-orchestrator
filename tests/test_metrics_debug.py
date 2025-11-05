#!/usr/bin/env python3
"""
Debug test to understand why metrics might be stuck at 0.0.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Initialize database
from app.models.db_models import Base
from app.services.db import engine
Base.metadata.create_all(bind=engine)

def test_list_streams_with_stats_behavior():
    """Debug what list_streams_with_stats returns when there are no stats."""
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from datetime import datetime, timezone
    
    # Clear state
    state.clear_state()
    
    # Create a stream WITHOUT adding stats
    evt = StreamStartedEvent(
        container_id="test_container_no_stats",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_no_stats_key"),
        session=SessionInfo(
            playback_session_id="test_no_stats_session",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "no_stats"}
    )
    state.on_stream_started(evt)
    
    # Get streams WITH stats (but we haven't added any stats yet)
    streams = state.list_streams_with_stats(status="started")
    
    print(f"Number of streams: {len(streams)}")
    if streams:
        stream = streams[0]
        print(f"Stream ID: {stream.id}")
        print(f"Stream speed_down: {stream.speed_down}")
        print(f"Stream speed_up: {stream.speed_up}")
        print(f"Stream peers: {stream.peers}")
    
    # Now add stats
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=1024,  # 1024 KB/s
        speed_up=512,     # 512 KB/s
        downloaded=1048576,
        uploaded=524288,
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Get streams again
    streams = state.list_streams_with_stats(status="started")
    
    print("\nAfter adding stats:")
    if streams:
        stream = streams[0]
        print(f"Stream ID: {stream.id}")
        print(f"Stream speed_down: {stream.speed_down}")
        print(f"Stream speed_up: {stream.speed_up}")
        print(f"Stream peers: {stream.peers}")
    
    # Clean up
    state.clear_state()
    
    print("\n✅ Debug test completed!")

def test_metrics_calculation_with_and_without_stats():
    """Test metrics calculation with streams that have and don't have stats."""
    from app.services.metrics import update_custom_metrics, reset_cumulative_metrics
    from app.services.metrics import (
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
        orch_total_streams
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from datetime import datetime, timezone
    
    # Clear state
    state.clear_state()
    reset_cumulative_metrics()
    
    # Create two streams, only add stats to one
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
        
        # Only add stats to stream 0
        if i == 0:
            stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=3,
                speed_down=1024,  # 1024 KB/s = 1 MB/s
                speed_up=512,     # 512 KB/s = 0.5 MB/s
                downloaded=1048576,
                uploaded=524288,
                status="active"
            )
            state.append_stat(stream_id, snap)
    
    # Debug: Check what list_streams_with_stats returns
    streams = state.list_streams_with_stats(status="started")
    print(f"\nTotal streams returned: {len(streams)}")
    for stream in streams:
        print(f"  Stream {stream.id[:20]}... - speed_down: {stream.speed_down}, speed_up: {stream.speed_up}")
    
    # Update metrics
    update_custom_metrics()
    
    # Check metrics
    total_streams = orch_total_streams._value.get()
    download_speed = orch_total_download_speed_mbps._value.get()
    upload_speed = orch_total_upload_speed_mbps._value.get()
    
    print(f"\nMetrics:")
    print(f"  Total streams: {total_streams} (expected: 2)")
    print(f"  Download speed: {download_speed} MB/s (expected: 1.0)")
    print(f"  Upload speed: {upload_speed} MB/s (expected: 0.5)")
    
    # Only one stream has stats, so speeds should reflect that one stream
    assert total_streams == 2
    assert download_speed == 1.0, f"Expected 1.0 MB/s but got {download_speed}"
    assert upload_speed == 0.5, f"Expected 0.5 MB/s but got {upload_speed}"
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    print("\n✅ Mixed stats test passed!")

if __name__ == "__main__":
    print("Running debug tests...\n")
    test_list_streams_with_stats_behavior()
    test_metrics_calculation_with_and_without_stats()
    print("\n✅ All debug tests passed!")
