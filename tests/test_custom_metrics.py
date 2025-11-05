#!/usr/bin/env python3
"""
Test custom Prometheus metrics endpoint.

Validates that the new aggregated metrics are properly exposed and updated.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Initialize database
from app.models.db_models import Base
from app.services.db import engine
Base.metadata.create_all(bind=engine)

def test_metrics_function():
    """Test that update_custom_metrics function works correctly."""
    from app.services.metrics import update_custom_metrics
    from app.services.metrics import (
        orch_total_uploaded_bytes,
        orch_total_downloaded_bytes,
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
        orch_total_peers,
        orch_total_streams,
        orch_healthy_engines,
        orch_unhealthy_engines,
        orch_used_engines,
        orch_vpn_health,
        orch_extra_engines
    )
    
    # Call update function (should not raise exceptions)
    update_custom_metrics()
    
    # Verify metrics exist and have expected types
    assert orch_total_uploaded_bytes._value.get() >= 0
    assert orch_total_downloaded_bytes._value.get() >= 0
    assert orch_total_upload_speed_mbps._value.get() >= 0
    assert orch_total_download_speed_mbps._value.get() >= 0
    assert orch_total_peers._value.get() >= 0
    assert orch_total_streams._value.get() >= 0
    assert orch_healthy_engines._value.get() >= 0
    assert orch_unhealthy_engines._value.get() >= 0
    assert orch_used_engines._value.get() >= 0
    assert orch_extra_engines._value.get() >= 0
    
    # VPN health metric is an Enum, just verify it exists and can be read
    # The Enum type doesn't have a simple .get() method, so we skip detailed validation
    assert orch_vpn_health is not None
    
    print("✅ Custom metrics function tests passed!")

def test_metrics_with_mock_data():
    """Test metrics calculation with mock stream data."""
    from app.services.metrics import update_custom_metrics, on_stream_stat_update
    from app.services.metrics import (
        orch_total_uploaded_bytes,
        orch_total_downloaded_bytes,
        orch_total_peers,
        orch_total_streams,
        orch_used_engines
    )
    from app.services.state import state
    from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo, StreamStatSnapshot
    from datetime import datetime, timezone
    
    # Clear state
    state.clear_state()
    
    # Add a mock engine and stream
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
    
    # Add some stats
    # Use the same stream_id format as the state module (key|playback_session_id)
    stream_id = f"{evt.stream.key}|{evt.session.playback_session_id}"
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=10,
        speed_down=1024,      # 1 MB/s = 1024 KB/s (AceStream API returns speeds in KB/s)
        speed_up=512,         # 0.5 MB/s = 512 KB/s (AceStream API returns speeds in KB/s)
        downloaded=10485760,  # 10 MB in bytes
        uploaded=5242880,     # 5 MB in bytes
        status="active"
    )
    state.append_stat(stream_id, snap)
    
    # Update cumulative metrics (simulating what collector does)
    on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify metrics reflect the mock data
    assert orch_total_streams._value.get() == 1
    assert orch_total_peers._value.get() == 10
    assert orch_total_uploaded_bytes._value.get() == 5242880
    assert orch_total_downloaded_bytes._value.get() == 10485760
    assert orch_used_engines._value.get() == 1
    
    # Clean up
    state.clear_state()
    
    print("✅ Custom metrics with mock data tests passed!")

def test_vpn_health_enum_states():
    """Test that VPN health enum metric accepts all possible states."""
    from app.services.metrics import orch_vpn_health
    
    # Test all possible VPN health states
    valid_states = ["healthy", "unhealthy", "unknown", "disabled", "starting"]
    
    for state in valid_states:
        try:
            orch_vpn_health.state(state)
            print(f"  ✓ VPN health state '{state}' accepted")
        except ValueError as e:
            raise AssertionError(f"VPN health state '{state}' should be valid but got error: {e}")
    
    print("✅ VPN health enum states test passed!")

if __name__ == "__main__":
    test_metrics_function()
    test_metrics_with_mock_data()
    test_vpn_health_enum_states()
    print("\n✅ All custom metrics tests passed!")
