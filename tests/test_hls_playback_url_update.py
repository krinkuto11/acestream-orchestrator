#!/usr/bin/env python3
"""
Test script to verify HLS playback URL update functionality.
This validates that when multiple clients connect to the same channel,
the playback URL is properly updated to prevent 403 Forbidden errors.
"""

import sys
import os
import threading
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_playback_url_update():
    """Test that playback URL can be updated for existing channels"""
    print("=" * 60)
    print("Test: Playback URL Update for Existing Channels")
    print("=" * 60)
    
    from app.proxy.hls_proxy import HLSProxyServer, StreamManager
    
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
    assert manager.playback_url == playback_url_1, "Initial playback URL should be set"
    assert manager.playback_session_id == "session_1", "Initial session ID should be set"
    
    print(f"✓ Channel created: {channel_id}")
    print(f"✓ Initial playback URL: {playback_url_1}")
    print(f"✓ Initial session ID: {manager.playback_session_id}")
    
    # Simulate second client connecting (new session)
    session_info_2 = {
        'playback_session_id': 'session_2',
        'stat_url': 'http://example.com/stat_2',
        'command_url': 'http://example.com/command_2',
        'is_live': 1
    }
    
    playback_url_2 = "http://gluetun:19000/ace/m/hash2/session2.m3u8"
    
    # Initialize the same channel again (should update playback URL)
    proxy.initialize_channel(
        channel_id=channel_id,
        playback_url=playback_url_2,
        engine_host="gluetun",
        engine_port=19000,
        engine_container_id="engine_container_1",
        session_info=session_info_2,
        api_key=None
    )
    
    # Verify playback URL was updated
    assert manager.playback_url == playback_url_2, "Playback URL should be updated"
    assert manager.playback_session_id == "session_2", "Session ID should be updated"
    assert manager.stat_url == "http://example.com/stat_2", "Stat URL should be updated"
    assert manager.command_url == "http://example.com/command_2", "Command URL should be updated"
    
    print(f"✓ Playback URL updated: {playback_url_2}")
    print(f"✓ Session ID updated: {manager.playback_session_id}")
    print(f"✓ Stat URL updated: {manager.stat_url}")
    print(f"✓ Command URL updated: {manager.command_url}")
    
    # Verify only one channel exists (not duplicated)
    assert len(proxy.stream_managers) == 1, "Should only have one channel"
    print(f"✓ Channel count: {len(proxy.stream_managers)} (no duplicates)")
    
    # Cleanup
    proxy.stop_channel(channel_id)
    
    print()
    return True


def test_stream_manager_update_method():
    """Test StreamManager.update_playback_url() method directly"""
    print("=" * 60)
    print("Test: StreamManager.update_playback_url() Method")
    print("=" * 60)
    
    from app.proxy.hls_proxy import StreamManager
    
    # Create initial session info
    session_info_1 = {
        'playback_session_id': 'session_1',
        'stat_url': 'http://example.com/stat_1',
        'command_url': 'http://example.com/command_1',
        'is_live': 1
    }
    
    manager = StreamManager(
        playback_url="http://example.com/old.m3u8",
        channel_id="test_channel",
        engine_host="localhost",
        engine_port=6878,
        engine_container_id="test_container",
        session_info=session_info_1,
        api_key=None
    )
    
    old_url = manager.playback_url
    assert old_url == "http://example.com/old.m3u8", "Initial URL should be set"
    print(f"✓ Initial URL: {old_url}")
    
    # Update to new session
    session_info_2 = {
        'playback_session_id': 'session_2',
        'stat_url': 'http://example.com/stat_2',
        'command_url': 'http://example.com/command_2',
        'is_live': 1
    }
    
    new_url = "http://example.com/new.m3u8"
    manager.update_playback_url(new_url, session_info_2)
    
    assert manager.playback_url == new_url, "URL should be updated"
    assert manager.playback_session_id == "session_2", "Session ID should be updated"
    print(f"✓ Updated URL: {manager.playback_url}")
    print(f"✓ Updated session ID: {manager.playback_session_id}")
    
    # Cleanup
    manager.stop()
    
    print()
    return True


def test_thread_safe_url_update():
    """Test that playback URL updates are thread-safe"""
    print("=" * 60)
    print("Test: Thread-Safe Playback URL Updates")
    print("=" * 60)
    
    from app.proxy.hls_proxy import StreamManager
    
    session_info = {
        'playback_session_id': 'session_1',
        'stat_url': 'http://example.com/stat',
        'command_url': 'http://example.com/command',
        'is_live': 1
    }
    
    manager = StreamManager(
        playback_url="http://example.com/initial.m3u8",
        channel_id="test_channel",
        engine_host="localhost",
        engine_port=6878,
        engine_container_id="test_container",
        session_info=session_info,
        api_key=None
    )
    
    # Simulate concurrent URL reads and updates
    urls_read = []
    errors = []
    
    def read_url():
        """Simulate fetcher reading URL"""
        try:
            for _ in range(50):
                with manager._playback_url_lock:
                    url = manager.playback_url
                    urls_read.append(url)
                time.sleep(0.001)  # Small delay
        except Exception as e:
            errors.append(e)
    
    def update_url():
        """Simulate URL updates"""
        try:
            for i in range(10):
                new_session = {
                    'playback_session_id': f'session_{i}',
                    'stat_url': f'http://example.com/stat_{i}',
                    'command_url': f'http://example.com/command_{i}',
                    'is_live': 1
                }
                new_url = f"http://example.com/update_{i}.m3u8"
                manager.update_playback_url(new_url, new_session)
                time.sleep(0.005)  # Small delay
        except Exception as e:
            errors.append(e)
    
    # Start threads
    read_thread_1 = threading.Thread(target=read_url)
    read_thread_2 = threading.Thread(target=read_url)
    update_thread = threading.Thread(target=update_url)
    
    read_thread_1.start()
    read_thread_2.start()
    update_thread.start()
    
    # Wait for completion
    read_thread_1.join(timeout=5)
    read_thread_2.join(timeout=5)
    update_thread.join(timeout=5)
    
    # Verify no errors occurred
    assert len(errors) == 0, f"Should have no threading errors, got: {errors}"
    print(f"✓ No threading errors during concurrent access")
    
    # Verify some URLs were read
    assert len(urls_read) > 0, "Should have read some URLs"
    print(f"✓ Successfully read {len(urls_read)} URLs during concurrent access")
    
    # Verify final URL is from the last update
    assert manager.playback_url == "http://example.com/update_9.m3u8", "Final URL should be the last update"
    print(f"✓ Final URL is correct: {manager.playback_url}")
    
    # Cleanup
    manager.stop()
    
    print()
    return True


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("HLS Playback URL Update Validation")
    print("=" * 60 + "\n")
    
    tests = [
        test_stream_manager_update_method,
        test_thread_safe_url_update,
        test_playback_url_update,
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
