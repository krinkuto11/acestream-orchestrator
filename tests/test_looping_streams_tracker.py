"""Test looping streams tracker functionality."""
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from app.services.looping_streams import looping_streams_tracker


def test_looping_streams_tracker_initialization():
    """Test that the looping streams tracker can be initialized."""
    assert looping_streams_tracker is not None


def test_add_looping_stream():
    """Test adding a stream to the looping streams list."""
    # Clear any existing streams
    looping_streams_tracker.clear_all()
    
    # Add a stream
    stream_id = "test_stream_12345"
    looping_streams_tracker.add_looping_stream(stream_id)
    
    # Verify it was added
    assert looping_streams_tracker.is_looping(stream_id)
    assert stream_id in looping_streams_tracker.get_looping_stream_ids()


def test_remove_looping_stream():
    """Test removing a stream from the looping streams list."""
    # Clear and add a stream
    looping_streams_tracker.clear_all()
    stream_id = "test_stream_12345"
    looping_streams_tracker.add_looping_stream(stream_id)
    
    # Remove it
    removed = looping_streams_tracker.remove_looping_stream(stream_id)
    assert removed
    
    # Verify it was removed
    assert not looping_streams_tracker.is_looping(stream_id)
    
    # Try to remove again (should return False)
    removed = looping_streams_tracker.remove_looping_stream(stream_id)
    assert not removed


def test_get_looping_streams():
    """Test getting all looping streams with timestamps."""
    # Clear and add some streams
    looping_streams_tracker.clear_all()
    
    stream1 = "stream_1"
    stream2 = "stream_2"
    
    looping_streams_tracker.add_looping_stream(stream1)
    looping_streams_tracker.add_looping_stream(stream2)
    
    # Get all streams
    streams = looping_streams_tracker.get_looping_streams()
    
    # Verify both are present
    assert stream1 in streams
    assert stream2 in streams
    
    # Verify timestamps are ISO formatted
    assert isinstance(streams[stream1], str)
    assert isinstance(streams[stream2], str)
    
    # Verify we can parse the timestamps
    datetime.fromisoformat(streams[stream1])
    datetime.fromisoformat(streams[stream2])


def test_clear_all():
    """Test clearing all looping streams."""
    # Add some streams
    looping_streams_tracker.add_looping_stream("stream_1")
    looping_streams_tracker.add_looping_stream("stream_2")
    looping_streams_tracker.add_looping_stream("stream_3")
    
    # Clear all
    looping_streams_tracker.clear_all()
    
    # Verify all are cleared
    streams = looping_streams_tracker.get_looping_stream_ids()
    assert len(streams) == 0


def test_retention_configuration():
    """Test retention time configuration."""
    # Test indefinite retention (0 or None)
    looping_streams_tracker.set_retention_minutes(0)
    assert looping_streams_tracker.get_retention_minutes() is None
    
    looping_streams_tracker.set_retention_minutes(None)
    assert looping_streams_tracker.get_retention_minutes() is None
    
    # Test specific retention time
    looping_streams_tracker.set_retention_minutes(60)
    assert looping_streams_tracker.get_retention_minutes() == 60


@pytest.mark.asyncio
async def test_start_stop_tracker():
    """Test starting and stopping the tracker."""
    # Start tracker
    await looping_streams_tracker.start()
    
    # Stop tracker
    await looping_streams_tracker.stop()
    
    # Should complete without error
    assert True


def test_looping_streams_api_endpoint():
    """Test the /looping-streams API endpoint."""
    from app.main import app
    from fastapi.testclient import TestClient
    from app.core.config import cfg
    
    # Save original API key
    original_api_key = cfg.API_KEY
    
    try:
        # Set a test API key
        cfg.API_KEY = "test-looping-streams-key"
        
        # Create test client
        client = TestClient(app)
        headers = {"Authorization": "Bearer test-looping-streams-key"}
        
        # Clear any existing streams
        looping_streams_tracker.clear_all()
        
        # Test 1: GET /looping-streams (empty)
        response = client.get("/looping-streams")
        assert response.status_code == 200
        data = response.json()
        assert "stream_ids" in data
        assert "streams" in data
        assert "retention_minutes" in data
        assert len(data["stream_ids"]) == 0
        
        # Add a stream
        test_stream = "test_content_id_12345"
        looping_streams_tracker.add_looping_stream(test_stream)
        
        # Test 2: GET /looping-streams (with stream)
        response = client.get("/looping-streams")
        assert response.status_code == 200
        data = response.json()
        assert len(data["stream_ids"]) == 1
        assert test_stream in data["stream_ids"]
        assert test_stream in data["streams"]
        
        # Test 3: DELETE /looping-streams/{stream_id}
        response = client.delete(f"/looping-streams/{test_stream}", headers=headers)
        assert response.status_code == 200
        
        # Verify it was removed
        response = client.get("/looping-streams")
        data = response.json()
        assert len(data["stream_ids"]) == 0
        
        # Test 4: DELETE non-existent stream
        response = client.delete("/looping-streams/nonexistent", headers=headers)
        assert response.status_code == 404
        
        # Test 5: POST /looping-streams/clear
        looping_streams_tracker.add_looping_stream("stream1")
        looping_streams_tracker.add_looping_stream("stream2")
        
        response = client.post("/looping-streams/clear", headers=headers)
        assert response.status_code == 200
        
        # Verify all cleared
        response = client.get("/looping-streams")
        data = response.json()
        assert len(data["stream_ids"]) == 0
        
        print("✅ All looping streams API endpoint tests passed")
        
    finally:
        # Restore original API key
        cfg.API_KEY = original_api_key
        # Clean up
        looping_streams_tracker.clear_all()
