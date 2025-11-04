#!/usr/bin/env python3
"""
Manual test to demonstrate the prometheus metrics fix.
This simulates a real scenario and shows the metrics output.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Initialize database
from app.models.db_models import Base
from app.services.db import engine
Base.metadata.create_all(bind=engine)

def demo_metrics_fix():
    """Demonstrate that prometheus metrics now correctly show speeds."""
    from app.services.metrics import update_custom_metrics, reset_cumulative_metrics
    from app.services.metrics import (
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
        orch_total_streams
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from datetime import datetime, timezone
    
    print("=" * 80)
    print("DEMONSTRATING PROMETHEUS METRICS FIX")
    print("=" * 80)
    print()
    
    # Clear state
    state.clear_state()
    reset_cumulative_metrics()
    
    # Simulate a realistic scenario: 3 active streams with different speeds
    streams_data = [
        {"name": "Stream 1", "container": "engine_001", "port": 19001, "down_kbps": 2048, "up_kbps": 512},   # 2 MB/s down, 0.5 MB/s up
        {"name": "Stream 2", "container": "engine_002", "port": 19002, "down_kbps": 1024, "up_kbps": 256},   # 1 MB/s down, 0.25 MB/s up  
        {"name": "Stream 3", "container": "engine_003", "port": 19003, "down_kbps": 3072, "up_kbps": 1024},  # 3 MB/s down, 1 MB/s up
    ]
    
    print("Setting up scenario with 3 active streams:")
    print("-" * 80)
    
    total_expected_down = 0
    total_expected_up = 0
    
    for i, stream_data in enumerate(streams_data):
        # Create stream
        evt = StreamStartedEvent(
            container_id=stream_data["container"],
            engine=EngineAddress(host="localhost", port=stream_data["port"]),
            stream=StreamKey(key_type="content_id", key=f"content_key_{i}"),
            session=SessionInfo(
                playback_session_id=f"session_{i}",
                stat_url=f"http://localhost:{stream_data['port']}/stat",
                command_url=f"http://localhost:{stream_data['port']}/command",
                is_live=1
            ),
            labels={"name": stream_data["name"]}
        )
        state.on_stream_started(evt)
        
        # Add stats (AceStream API returns speeds in KB/s)
        stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
        snap = StreamStatSnapshot(
            ts=datetime.now(timezone.utc),
            peers=10 + i * 5,
            speed_down=stream_data["down_kbps"],  # KB/s
            speed_up=stream_data["up_kbps"],      # KB/s
            downloaded=1048576 * (i + 1),
            uploaded=524288 * (i + 1),
            status="active"
        )
        state.append_stat(stream_id, snap)
        
        down_mbps = stream_data["down_kbps"] / 1024
        up_mbps = stream_data["up_kbps"] / 1024
        total_expected_down += down_mbps
        total_expected_up += up_mbps
        
        print(f"{stream_data['name']:12} - Download: {down_mbps:5.2f} MB/s, Upload: {up_mbps:5.2f} MB/s  (from {stream_data['down_kbps']:5} KB/s, {stream_data['up_kbps']:4} KB/s)")
    
    print("-" * 80)
    print(f"{'TOTAL':12} - Download: {total_expected_down:5.2f} MB/s, Upload: {total_expected_up:5.2f} MB/s")
    print()
    
    # Update metrics
    update_custom_metrics()
    
    # Get metric values
    actual_streams = orch_total_streams._value.get()
    actual_down = orch_total_download_speed_mbps._value.get()
    actual_up = orch_total_upload_speed_mbps._value.get()
    
    print("Prometheus Metrics Output:")
    print("-" * 80)
    print(f"orch_total_streams {int(actual_streams)}")
    print(f"orch_total_download_speed_mbps {actual_down}")
    print(f"orch_total_upload_speed_mbps {actual_up}")
    print()
    
    # Verify
    print("Verification:")
    print("-" * 80)
    print(f"Expected {int(len(streams_data))} streams, got {int(actual_streams)}: {'✓ PASS' if actual_streams == len(streams_data) else '✗ FAIL'}")
    print(f"Expected {total_expected_down:.2f} MB/s download, got {actual_down:.2f} MB/s: {'✓ PASS' if abs(actual_down - total_expected_down) < 0.01 else '✗ FAIL'}")
    print(f"Expected {total_expected_up:.2f} MB/s upload, got {actual_up:.2f} MB/s: {'✓ PASS' if abs(actual_up - total_expected_up) < 0.01 else '✗ FAIL'}")
    print()
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    if (actual_streams == len(streams_data) and 
        abs(actual_down - total_expected_down) < 0.01 and 
        abs(actual_up - total_expected_up) < 0.01):
        print("=" * 80)
        print("✅ SUCCESS! Prometheus metrics now correctly aggregate speeds from KB/s to MB/s")
        print("=" * 80)
        return True
    else:
        print("=" * 80)
        print("✗ FAILED - Metrics do not match expected values")
        print("=" * 80)
        return False

if __name__ == "__main__":
    success = demo_metrics_fix()
    sys.exit(0 if success else 1)
