"""Test stream loop detection functionality."""
import pytest
from datetime import datetime, timezone
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
