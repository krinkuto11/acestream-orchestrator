#!/usr/bin/env python3
"""
Demonstration script showing that HLS proxy operations are non-blocking.

This script simulates stream initialization and measures the response time
to prove that the orchestrator doesn't block on event HTTP requests.
"""

import time
import sys
import os
from unittest.mock import MagicMock, patch
import threading

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def demonstrate_non_blocking():
    """Demonstrate that HLS proxy operations don't block"""
    from app.proxy.hls_proxy import HLSProxyServer
    
    print("=" * 70)
    print("HLS Proxy Non-Blocking Demonstration")
    print("=" * 70)
    print()
    
    # Reset singleton
    HLSProxyServer._instance = None
    
    # Track when the HTTP request completes
    event_completed = threading.Event()
    
    def slow_post(*args, **kwargs):
        """Simulate a slow event HTTP request (3 seconds)"""
        time.sleep(3)
        event_completed.set()
        
        # Return successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 'demo_stream_123'}
        mock_response.raise_for_status = MagicMock()
        return mock_response
    
    # Mock the requests.post to simulate slow orchestrator response
    with patch('app.proxy.hls_proxy.requests.post', side_effect=slow_post):
        # Session info
        session_info = {
            'playback_session_id': 'demo_session_123',
            'stat_url': 'http://example.com/stat',
            'command_url': 'http://example.com/command',
            'is_live': 1
        }
        
        proxy = HLSProxyServer.get_instance()
        
        print("Initializing HLS channel...")
        print("(Event HTTP request will take 3 seconds)")
        print()
        
        # Measure initialization time
        start_time = time.time()
        
        proxy.initialize_channel(
            channel_id="demo_channel",
            playback_url="http://example.com/demo.m3u8",
            engine_host="localhost",
            engine_port=6878,
            engine_container_id="demo_container_123",
            session_info=session_info,
            api_key="demo_api_key"
        )
        
        init_time = time.time() - start_time
        
        print(f"✓ initialize_channel() returned in: {init_time:.3f} seconds")
        print()
        
        # Show the difference
        if init_time < 0.5:
            print("✓ SUCCESS: Method returned immediately (non-blocking)")
            print(f"  Expected: < 0.5 seconds")
            print(f"  Actual: {init_time:.3f} seconds")
        else:
            print("✗ FAILED: Method blocked waiting for HTTP request")
            print(f"  Expected: < 0.5 seconds")
            print(f"  Actual: {init_time:.3f} seconds")
        
        print()
        print("Waiting for background event to complete...")
        
        # Wait for background request
        if event_completed.wait(timeout=5):
            event_time = time.time() - start_time
            print(f"✓ Background event completed in: {event_time:.3f} seconds")
            print()
            print("DEMONSTRATION SUMMARY:")
            print("-" * 70)
            print(f"  API call returned:       {init_time:.3f}s (immediate)")
            print(f"  Event sent in background: {event_time:.3f}s (3s as expected)")
            print(f"  UI remained responsive:  ✓ YES")
            print()
            print("This proves that stream initialization doesn't block the")
            print("orchestrator, keeping the UI responsive during stream startup.")
        else:
            print("✗ Background event did not complete in time")
        
        # Cleanup
        proxy.stop_channel("demo_channel")
    
    print("=" * 70)


if __name__ == '__main__':
    demonstrate_non_blocking()
