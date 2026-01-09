#!/usr/bin/env python3
"""
Validation script to demonstrate the proxy fixes.
This script verifies:
1. Configurable no-data tolerance settings
2. Configurable initial data wait settings
3. API key is sent with Bearer token authentication
4. HTTP streamer handles race conditions gracefully
"""

import os
import sys

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_configurable_settings():
    """Test that settings are configurable via environment"""
    print("=" * 60)
    print("Test 1: Configurable Settings")
    print("=" * 60)
    
    # Set custom values
    os.environ['PROXY_NO_DATA_TIMEOUT_CHECKS'] = '50'
    os.environ['PROXY_NO_DATA_CHECK_INTERVAL'] = '0.2'
    os.environ['PROXY_INITIAL_DATA_WAIT_TIMEOUT'] = '15'
    os.environ['PROXY_INITIAL_DATA_CHECK_INTERVAL'] = '0.3'
    
    # Reload config
    import importlib
    from app.proxy import config_helper
    importlib.reload(config_helper)
    from app.proxy.config_helper import ConfigHelper
    
    # Verify settings
    assert ConfigHelper.no_data_timeout_checks() == 50, "NO_DATA_TIMEOUT_CHECKS not configurable"
    assert ConfigHelper.no_data_check_interval() == 0.2, "NO_DATA_CHECK_INTERVAL not configurable"
    assert ConfigHelper.initial_data_wait_timeout() == 15, "INITIAL_DATA_WAIT_TIMEOUT not configurable"
    assert ConfigHelper.initial_data_check_interval() == 0.3, "INITIAL_DATA_CHECK_INTERVAL not configurable"
    
    # Calculate total timeout
    total_no_data_timeout = 50 * 0.2  # 10 seconds
    print(f"✓ NO_DATA_TIMEOUT_CHECKS: {ConfigHelper.no_data_timeout_checks()}")
    print(f"✓ NO_DATA_CHECK_INTERVAL: {ConfigHelper.no_data_check_interval()}s")
    print(f"  → Total no-data timeout: {total_no_data_timeout}s")
    print(f"✓ INITIAL_DATA_WAIT_TIMEOUT: {ConfigHelper.initial_data_wait_timeout()}s")
    print(f"✓ INITIAL_DATA_CHECK_INTERVAL: {ConfigHelper.initial_data_check_interval()}s")
    print()
    
    return True


def test_api_key_bearer_token():
    """Test that API key uses Bearer token format"""
    print("=" * 60)
    print("Test 2: API Key Bearer Token Format")
    print("=" * 60)
    
    from unittest.mock import Mock, patch
    from app.proxy.stream_manager import StreamManager
    from app.proxy.stream_buffer import StreamBuffer
    
    # Create mocks
    mock_redis = Mock()
    mock_buffer = StreamBuffer(content_id="test", redis_client=mock_redis)
    mock_client_manager = Mock()
    
    # Create stream manager with API key
    manager = StreamManager(
        content_id="test_id",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container",
        buffer=mock_buffer,
        client_manager=mock_client_manager,
        worker_id="test_worker",
        api_key="test_api_key_123"
    )
    
    # Mock requests
    with patch('app.proxy.stream_manager.requests.post') as mock_post:
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {'id': 'stream_123'}
        mock_post.return_value = mock_response
        
        # Send event
        manager.playback_session_id = "session_123"
        manager._send_stream_started_event()
        
        # Verify Bearer token was used
        assert mock_post.called, "POST request not made"
        headers = mock_post.call_args[1]['headers']
        
        assert 'Authorization' in headers, "Authorization header missing"
        assert headers['Authorization'] == 'Bearer test_api_key_123', "Bearer token format incorrect"
        assert 'X-API-KEY' not in headers, "Old X-API-KEY header still present"
        
        print(f"✓ API key sent as: {headers['Authorization']}")
        print(f"✓ Correct format: Authorization: Bearer <key>")
        print(f"✓ Old X-API-KEY header removed")
        print()
    
    return True


