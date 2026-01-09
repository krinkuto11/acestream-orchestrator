#!/usr/bin/env python3
"""
Test script to validate the idle stream cleanup fix and MAX_STREAMS_PER_ENGINE configuration.

This script verifies:
1. The last_client_disconnect key is cleared when a client connects
2. MAX_STREAMS_PER_ENGINE is configurable via API
3. MAX_STREAMS_PER_ENGINE persists across restarts
"""

import os
import sys
import time
import json
from unittest.mock import Mock, MagicMock

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_last_disconnect_cleared_on_connect():
    """Test that last_client_disconnect key is cleared when client connects"""
    print("=" * 60)
    print("Test 1: Last Disconnect Key Cleared on Client Connect")
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
    redis_mock.scard = MagicMock(return_value=1)
    
    # Create client manager with mocked Redis
    content_id = "test_content_123"
    manager = ClientManager(
        content_id=content_id,
        redis_client=redis_mock,
        worker_id="test_worker"
    )
    
    # Add a client
    client_id = "test_client_1"
    client_ip = "127.0.0.1"
    manager.add_client(client_id, client_ip, "test_user_agent")
    
    # Verify that delete was called for last_client_disconnect
    disconnect_key = RedisKeys.last_client_disconnect(content_id)
    delete_calls = [call[0][0] for call in redis_mock.delete.call_args_list]
    
    if disconnect_key in delete_calls:
        print(f"✓ last_client_disconnect key '{disconnect_key}' was deleted when client connected")
        return True
    else:
        print(f"✗ last_client_disconnect key was NOT deleted")
        print(f"  Expected key: {disconnect_key}")
        print(f"  Delete calls: {delete_calls}")
        return False


def test_max_streams_per_engine_api():
    """Test that MAX_STREAMS_PER_ENGINE is available in proxy config API"""
    print("\n" + "=" * 60)
    print("Test 2: MAX_STREAMS_PER_ENGINE in Proxy Config API")
    print("=" * 60)
    
    from app.core.config import cfg
    
    # Set initial value
    cfg.ACEXY_MAX_STREAMS_PER_ENGINE = 5
    
    # Import the endpoint after setting the config
    from app.main import get_proxy_config
    
    # Call the GET endpoint
    config = get_proxy_config()
    
    if 'max_streams_per_engine' in config:
        if config['max_streams_per_engine'] == 5:
            print(f"✓ max_streams_per_engine present in API response: {config['max_streams_per_engine']}")
            return True
        else:
            print(f"✗ max_streams_per_engine has wrong value: {config['max_streams_per_engine']} (expected 5)")
            return False
    else:
        print(f"✗ max_streams_per_engine NOT present in API response")
        print(f"  Available keys: {list(config.keys())}")
        return False


def test_max_streams_persistence():
    """Test that MAX_STREAMS_PER_ENGINE can be persisted"""
    print("\n" + "=" * 60)
    print("Test 3: MAX_STREAMS_PER_ENGINE Persistence")
    print("=" * 60)
    
    from app.services.settings_persistence import SettingsPersistence
    
    # Save a config with max_streams_per_engine
    test_config = {
        "max_streams_per_engine": 7,
        "channel_shutdown_delay": 5,
        "initial_data_wait_timeout": 10
    }
    
    success = SettingsPersistence.save_proxy_config(test_config)
    if not success:
        print("✗ Failed to save proxy config")
        return False
    
    print("✓ Proxy config saved successfully")
    
    # Load it back
    loaded_config = SettingsPersistence.load_proxy_config()
    
    if loaded_config and 'max_streams_per_engine' in loaded_config:
        if loaded_config['max_streams_per_engine'] == 7:
            print(f"✓ max_streams_per_engine persisted correctly: {loaded_config['max_streams_per_engine']}")
            return True
        else:
            print(f"✗ max_streams_per_engine has wrong value: {loaded_config['max_streams_per_engine']} (expected 7)")
            return False
    else:
        print(f"✗ max_streams_per_engine NOT found in loaded config")
        return False


def main():
    """Run all tests"""
    print("Testing Idle Stream Cleanup Fix and MAX_STREAMS_PER_ENGINE Configuration")
    print()
    
    results = []
    
    # Test 1: Last disconnect key cleared
    try:
        results.append(("Last Disconnect Key Cleared", test_last_disconnect_cleared_on_connect()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        results.append(("Last Disconnect Key Cleared", False))
    
    # Test 2: API integration
    try:
        results.append(("MAX_STREAMS_PER_ENGINE API", test_max_streams_per_engine_api()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        results.append(("MAX_STREAMS_PER_ENGINE API", False))
    
    # Test 3: Persistence
    try:
        results.append(("MAX_STREAMS_PER_ENGINE Persistence", test_max_streams_persistence()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        results.append(("MAX_STREAMS_PER_ENGINE Persistence", False))
    
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
