"""
Test that httpx logs are suppressed.

This test validates that:
1. The logging setup configures httpx logger to WARNING level
2. httpx INFO and DEBUG logs are not emitted
"""

import sys
import os
import logging
import io

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.logging import setup


def test_httpx_logger_level():
    """Test that httpx logger is set to WARNING level."""
    # Setup logging
    setup()
    
    # Get the httpx logger
    httpx_logger = logging.getLogger("httpx")
    
    # Verify it's set to WARNING
    assert httpx_logger.level == logging.WARNING, f"Expected WARNING ({logging.WARNING}), got {httpx_logger.level}"
    print(f"✓ httpx logger level is correctly set to WARNING ({logging.WARNING})")


def test_httpx_info_not_logged():
    """Test that httpx INFO messages are not logged."""
    # Setup logging
    setup()
    
    # Create a string stream to capture log output
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    
    # Get the httpx logger and add our test handler
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.addHandler(handler)
    
    # Log an INFO message (should not appear)
    httpx_logger.info("This is a test INFO message from httpx")
    
    # Log a WARNING message (should appear)
    httpx_logger.warning("This is a test WARNING message from httpx")
    
    # Get the logged content
    log_output = log_stream.getvalue()
    
    # Verify INFO message is not present
    assert "test INFO message" not in log_output, "INFO message should not be logged"
    print("✓ httpx INFO messages are correctly suppressed")
    
    # Verify WARNING message is present
    assert "test WARNING message" in log_output, "WARNING message should be logged"
    print("✓ httpx WARNING messages are correctly logged")
    
    # Clean up
    httpx_logger.removeHandler(handler)


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Running httpx logging suppression tests")
    print("=" * 60)
    
    try:
        test_httpx_logger_level()
        test_httpx_info_not_logged()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