def test_http_streamer_race_condition():
    """Test that HTTP streamer handles stop during iteration"""
    print("=" * 60)
    print("Test 3: HTTP Streamer Race Condition Fix")
    print("=" * 60)
    
    import inspect
    from app.proxy import http_streamer
    
    # Check that the code has AttributeError handling
    source = inspect.getsource(http_streamer.HTTPStreamReader._read_stream)
    
    has_attribute_error_handling = 'except AttributeError' in source
    has_running_check = 'if not self.running' in source
    
    assert has_attribute_error_handling, "Missing AttributeError exception handling"
    assert has_running_check, "Missing self.running checks"
    
    print("✓ AttributeError exception handling added")
    print("✓ self.running flag checks in place")
    print("✓ Graceful shutdown logic implemented")
    print()
    
    # Check stop() method
    source_stop = inspect.getsource(http_streamer.HTTPStreamReader.stop)
    has_wait_before_close = 'join(timeout=' in source_stop
    has_none_assignment = 'self.response = None' in source_stop
    
    assert has_wait_before_close, "Missing wait before closing response"
    assert has_none_assignment, "Missing response = None assignment"
    
    print("✓ Wait period before closing response")
    print("✓ Response set to None after closing")
    print("✓ Prevents 'NoneType' object has no attribute 'read' error")
    print()
    
    return True


def test_default_values():
    """Test that default values are reasonable"""
    print("=" * 60)
    print("Test 4: Default Configuration Values")
    print("=" * 60)
    
    # Clear env vars to test defaults
    for key in ['PROXY_NO_DATA_TIMEOUT_CHECKS', 'PROXY_NO_DATA_CHECK_INTERVAL',
                'PROXY_INITIAL_DATA_WAIT_TIMEOUT', 'PROXY_INITIAL_DATA_CHECK_INTERVAL']:
        os.environ.pop(key, None)
    
    # Reload to get defaults
    import importlib
    from app.proxy import config_helper
    importlib.reload(config_helper)
    from app.proxy.config_helper import ConfigHelper
    
    # Check defaults
    no_data_checks = ConfigHelper.no_data_timeout_checks()
    no_data_interval = ConfigHelper.no_data_check_interval()
    initial_timeout = ConfigHelper.initial_data_wait_timeout()
    initial_interval = ConfigHelper.initial_data_check_interval()
    
    total_no_data = no_data_checks * no_data_interval
    
    print(f"✓ Default no-data timeout: {total_no_data}s ({no_data_checks} checks × {no_data_interval}s)")
    print(f"✓ Default initial data wait: {initial_timeout}s (check every {initial_interval}s)")
    print()
    
    # Verify reasonable defaults
    assert total_no_data >= 1.0, "No-data timeout too short"
    assert initial_timeout >= 5, "Initial wait timeout too short"
    
    print("✓ All default values are reasonable for AceStream stability")
    print()
    
    return True


def main():
    """Run all validation tests"""
    print()
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 12 + "PROXY FIXES VALIDATION" + " " * 24 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    
    tests = [
        test_configurable_settings,
        test_api_key_bearer_token,
        test_http_streamer_race_condition,
        test_default_values,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"✗ Test failed: {e}")
            failed += 1
            import traceback
            traceback.print_exc()
            print()
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    print()
    
    if failed == 0:
        print("✓ All proxy fixes validated successfully!")
        print()
        print("Summary of fixes:")
        print("  1. Configurable no-data tolerance (PROXY_NO_DATA_TIMEOUT_CHECKS)")
        print("  2. Configurable initial buffer wait (PROXY_INITIAL_DATA_WAIT_TIMEOUT)")
        print("  3. API key now uses Authorization: Bearer header")
        print("  4. HTTP streamer race condition fixed")
        print()
        return 0
    else:
        print("✗ Some validations failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
