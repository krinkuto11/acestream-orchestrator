#!/usr/bin/env python3
"""
Test custom Prometheus metrics endpoint.

Validates that the new aggregated metrics are properly exposed and updated.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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
    from app.services.metrics import update_custom_metrics
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
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=10,
        speed_down=1048576,  # 1 MB/s in bytes
        speed_up=524288,     # 0.5 MB/s in bytes
        downloaded=10485760,  # 10 MB in bytes
        uploaded=5242880,     # 5 MB in bytes
        status="active"
    )
    state.append_stat(evt.stream.key + "|" + evt.session.playback_session_id, snap)
    
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

if __name__ == "__main__":
    test_metrics_function()
    test_metrics_with_mock_data()
    print("\n✅ All custom metrics tests passed!")
