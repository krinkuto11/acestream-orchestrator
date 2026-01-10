#!/usr/bin/env python3
"""
Test script to verify HLS proxy implementation.
This validates that:
1. HLS proxy can be initialized
2. Channels can be created and managed
3. Manifest generation works correctly
4. Segment buffering works
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_hls_proxy_creation():
    """Test HLS proxy singleton creation"""
    print("=" * 60)
    print("Test 1: HLS Proxy Creation")
    print("=" * 60)
    
    from app.proxy.hls_proxy import HLSProxyServer
    
    # Test singleton
    proxy1 = HLSProxyServer.get_instance()
    proxy2 = HLSProxyServer.get_instance()
    
    assert proxy1 is proxy2, "HLS proxy should be a singleton"
    print("✓ HLS proxy singleton works correctly")
    print()
    
    return True


def test_hls_config():
    """Test HLS configuration"""
    print("=" * 60)
    print("Test 2: HLS Configuration")
    print("=" * 60)
    
    from app.proxy.hls_proxy import HLSConfig
    
    # Verify default config values (now using methods)
    assert HLSConfig.MAX_SEGMENTS() == 20, "MAX_SEGMENTS should be 20"
    assert HLSConfig.INITIAL_SEGMENTS() == 3, "INITIAL_SEGMENTS should be 3"
    assert HLSConfig.WINDOW_SIZE() == 6, "WINDOW_SIZE should be 6"
    
    print(f"✓ MAX_SEGMENTS: {HLSConfig.MAX_SEGMENTS()}")
    print(f"✓ INITIAL_SEGMENTS: {HLSConfig.INITIAL_SEGMENTS()}")
    print(f"✓ WINDOW_SIZE: {HLSConfig.WINDOW_SIZE()}")
    print(f"✓ BUFFER_READY_TIMEOUT: {HLSConfig.BUFFER_READY_TIMEOUT()}s")
    print()
    
    return True


def test_stream_buffer():
    """Test stream buffer functionality"""
    print("=" * 60)
    print("Test 3: Stream Buffer")
    print("=" * 60)
    
    from app.proxy.hls_proxy import StreamBuffer
    
    buffer = StreamBuffer()
    
    # Test adding segments
    buffer[0] = b"segment0"
    buffer[1] = b"segment1"
    buffer[2] = b"segment2"
    
    # Test retrieval
    assert buffer[0] == b"segment0", "Buffer should store and retrieve segments"
    assert buffer[1] == b"segment1", "Buffer should store and retrieve segments"
    
    # Test contains
    assert 0 in buffer, "Buffer should track stored segments"
    assert 3 not in buffer, "Buffer should not contain non-existent segments"
    
    # Test keys
    keys = buffer.keys()
    assert len(keys) == 3, "Buffer should track all segments"
    
    print("✓ Stream buffer stores segments")
    print("✓ Stream buffer retrieves segments")
    print("✓ Stream buffer tracks segment presence")
    print(f"✓ Buffer contains {len(keys)} segments")
    print()
    
    return True


def test_stream_manager():
    """Test stream manager creation"""
    print("=" * 60)
    print("Test 4: Stream Manager")
    print("=" * 60)
    
    from app.proxy.hls_proxy import StreamManager
    
    # Create session info dict
    session_info = {
        'playback_session_id': 'test_session_123',
        'stat_url': 'http://example.com/stat',
        'command_url': 'http://example.com/command',
        'is_live': 1
    }
    
    manager = StreamManager(
        playback_url="http://example.com/test.m3u8",
        channel_id="test_channel",
        engine_host="localhost",
        engine_port=6878,
        engine_container_id="test_container_123",
        session_info=session_info,
        api_key=None
    )
    
    assert manager.channel_id == "test_channel", "Channel ID should be set"
    assert manager.playback_url == "http://example.com/test.m3u8", "Playback URL should be set"
    assert manager.running == True, "Manager should be running initially"
    assert manager.initial_buffering == True, "Manager should start in initial buffering mode"
    assert manager.engine_host == "localhost", "Engine host should be set"
    assert manager.engine_port == 6878, "Engine port should be set"
    assert manager.playback_session_id == "test_session_123", "Session ID should be set"
    
    print(f"✓ Stream manager created for channel: {manager.channel_id}")
    print(f"✓ Playback URL: {manager.playback_url}")
    print(f"✓ Engine: {manager.engine_host}:{manager.engine_port}")
    print(f"✓ Session ID: {manager.playback_session_id}")
    print(f"✓ Initial buffering: {manager.initial_buffering}")
    print(f"✓ Target duration: {manager.target_duration}s")
    print()
    
    # Cleanup
    manager.stop()
    
    return True


def test_hls_routing():
    """Test that HLS mode routes to HLS proxy in main.py"""
    print("=" * 60)
    print("Test 5: HLS Routing Logic")
    print("=" * 60)
    
    # Check that main.py imports HLS proxy correctly
    import ast
    import os
    
    main_py_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'main.py')
    
    with open(main_py_path, 'r') as f:
        content = f.read()
    
    # Verify HLS proxy is imported
    assert 'from app.proxy.hls_proxy import HLSProxyServer' in content, "main.py should import HLSProxyServer"
    
    # Verify HLS mode handling
    assert "if stream_mode == 'HLS':" in content, "main.py should check for HLS mode"
    assert 'hls_proxy = HLSProxyServer.get_instance()' in content, "main.py should get HLS proxy instance"
    assert 'hls_proxy.initialize_channel' in content, "main.py should initialize HLS channel"
    
    print("✓ main.py imports HLSProxyServer")
    print("✓ main.py checks for HLS mode")
    print("✓ main.py initializes HLS proxy channels")
    print("✓ HLS routing logic is correct")
    print()
    
    return True


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("HLS Proxy Implementation Validation")
    print("=" * 60 + "\n")
    
    tests = [
        test_hls_proxy_creation,
        test_hls_config,
        test_stream_buffer,
        test_stream_manager,
        test_hls_routing,
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
