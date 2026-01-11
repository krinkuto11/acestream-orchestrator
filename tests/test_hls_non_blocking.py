#!/usr/bin/env python3
"""
Test to verify that HLS proxy event handling is non-blocking.
This validates that initialize_channel() returns immediately without waiting for event HTTP requests.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestHLSNonBlocking(unittest.TestCase):
    """Test HLS proxy non-blocking behavior"""
    
    def setUp(self):
        """Set up test fixtures"""
        from app.proxy.hls_proxy import HLSProxyServer
        # Reset singleton for clean test
        HLSProxyServer._instance = None
    
    @patch('app.proxy.hls_proxy.requests.post')
    def test_initialize_channel_returns_immediately(self, mock_post):
        """Test that initialize_channel returns immediately even if event request is slow"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Track when the HTTP request was made
        request_made = threading.Event()
        request_completed = threading.Event()
        
        def slow_post(*args, **kwargs):
            """Simulate a slow HTTP request"""
            request_made.set()
            time.sleep(2)  # Simulate 2 second delay
            request_completed.set()
            
            # Return successful response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {'id': 'stream_123'}
            mock_response.raise_for_status = MagicMock()
            return mock_response
        
        mock_post.side_effect = slow_post
        
        # Create session info
        session_info = {
            'playback_session_id': 'test_session_123',
            'stat_url': 'http://example.com/stat',
            'command_url': 'http://example.com/command',
            'is_live': 1
        }
        
        # Measure time to initialize channel
        start_time = time.time()
        
        # Initialize channel
        proxy = HLSProxyServer.get_instance()
        proxy.initialize_channel(
            channel_id="test_channel",
            playback_url="http://example.com/test.m3u8",
            engine_host="localhost",
            engine_port=6878,
            engine_container_id="container_123",
            session_info=session_info,
            api_key="test_api_key"
        )
        
        init_time = time.time() - start_time
        
        # Initialize should return quickly (< 0.5 seconds) even though HTTP request takes 2 seconds
        self.assertLess(init_time, 0.5, 
                       f"initialize_channel took {init_time:.2f}s, should return immediately")
        
        # The HTTP request should not have completed yet
        self.assertFalse(request_completed.is_set(),
                        "HTTP request should still be running in background")
        
        # Wait for background thread to make the request
        self.assertTrue(request_made.wait(timeout=1.0),
                       "Background thread should have started the HTTP request")
        
        # Wait for the request to complete
        self.assertTrue(request_completed.wait(timeout=3.0),
                       "Background HTTP request should complete eventually")
        
        # Cleanup
        proxy.stop_channel("test_channel")
    
    @patch('app.proxy.hls_proxy.requests.post')
    @patch('app.proxy.hls_proxy.requests.get')
    def test_stop_channel_returns_immediately(self, mock_get, mock_post):
        """Test that stop_channel returns immediately even if event request is slow"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Track when requests are made
        start_request_made = threading.Event()
        end_request_made = threading.Event()
        end_request_completed = threading.Event()
        
        def post_side_effect(*args, **kwargs):
            """Track when events are sent"""
            url = args[0] if args else kwargs.get('url', '')
            
            if 'stream_started' in url:
                start_request_made.set()
                # Start event completes quickly
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = {'id': 'stream_123'}
                mock_response.raise_for_status = MagicMock()
                return mock_response
            elif 'stream_ended' in url:
                end_request_made.set()
                # End event is slow (2 seconds)
                time.sleep(2)
                end_request_completed.set()
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.raise_for_status = MagicMock()
                return mock_response
        
        mock_post.side_effect = post_side_effect
        
        # Mock stop command
        mock_stop_response = MagicMock()
        mock_stop_response.status_code = 200
        mock_get.return_value = mock_stop_response
        
        # Create session info
        session_info = {
            'playback_session_id': 'test_session_123',
            'stat_url': 'http://example.com/stat',
            'command_url': 'http://example.com/command',
            'is_live': 1
        }
        
        # Initialize channel
        proxy = HLSProxyServer.get_instance()
        proxy.initialize_channel(
            channel_id="test_channel",
            playback_url="http://example.com/test.m3u8",
            engine_host="localhost",
            engine_port=6878,
            engine_container_id="container_123",
            session_info=session_info,
            api_key="test_api_key"
        )
        
        # Wait for start event to be sent
        self.assertTrue(start_request_made.wait(timeout=1.0),
                       "Start event should be sent in background")
        
        # Give time for stream_id to be set
        time.sleep(0.2)
        
        # Measure time to stop channel
        start_time = time.time()
        proxy.stop_channel("test_channel", reason="test_stop")
        stop_time = time.time() - start_time
        
        # Stop should return quickly (< 0.5 seconds) even though end event takes 2 seconds
        self.assertLess(stop_time, 0.5,
                       f"stop_channel took {stop_time:.2f}s, should return immediately")
        
        # The end event should not have completed yet
        self.assertFalse(end_request_completed.is_set(),
                        "End event HTTP request should still be running in background")
        
        # Wait for background thread to make the request
        self.assertTrue(end_request_made.wait(timeout=1.0),
                       "Background thread should have started the end event HTTP request")
        
        # Wait for the request to complete
        self.assertTrue(end_request_completed.wait(timeout=3.0),
                       "Background end event HTTP request should complete eventually")
    
    @patch('app.proxy.hls_proxy.requests.post')
    def test_multiple_sequential_initializations_dont_block(self, mock_post):
        """Test that multiple sequential channel initializations don't block on each other"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Track requests
        requests_made = []
        
        def slow_post(*args, **kwargs):
            """Simulate slow HTTP requests"""
            request_id = len(requests_made)
            requests_made.append(time.time())
            time.sleep(1)  # Each request takes 1 second
            
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {'id': f'stream_{request_id}'}
            mock_response.raise_for_status = MagicMock()
            return mock_response
        
        mock_post.side_effect = slow_post
        
        # Create session info template
        def get_session_info(channel_num):
            return {
                'playback_session_id': f'test_session_{channel_num}',
                'stat_url': f'http://example.com/stat_{channel_num}',
                'command_url': f'http://example.com/command_{channel_num}',
                'is_live': 1
            }
        
        # Initialize 3 channels sequentially
        proxy = HLSProxyServer.get_instance()
        start_time = time.time()
        
        for i in range(3):
            proxy.initialize_channel(
                channel_id=f"test_channel_{i}",
                playback_url=f"http://example.com/test_{i}.m3u8",
                engine_host="localhost",
                engine_port=6878 + i,
                engine_container_id=f"container_{i}",
                session_info=get_session_info(i),
                api_key="test_api_key"
            )
        
        total_init_time = time.time() - start_time
        
        # All 3 initializations should complete quickly (< 1 second total)
        # even though each HTTP request takes 1 second
        self.assertLess(total_init_time, 1.0,
                       f"3 initializations took {total_init_time:.2f}s, should not wait for HTTP requests")
        
        # Wait for all background requests to complete
        time.sleep(4)
        
        # Verify at least 3 events were sent (could be more due to stop events)
        self.assertGreaterEqual(len(requests_made), 3,
                        "At least 3 events should be sent in background")
        
        # Cleanup
        for i in range(3):
            proxy.stop_channel(f"test_channel_{i}")


def main():
    """Run all tests"""
    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
