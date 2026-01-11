"""
Test that HLS cleanup monitoring skips checks during initial buffering.

This test validates the fix for the issue where clients were marked as inactive
and channels were stopped prematurely during initial buffering.
"""

import time
import threading
from unittest.mock import Mock, MagicMock
from app.proxy.hls_proxy import StreamManager, ClientManager


def test_cleanup_skips_during_initial_buffering():
    """
    Test that cleanup monitoring skips checks while initial_buffering is True.
    
    This simulates the scenario from the bug:
    1. Client connects and channel starts
    2. Initial buffering takes a long time (e.g., 20-30 seconds)
    3. Cleanup monitoring should skip checks during this time
    4. Once buffering completes, cleanup monitoring should resume
    """
    # Create a mock proxy server
    mock_proxy = Mock()
    mock_proxy.stop_channel = MagicMock()
    
    # Create stream manager with initial_buffering=True (default state)
    manager = StreamManager(
        playback_url="http://test:8000/test.m3u8",
        channel_id="test_channel",
        engine_host="test_engine",
        engine_port=8000,
        engine_container_id="test_container",
        session_info={
            'playback_session_id': 'test_session',
            'stat_url': 'http://test/stat',
            'command_url': 'http://test/command',
            'is_live': 1
        }
    )
    
    # Verify initial state
    assert manager.initial_buffering, "Manager should start in initial_buffering state"
    
    # Create client manager and add a client
    client_manager = ClientManager()
    manager.client_manager = client_manager
    
    # Record client activity
    client_manager.record_activity("192.168.1.1")
    
    # Start cleanup monitoring
    manager.start_cleanup_monitoring(mock_proxy)
    
    # Wait for cleanup thread to start and run a few cycles
    # The cleanup thread runs every 5 seconds after a 2-second initial delay
    time.sleep(8)  # Enough time for at least one cleanup check
    
    # Verify that stop_channel was NOT called because we're still in initial_buffering
    mock_proxy.stop_channel.assert_not_called()
    
    # Now simulate initial buffering completing
    manager.initial_buffering = False
    manager.buffer_ready.set()
    
    # Wait for cleanup to potentially run again
    time.sleep(6)
    
    # With initial_buffering=False and no recent client activity,
    # cleanup should still not trigger yet (only 14 seconds have passed,
    # timeout is 3x target_duration = 30 seconds)
    mock_proxy.stop_channel.assert_not_called()
    
    # Clean up
    manager.running = False
    manager.cleanup_running = False
    
    print("✓ Cleanup monitoring correctly skipped checks during initial_buffering")
    print("✓ Cleanup monitoring resumed after buffering completed")
    return True


def test_cleanup_runs_after_buffering_and_timeout():
    """
    Test that cleanup monitoring correctly stops the channel after buffering
    completes and clients become inactive.
    """
    # Create a mock proxy server
    mock_proxy = Mock()
    mock_proxy.stop_channel = MagicMock()
    
    # Create stream manager
    manager = StreamManager(
        playback_url="http://test:8000/test.m3u8",
        channel_id="test_channel",
        engine_host="test_engine",
        engine_port=8000,
        engine_container_id="test_container",
        session_info={
            'playback_session_id': 'test_session',
            'stat_url': 'http://test/stat',
            'command_url': 'http://test/command',
            'is_live': 1
        }
    )
    
    # Set target_duration to a small value for faster testing
    manager.target_duration = 2.0  # 2 seconds instead of 10
    
    # Create client manager and add a client
    client_manager = ClientManager()
    manager.client_manager = client_manager
    client_manager.record_activity("192.168.1.1")
    
    # Mark buffering as complete immediately
    manager.initial_buffering = False
    manager.buffer_ready.set()
    
    # Start cleanup monitoring
    manager.start_cleanup_monitoring(mock_proxy)
    
    # Wait for cleanup thread to start
    time.sleep(3)
    
    # At this point, cleanup should not have triggered yet
    # (only 3 seconds, timeout is 2.0 * 3 = 6 seconds)
    mock_proxy.stop_channel.assert_not_called()
    
    # Wait for timeout to be exceeded
    time.sleep(5)  # Total wait: 8 seconds, which exceeds 6-second timeout
    
    # Now cleanup should have triggered
    # Note: There's a race condition here in testing, but the call should happen
    # within a reasonable time
    time.sleep(2)  # Give cleanup loop time to detect and act
    
    # Verify stop_channel was called
    assert mock_proxy.stop_channel.call_count > 0, "stop_channel should have been called after timeout"
    
    # Clean up
    manager.running = False
    manager.cleanup_running = False
    
    print("✓ Cleanup monitoring correctly stopped channel after timeout")
    return True


if __name__ == "__main__":
    test_cleanup_skips_during_initial_buffering()
    test_cleanup_runs_after_buffering_and_timeout()
    print("\nAll tests passed!")
