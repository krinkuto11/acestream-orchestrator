#!/usr/bin/env python3
"""
Test MB metrics display.

This test validates that the new MB-formatted metrics are properly set.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_mb_metrics_display():
    """Test that MB metrics are properly formatted."""
    from app.services.metrics import update_custom_metrics, on_stream_stat_update, reset_cumulative_metrics
    from app.services.metrics import (
        orch_total_uploaded_bytes,
        orch_total_downloaded_bytes,
        orch_total_uploaded_mb,
        orch_total_downloaded_mb
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from app.services.db import engine
    from app.models.db_models import Base
    from datetime import datetime, timezone
    
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    
    # Clear state and metrics
    state.clear_state()
    reset_cumulative_metrics()
    
    # Add a mock engine and stream
    evt = StreamStartedEvent(
        container_id="test_container_mb",
        engine=EngineAddress(host="localhost", port=19000),
        stream=StreamKey(key_type="content_id", key="test_key_mb"),
        session=SessionInfo(
            playback_session_id="test_session_mb",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt)
    
    # Add stats with 10 MB downloaded and 5 MB uploaded
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    downloaded_bytes = 10 * 1024 * 1024  # 10 MB
    uploaded_bytes = 5 * 1024 * 1024     # 5 MB
    
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=512,
        speed_up=256,
        downloaded=downloaded_bytes,
        uploaded=uploaded_bytes,
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Update cumulative byte metrics
    on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify byte metrics
    assert orch_total_uploaded_bytes._value.get() == uploaded_bytes, "Should have uploaded bytes"
    assert orch_total_downloaded_bytes._value.get() == downloaded_bytes, "Should have downloaded bytes"
    
    # Verify MB metrics
    assert orch_total_uploaded_mb._value.get() == 5.0, "Should have 5.00 MB uploaded"
    assert orch_total_downloaded_mb._value.get() == 10.0, "Should have 10.00 MB downloaded"
    
    # Add more data: 1.5 MB uploaded, 2.75 MB downloaded
    snap2 = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=512,
        speed_up=256,
        downloaded=downloaded_bytes + int(2.75 * 1024 * 1024),
        uploaded=uploaded_bytes + int(1.5 * 1024 * 1024),
        status="active"
    )
    state.append_stat(stream_id, snap2)
    
    # Update cumulative byte metrics
    on_stream_stat_update(stream_id, snap2.uploaded, snap2.downloaded)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify updated MB metrics (with rounding to 2 decimal places)
    expected_uploaded_mb = 6.5  # 5 + 1.5
    expected_downloaded_mb = 12.75  # 10 + 2.75
    
    assert orch_total_uploaded_mb._value.get() == expected_uploaded_mb, \
        f"Should have {expected_uploaded_mb} MB uploaded, got {orch_total_uploaded_mb._value.get()}"
    assert orch_total_downloaded_mb._value.get() == expected_downloaded_mb, \
        f"Should have {expected_downloaded_mb} MB downloaded, got {orch_total_downloaded_mb._value.get()}"
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    print("✅ MB metrics display test passed!")

def test_mb_metrics_with_small_values():
    """Test that MB metrics work correctly with small byte values."""
    from app.services.metrics import update_custom_metrics, on_stream_stat_update, reset_cumulative_metrics
    from app.services.metrics import orch_total_uploaded_mb, orch_total_downloaded_mb
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from app.services.db import engine
    from app.models.db_models import Base
    from datetime import datetime, timezone
    
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    
    # Clear state and metrics
    state.clear_state()
    reset_cumulative_metrics()
    
    # Add a mock engine and stream
    evt = StreamStartedEvent(
        container_id="test_container_small",
        engine=EngineAddress(host="localhost", port=19001),
        stream=StreamKey(key_type="content_id", key="test_key_small"),
        session=SessionInfo(
            playback_session_id="test_session_small",
            stat_url="http://localhost:19001/stat",
            command_url="http://localhost:19001/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt)
    
    # Add stats with small values: 512 KB downloaded, 256 KB uploaded
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    downloaded_bytes = 512 * 1024  # 512 KB = 0.5 MB
    uploaded_bytes = 256 * 1024    # 256 KB = 0.25 MB
    
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=2,
        speed_down=128,
        speed_up=64,
        downloaded=downloaded_bytes,
        uploaded=uploaded_bytes,
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Update cumulative byte metrics
    on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify MB metrics with rounding
    assert orch_total_uploaded_mb._value.get() == 0.25, "Should have 0.25 MB uploaded"
    assert orch_total_downloaded_mb._value.get() == 0.5, "Should have 0.50 MB downloaded"
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    print("✅ MB metrics with small values test passed!")

def test_mb_metrics_with_zero_values():
    """Test that MB metrics handle zero values correctly."""
    from app.services.metrics import update_custom_metrics, on_stream_stat_update, reset_cumulative_metrics
    from app.services.metrics import orch_total_uploaded_mb, orch_total_downloaded_mb
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from app.services.db import engine
    from app.models.db_models import Base
    from datetime import datetime, timezone
    
    # Initialize database tables
    Base.metadata.create_all(bind=engine)
    
    # Clear state and metrics
    state.clear_state()
    reset_cumulative_metrics()
    
    # Add a mock engine and stream
    evt = StreamStartedEvent(
        container_id="test_container_zero_mb",
        engine=EngineAddress(host="localhost", port=19002),
        stream=StreamKey(key_type="content_id", key="test_key_zero_mb"),
        session=SessionInfo(
            playback_session_id="test_session_zero_mb",
            stat_url="http://localhost:19002/stat",
            command_url="http://localhost:19002/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt)
    
    # Add stats with zero values
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    
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
    
    # Update cumulative byte metrics
    on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify MB metrics show 0.0
    assert orch_total_uploaded_mb._value.get() == 0.0, "Should have 0.00 MB uploaded"
    assert orch_total_downloaded_mb._value.get() == 0.0, "Should have 0.00 MB downloaded"
    
    # Clean up
    state.clear_state()
    reset_cumulative_metrics()
    
    print("✅ MB metrics with zero values test passed!")

if __name__ == "__main__":
    test_mb_metrics_display()
    test_mb_metrics_with_small_values()
    test_mb_metrics_with_zero_values()
    print("\n✅ All MB metrics tests passed!")
