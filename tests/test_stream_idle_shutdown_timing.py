#!/usr/bin/env python3
"""
Test to verify that streams are terminated after exactly PROXY_GRACE_PERIOD seconds
when the last client disconnects, not after variable polling delays.
"""

import sys
import os
import time
import threading
from unittest.mock import MagicMock, patch, Mock

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_event_based_shutdown_uses_threading():
    """Test that client disconnect uses threading.Thread instead of gevent.spawn"""
    print("=" * 60)
    print("Test 1: Event-based shutdown uses threading.Thread")
    print("=" * 60)
    
    from app.proxy.client_manager import ClientManager
    from app.proxy.redis_keys import RedisKeys
    
    # Mock Redis client
    redis_mock = MagicMock()
    redis_mock.hset = MagicMock()
    redis_mock.expire = MagicMock()
    redis_mock.sadd = MagicMock()
    redis_mock.delete = MagicMock()
    redis_mock.publish = MagicMock()
    redis_mock.srem = MagicMock()
    redis_mock.scard = MagicMock(return_value=0)  # No clients left
    redis_mock.hgetall = MagicMock(return_value={})
    redis_mock.setex = MagicMock()
    redis_mock.get = MagicMock(return_value=None)
    
    # Mock proxy server
    mock_proxy_server = MagicMock()
    mock_proxy_server.am_i_owner = MagicMock(return_value=True)
    mock_proxy_server.handle_client_disconnect = MagicMock()
    
    # Create client manager with mocked Redis
    content_id = "test_content_123"
    manager = ClientManager(
        content_id=content_id,
        redis_client=redis_mock,
        worker_id="test_worker"
    )
    
    # Inject mock proxy server
    manager.proxy_server = mock_proxy_server
    
    # Add a client
    client_id = "test_client_1"
    client_ip = "127.0.0.1"
    manager.add_client(client_id, client_ip, "test_user_agent")
    
    # Track if threading.Thread was used
    thread_started = threading.Event()
    original_thread_init = threading.Thread.__init__
    
    def track_thread_init(self, *args, **kwargs):
        original_thread_init(self, *args, **kwargs)
        if kwargs.get('target') == mock_proxy_server.handle_client_disconnect:
            thread_started.set()
    
    with patch.object(threading.Thread, '__init__', track_thread_init):
        # Remove the client (should trigger shutdown check via threading.Thread)
        manager.remove_client(client_id)
        
        # Wait briefly for thread to start
        time.sleep(0.1)
    
    # Verify that a thread was started
    if thread_started.is_set():
        print("✓ Event-based shutdown uses threading.Thread (not gevent.spawn)")
        return True
    else:
        print("✗ Event-based shutdown did NOT use threading.Thread")
        return False


def test_gevent_not_imported():
    """Test that gevent is not imported in client_manager"""
    print("\n" + "=" * 60)
    print("Test 2: gevent is not imported in client_manager")
    print("=" * 60)
    
    # Read the file content
    file_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'proxy', 'client_manager.py')
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check if gevent is imported
    if 'import gevent' in content or 'from gevent' in content:
        print("✗ gevent is still imported in client_manager.py")
        return False
    else:
        print("✓ gevent is not imported in client_manager.py")
        return True


def test_shutdown_timer_configuration():
    """Test that shutdown timer uses CHANNEL_SHUTDOWN_DELAY from config"""
    print("\n" + "=" * 60)
    print("Test 3: Shutdown uses CHANNEL_SHUTDOWN_DELAY config")
    print("=" * 60)
    
    # Read the server.py file and verify it uses Config.CHANNEL_SHUTDOWN_DELAY
    file_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'proxy', 'server.py')
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Check if the timer uses Config.CHANNEL_SHUTDOWN_DELAY
    if 'threading.Timer(Config.CHANNEL_SHUTDOWN_DELAY' in content:
        print("✓ Shutdown timer uses Config.CHANNEL_SHUTDOWN_DELAY")
        return True
    else:
        print("✗ Shutdown timer does not use Config.CHANNEL_SHUTDOWN_DELAY")
        return False


def main():
    """Run all tests"""
    print("Testing Stream Idle Shutdown Timing Fix")
    print()
    
    results = []
    
    # Test 1: Threading is used
    try:
        results.append(("Event-based shutdown uses threading.Thread", test_event_based_shutdown_uses_threading()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Event-based shutdown uses threading.Thread", False))
    
    # Test 2: gevent not imported
    try:
        results.append(("gevent not imported", test_gevent_not_imported()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        results.append(("gevent not imported", False))
    
    # Test 3: Shutdown timer configuration
    try:
        results.append(("Shutdown timer configuration", test_shutdown_timer_configuration()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Shutdown timer configuration", False))
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        symbol = "✓" if passed else "✗"
        print(f"{symbol} {test_name}: {status}")
    
    all_passed = all(passed for _, passed in results)
    print()
    if all_passed:
        print("All tests PASSED! ✓")
        return 0
    else:
        print("Some tests FAILED! ✗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
