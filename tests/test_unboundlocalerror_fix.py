#!/usr/bin/env python3
"""
Test that the UnboundLocalError in update_custom_metrics is fixed.

This test verifies the fix for the error:
  UnboundLocalError: cannot access local variable 'total_uploaded' 
  where it is not associated with a value

The issue was in lines 129-132 of metrics.py where total_uploaded and 
total_downloaded were referenced but never initialized.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Initialize database
from app.models.db_models import Base
from app.services.db import engine
Base.metadata.create_all(bind=engine)

def test_update_custom_metrics_no_unboundlocalerror():
    """Test that update_custom_metrics does not raise UnboundLocalError."""
    from app.services.metrics import update_custom_metrics
    from app.services.state import state
    from app.models.schemas import (
        StreamStartedEvent, EngineAddress, 
        StreamKey, SessionInfo, StreamStatSnapshot
    )
    from datetime import datetime, timezone
    
    # Clear state to start fresh
    state.clear_state()
    
    # Add a stream with uploaded/downloaded stats to trigger the code path
    evt = StreamStartedEvent(
        container_id="test_container",
        engine=EngineAddress(host="localhost", port=19001),
        stream=StreamKey(key_type="content_id", key="test_key"),
        session=SessionInfo(
            playback_session_id="test_session",
            stat_url="http://localhost:19001/stat",
            command_url="http://localhost:19001/command",
            is_live=1
        ),
        labels={"test": "true"}
    )
    state.on_stream_started(evt)
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    
    # Add stats with uploaded and downloaded values
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=5,
        speed_down=100000,
        speed_up=50000,
        downloaded=1000,
        uploaded=500,
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # This should NOT raise UnboundLocalError anymore
    try:
        update_custom_metrics()
        print("✓ update_custom_metrics() executed without UnboundLocalError")
    except UnboundLocalError as e:
        raise AssertionError(f"UnboundLocalError should be fixed but got: {e}")
    
    # Clean up
    state.clear_state()
    
    print("✅ UnboundLocalError fix test passed!")

if __name__ == "__main__":
    test_update_custom_metrics_no_unboundlocalerror()
