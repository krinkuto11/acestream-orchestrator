#!/usr/bin/env python3
"""
Verify what the existing test actually produces.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Initialize database
from app.models.db_models import Base
from app.services.db import engine
Base.metadata.create_all(bind=engine)

def test_what_existing_test_produces():
    """Test what speeds the existing test actually produces."""
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
    
    # Use the EXACT same values as test_custom_metrics.py
    evt = StreamStartedEvent(
        container_id="test_container_123",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_key"),
        session=SessionInfo(
            playback_session_id="test_session",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt)
    
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=10,
        speed_down=1048576,  # 1 MB/s in bytes (WRONG UNIT!)
        speed_up=524288,     # 0.5 MB/s in bytes (WRONG UNIT!)
        downloaded=10485760,
        uploaded=5242880,
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Update metrics
    update_custom_metrics()
    
    # Check what we get
    download_speed = orch_total_download_speed_mbps._value.get()
    upload_speed = orch_total_upload_speed_mbps._value.get()
    
    print(f"Using values from existing test:")
    print(f"  speed_down=1048576 (claimed to be '1 MB/s in bytes')")
    print(f"  speed_up=524288 (claimed to be '0.5 MB/s in bytes')")
    print(f"\nWith current code (after fix):")
    print(f"  Download speed: {download_speed} MB/s")
    print(f"  Upload speed: {upload_speed} MB/s")
    print(f"\nExpected if values were really in KB/s:")
    print(f"  1048576 KB/s * 1024 / (1024*1024) = 1024 MB/s")
    print(f"  524288 KB/s * 1024 / (1024*1024) = 512 MB/s")
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()

if __name__ == "__main__":
    test_what_existing_test_produces()
