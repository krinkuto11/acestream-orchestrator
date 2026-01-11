#!/usr/bin/env python3
"""
Test script to verify HLS channel reuse and has_channel() functionality.
This validates that when multiple clients connect to the same channel,
existing channels are reused instead of creating new engine sessions.
"""

import sys
import os
import threading
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_has_channel():
    """Test that has_channel() correctly detects existing channels"""
    print("=" * 60)
    print("Test: has_channel() Method")
    print("=" * 60)
    
    from app.proxy.hls_proxy import HLSProxyServer
    
    # Reset singleton instance for clean test
    HLSProxyServer._instance = None
    
    # Create proxy instance
    proxy = HLSProxyServer.get_instance()
    
    # Create session info
    session_info = {
        'playback_session_id': 'session_1',
        'stat_url': 'http://example.com/stat_1',
        'command_url': 'http://example.com/command_1',
        'is_live': 1
    }
    
    channel_id = "test_channel_abc123"
    
    # Verify channel doesn't exist initially
    assert not proxy.has_channel(channel_id), "Channel should not exist initially"
    print(f"✓ Channel {channel_id} doesn't exist initially")
    
    # Initialize channel
    proxy.initialize_channel(
        channel_id=channel_id,
        playback_url="http://gluetun:19000/ace/m/hash1/session1.m3u8",
        engine_host="gluetun",
        engine_port=19000,
        engine_container_id="engine_container_1",
        session_info=session_info,
        api_key=None
    )
    
    # Verify channel now exists
    assert proxy.has_channel(channel_id), "Channel should exist after initialization"
    print(f"✓ Channel {channel_id} exists after initialization")
    
    # Cleanup
    proxy.stop_channel(channel_id)
    
    # Verify channel no longer exists
    assert not proxy.has_channel(channel_id), "Channel should not exist after cleanup"
    print(f"✓ Channel {channel_id} doesn't exist after cleanup")
    
    print()
    return True


def test_channel_reuse():
    """Test that existing channels are reused without modification"""
    print("=" * 60)
    print("Test: Channel Reuse for Multiple Clients")
    print("=" * 60)
    
    from app.proxy.hls_proxy import HLSProxyServer
    
    # Reset singleton instance for clean test
    HLSProxyServer._instance = None
    
    # Create proxy instance
    proxy = HLSProxyServer.get_instance()
    
    # Create initial session info
    session_info_1 = {
        'playback_session_id': 'session_1',
        'stat_url': 'http://example.com/stat_1',
        'command_url': 'http://example.com/command_1',
        'is_live': 1
    }
    
    # Initialize first channel
    channel_id = "test_channel_abc123"
    playback_url_1 = "http://gluetun:19000/ace/m/hash1/session1.m3u8"
    
    proxy.initialize_channel(
        channel_id=channel_id,
        playback_url=playback_url_1,
        engine_host="gluetun",
        engine_port=19000,
        engine_container_id="engine_container_1",
        session_info=session_info_1,
        api_key=None
    )
    
    # Verify channel was created
    assert channel_id in proxy.stream_managers, "Channel should be created"
    manager = proxy.stream_managers[channel_id]
    initial_url = manager.playback_url
    initial_session_id = manager.playback_session_id
    
    assert initial_url == playback_url_1, "Initial playback URL should be set"
    assert initial_session_id == "session_1", "Initial session ID should be set"
    
    print(f"✓ Channel created: {channel_id}")
    print(f"✓ Initial playback URL: {playback_url_1}")
    print(f"✓ Initial session ID: {initial_session_id}")
    
    # Simulate second client - should NOT change the channel
    # In the new implementation, this call should be skipped in main.py
    # But if it is called, it should not modify the existing channel
    
    # Try to initialize again (this should be detected and skipped)
    proxy.initialize_channel(
        channel_id=channel_id,
        playback_url="http://gluetun:19000/ace/m/hash2/session2.m3u8",
        engine_host="gluetun",
        engine_port=19000,
        engine_container_id="engine_container_1",
        session_info={
            'playback_session_id': 'session_2',
            'stat_url': 'http://example.com/stat_2',
            'command_url': 'http://example.com/command_2',
            'is_live': 1
        },
        api_key=None
    )
    
    # Verify the channel was NOT modified (URL and session should remain the same)
    assert manager.playback_url == initial_url, "Playback URL should NOT change"
    assert manager.playback_session_id == initial_session_id, "Session ID should NOT change"
    
    print(f"✓ Channel reused without modification")
    print(f"✓ Playback URL unchanged: {manager.playback_url}")
    print(f"✓ Session ID unchanged: {manager.playback_session_id}")
    
    # Verify only one channel exists (not duplicated)
    assert len(proxy.stream_managers) == 1, "Should only have one channel"
    print(f"✓ Channel count: {len(proxy.stream_managers)} (no duplicates)")
    
    # Cleanup
    proxy.stop_channel(channel_id)
    
    print()
    return True


def test_thread_safe_channel_check():
    """Test that has_channel() is thread-safe"""
    print("=" * 60)
    print("Test: Thread-Safe Channel Checks")
    print("=" * 60)
    
    from app.proxy.hls_proxy import HLSProxyServer
    
    # Reset singleton instance for clean test
    HLSProxyServer._instance = None
    
    proxy = HLSProxyServer.get_instance()
    
    session_info = {
        'playback_session_id': 'session_1',
        'stat_url': 'http://example.com/stat',
        'command_url': 'http://example.com/command',
        'is_live': 1
    }
    
    channel_id = "test_channel"
    checks_performed = []
    errors = []
    
    def check_channel():
        """Simulate concurrent channel checks"""
        try:
            for _ in range(50):
                exists = proxy.has_channel(channel_id)
                checks_performed.append(exists)
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)
    
    def init_channel():
        """Simulate channel initialization"""
        try:
            time.sleep(0.025)  # Let some checks run first
            proxy.initialize_channel(
                channel_id=channel_id,
                playback_url="http://example.com/test.m3u8",
                engine_host="localhost",
                engine_port=6878,
                engine_container_id="test_container",
                session_info=session_info,
                api_key=None
            )
        except Exception as e:
            errors.append(e)
    
    # Start threads
    check_thread_1 = threading.Thread(target=check_channel)
    check_thread_2 = threading.Thread(target=check_channel)
    init_thread = threading.Thread(target=init_channel)
    
    check_thread_1.start()
    check_thread_2.start()
    init_thread.start()
    
    # Wait for completion
    check_thread_1.join(timeout=5)
    check_thread_2.join(timeout=5)
    init_thread.join(timeout=5)
    
    # Verify no errors occurred
    assert len(errors) == 0, f"Should have no threading errors, got: {errors}"
    print(f"✓ No threading errors during concurrent access")
    
    # Verify checks were performed
    assert len(checks_performed) > 0, "Should have performed some checks"
    print(f"✓ Successfully performed {len(checks_performed)} channel checks during concurrent access")
    
    # Verify channel exists at the end
    assert proxy.has_channel(channel_id), "Channel should exist after initialization"
    print(f"✓ Channel exists after concurrent operations")
    
    # Cleanup
    proxy.stop_channel(channel_id)
    
    print()
    return True


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("HLS Channel Reuse Validation")
    print("=" * 60 + "\n")
    
    tests = [
        test_has_channel,
        test_channel_reuse,
        test_thread_safe_channel_check,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"✗ Test failed: {test.__name__}")
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
            print()
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
