"""Test stream loop detection functionality."""
import pytest
import os
from datetime import datetime, timezone
from unittest.mock import patch
from app.core.config import cfg
from app.services.stream_loop_detector import StreamLoopDetector


def test_loop_detector_initialization():
    """Test that loop detector can be initialized."""
    detector = StreamLoopDetector()
    assert detector is not None
    assert detector._task is None
    assert detector._stop is not None


def test_loop_detector_config():
    """Test that loop detection configuration is loaded."""
    # Default config should be loaded
    assert hasattr(cfg, 'STREAM_LOOP_DETECTION_ENABLED')
    assert hasattr(cfg, 'STREAM_LOOP_DETECTION_THRESHOLD_S')
    
    # Default values
    assert cfg.STREAM_LOOP_DETECTION_ENABLED in [True, False]
    assert isinstance(cfg.STREAM_LOOP_DETECTION_THRESHOLD_S, int)
    assert cfg.STREAM_LOOP_DETECTION_THRESHOLD_S >= 60  # At least 60 seconds


def test_timestamp_validation():
    """Test timestamp validation constants."""
    from app.services.stream_loop_detector import StreamLoopDetector
    
    detector = StreamLoopDetector()
    
    # Test valid timestamp (2025)
    valid_timestamp = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
    assert valid_timestamp > 1577836800  # After 2020
    assert valid_timestamp < 2524608000  # Before 2050
    
    # Test invalid timestamps
    invalid_old = 1000000000  # Before 2020
    invalid_new = 3000000000  # After 2050
    
    assert invalid_old < 1577836800
    assert invalid_new > 2524608000


@pytest.mark.asyncio
async def test_loop_detector_start_stop():
    """Test that loop detector can be started and stopped."""
    detector = StreamLoopDetector()
    
    # Start detector (won't actually run if disabled in config)
    await detector.start()
    
    # Stop detector
    await detector.stop()
    
    # Should complete without error
    assert True


def test_config_threshold_update():
    """Test that config can be updated at runtime."""
    # Save original values
    original_enabled = cfg.STREAM_LOOP_DETECTION_ENABLED
    original_threshold = cfg.STREAM_LOOP_DETECTION_THRESHOLD_S
    
    try:
        # Update config
        cfg.STREAM_LOOP_DETECTION_ENABLED = True
        cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = 7200  # 2 hours
        
        assert cfg.STREAM_LOOP_DETECTION_ENABLED == True
        assert cfg.STREAM_LOOP_DETECTION_THRESHOLD_S == 7200
        
    finally:
        # Restore original values
        cfg.STREAM_LOOP_DETECTION_ENABLED = original_enabled
        cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = original_threshold


def test_update_stream_loop_detection_config_endpoint():
    """Test the /stream-loop-detection/config API endpoint."""
    from app.main import app
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock, patch
    
    # Save original values
    original_enabled = cfg.STREAM_LOOP_DETECTION_ENABLED
    original_threshold = cfg.STREAM_LOOP_DETECTION_THRESHOLD_S
    original_api_key = cfg.API_KEY
    
    try:
        # Set a test API key
        cfg.API_KEY = "test-stream-loop-key"
        
        # Create test client
        client = TestClient(app)
        headers = {"Authorization": "Bearer test-stream-loop-key"}
        
        # Mock the stream_loop_detector methods to avoid async issues in testing
        with patch('app.main.stream_loop_detector.stop', new_callable=AsyncMock) as mock_stop:
            with patch('app.main.stream_loop_detector.start', new_callable=AsyncMock) as mock_start:
                
                # Test 1: Enable with valid threshold
                response = client.post(
                    "/stream-loop-detection/config",
                    params={"enabled": True, "threshold_seconds": 3600},
                    headers=headers
                )
                assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
                data = response.json()
                assert data["enabled"] == True
                assert data["threshold_seconds"] == 3600
                assert data["threshold_minutes"] == 60
                assert data["threshold_hours"] == 1
                assert "message" in data
                
                # Verify config was updated
                assert cfg.STREAM_LOOP_DETECTION_ENABLED == True
                assert cfg.STREAM_LOOP_DETECTION_THRESHOLD_S == 3600
                
                # Verify methods were called
                assert mock_stop.called
                assert mock_start.called
                
                # Reset mocks
                mock_stop.reset_mock()
                mock_start.reset_mock()
                
                # Test 2: Disable
                response = client.post(
                    "/stream-loop-detection/config",
                    params={"enabled": False, "threshold_seconds": 7200},
                    headers=headers
                )
                assert response.status_code == 200
                data = response.json()
                assert data["enabled"] == False
                assert data["threshold_seconds"] == 7200
                
                # Verify config was updated
                assert cfg.STREAM_LOOP_DETECTION_ENABLED == False
                assert cfg.STREAM_LOOP_DETECTION_THRESHOLD_S == 7200
                
                # Verify stop was called but not start (disabled)
                assert mock_stop.called
                assert not mock_start.called
        
        # Test 3: Reject threshold below 60 seconds
        response = client.post(
            "/stream-loop-detection/config",
            params={"enabled": True, "threshold_seconds": 30},
            headers=headers
        )
        assert response.status_code == 400
        assert "Threshold must be at least 60 seconds" in response.text
        
        # Test 4: Reject without API key
        response = client.post(
            "/stream-loop-detection/config",
            params={"enabled": True, "threshold_seconds": 3600}
        )
        assert response.status_code == 403 or response.status_code == 401
        
        print("✅ All stream loop detection config endpoint tests passed")
        
    finally:
        # Restore original values
        cfg.STREAM_LOOP_DETECTION_ENABLED = original_enabled
        cfg.STREAM_LOOP_DETECTION_THRESHOLD_S = original_threshold
        cfg.API_KEY = original_api_key
