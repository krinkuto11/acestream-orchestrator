"""Test that stream playback is blocked for looping streams."""
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.looping_streams import looping_streams_tracker


def test_stream_blocked_for_looping():
    """Test that stream playback is blocked when stream is on looping blacklist."""
    # Create test client
    client = TestClient(app)
    
    # Clear any existing looping streams
    looping_streams_tracker.clear_all()
    
    # Add a test stream to the looping blacklist
    test_content_id = "test_looping_stream_abc123"
    looping_streams_tracker.add_looping_stream(test_content_id)
    
    # Try to stream the blacklisted content
    response = client.get(f"/ace/getstream?id={test_content_id}")
    
    # Should be blocked with 422 status
    assert response.status_code == 422
    
    # Verify the error details
    error = response.json()
    assert "detail" in error
    detail = error["detail"]
    assert detail["error"] == "stream_blacklisted"
    assert detail["code"] == "looping_stream"
    
    print(f"✅ Stream playback correctly blocked for looping stream: {test_content_id}")
    print(f"   Error message: {detail['message']}")
    
    # Clean up
    looping_streams_tracker.clear_all()


def test_stream_allowed_for_non_looping():
    """Test that stream playback is allowed when stream is NOT on looping blacklist."""
    # Create test client
    client = TestClient(app)
    
    # Clear any existing looping streams
    looping_streams_tracker.clear_all()
    
    # Try to stream a non-blacklisted content
    test_content_id = "test_normal_stream_xyz789"
    response = client.get(f"/ace/getstream?id={test_content_id}")
    
    # Should not be blocked with 422 for looping
    # (may fail with other errors like no engines available, but NOT 422 for looping)
    if response.status_code == 422:
        error = response.json()
        detail = error.get("detail", {})
        # Make sure it's NOT blocked for being a looping stream
        assert detail.get("code") != "looping_stream", \
            f"Stream should not be blocked for looping, got: {detail}"
    
    print(f"✅ Stream playback not blocked for non-looping stream: {test_content_id}")
    
    # Clean up
    looping_streams_tracker.clear_all()


if __name__ == "__main__":
    test_stream_blocked_for_looping()
    test_stream_allowed_for_non_looping()
    print("\n✅ All stream blacklist tests passed!")

