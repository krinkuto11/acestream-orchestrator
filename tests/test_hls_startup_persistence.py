#!/usr/bin/env python3
"""
Test to verify that HLS mode startup validation and persistence works correctly.

This test validates that:
1. Stream start event handling has timeout protection code.
2. The startup code in main.py persists the mode change.
"""

import sys
import os
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_startup_persistence_code():
    """Test that startup code persists the mode change when HLS is not supported"""
    main_py_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'main.py')
    
    with open(main_py_path, 'r') as f:
        content = f.read()
    
    # Find the section where stream_mode is validated on startup
    pattern = r"if 'stream_mode' in proxy_settings:.*?if mode == 'HLS' and not cfg\.ENGINE_VARIANT\.startswith\('krinkuto11-amd64'\):.*?SettingsPersistence\.save_proxy_config\(proxy_settings\)"
    
    if re.search(pattern, content, re.DOTALL):
        print("✓ Startup code persists mode change when HLS is not supported")
    else:
        print("✗ Startup code does NOT persist mode change")
        return False
    
    # Check that the warning message mentions persistence
    if 'persisting change' in content:
        print("✓ Warning message mentions persistence")
    else:
        print("✗ Warning message does not mention persistence")
        return False
    
    return True


def test_stream_start_event_timeout_protection():
    """Test that stream start event handling has timeout protection"""
    
    # Check HLS proxy
    hls_proxy_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'proxy', 'hls_proxy.py')
    with open(hls_proxy_path, 'r') as f:
        hls_content = f.read()
    
    # Verify timeout is implemented in HLS proxy
    if 'join(timeout=' not in hls_content:
        print("✗ HLS proxy missing timeout protection")
        return False
    
    if 'is_alive()' not in hls_content:
        print("✗ HLS proxy missing thread.is_alive() check")
        return False
    
    if 'temp-hls-' not in hls_content:
        print("✗ HLS proxy missing temp stream_id generation")
        return False
    
    print("✓ HLS proxy has timeout protection")
    
    # Check TS proxy
    stream_manager_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'proxy', 'stream_manager.py')
    with open(stream_manager_path, 'r') as f:
        ts_content = f.read()
    
    # Verify timeout is implemented in TS proxy
    if 'join(timeout=' not in ts_content:
        print("✗ TS proxy missing timeout protection")
        return False
    
    if 'is_alive()' not in ts_content:
        print("✗ TS proxy missing thread.is_alive() check")
        return False
    
    if 'temp-ts-' not in ts_content:
        print("✗ TS proxy missing temp stream_id generation")
        return False
    
    print("✓ TS proxy has timeout protection")
    
    return True


def main():
    """Run all tests"""
    print("=" * 70)
    print("Testing HLS Startup Validation and Persistence")
    print("=" * 70)
    print()
    
    test1_passed = test_startup_persistence_code()
    print()
    test2_passed = test_stream_start_event_timeout_protection()
    print()
    
    if test1_passed and test2_passed:
        print("=" * 70)
        print("✅ All tests passed!")
        print("=" * 70)
        return 0
    else:
        print("=" * 70)
        print("✗ Some tests failed")
        print("=" * 70)
        return 1


if __name__ == '__main__':
    sys.exit(main())

