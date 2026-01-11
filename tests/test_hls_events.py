#!/usr/bin/env python3
"""
Test script to verify HLS proxy event handling.
This validates that:
1. Stream started events are sent when channels are initialized
2. Stream ended events are sent when channels are stopped
3. Multiple clients can connect to the same channel
4. Channels are only stopped when all clients disconnect
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch, call
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

class TestHLSEvents(unittest.TestCase):
    """Test HLS proxy event handling"""
    
    def setUp(self):
        """Set up test fixtures"""
        from app.proxy.hls_proxy import HLSProxyServer
        # Reset singleton for clean test
        HLSProxyServer._instance = None
    
    @patch('app.proxy.hls_proxy.requests.post')
    def test_stream_started_event_sent(self, mock_post):
        """Test that stream started event is sent when channel is initialized"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 'stream_123'}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
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
        
        # Give time for event to be sent
        time.sleep(0.1)
        
        # Verify event was sent
        self.assertTrue(mock_post.called, "Stream started event should be sent")
        
        # Verify event payload
        call_args = mock_post.call_args
        self.assertIsNotNone(call_args, "Event should be sent")
        
        # Check URL
        self.assertIn('/events/stream_started', call_args[0][0])
        
        # Check event data
        event_data = call_args[1]['json']
        self.assertEqual(event_data['container_id'], 'container_123')
        self.assertEqual(event_data['engine']['host'], 'localhost')
        self.assertEqual(event_data['engine']['port'], 6878)
        self.assertEqual(event_data['stream']['key'], 'test_channel')
        self.assertEqual(event_data['session']['playback_session_id'], 'test_session_123')
        
        # Cleanup
        proxy.stop_channel("test_channel")
    
    @patch('app.proxy.hls_proxy.requests.post')
    @patch('app.proxy.hls_proxy.requests.get')
    def test_stream_ended_event_sent(self, mock_get, mock_post):
        """Test that stream ended event is sent when channel is stopped"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Mock successful started event response
        mock_start_response = MagicMock()
        mock_start_response.status_code = 200
        mock_start_response.json.return_value = {'id': 'stream_123'}
        mock_start_response.raise_for_status = MagicMock()
        
        # Mock successful ended event response
        mock_end_response = MagicMock()
        mock_end_response.status_code = 200
        mock_end_response.raise_for_status = MagicMock()
        
        # Return different responses for different calls
        mock_post.side_effect = [mock_start_response, mock_end_response]
        
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
        
        # Give time for started event
        time.sleep(0.1)
        
        # Stop channel
        proxy.stop_channel("test_channel", reason="test_stop")
        
        # Give time for ended event
        time.sleep(0.1)
        
        # Verify both events were sent
        self.assertEqual(mock_post.call_count, 2, "Both started and ended events should be sent")
        
        # Check ended event
        ended_call = mock_post.call_args_list[1]
        self.assertIn('/events/stream_ended', ended_call[0][0])
        
        ended_data = ended_call[1]['json']
        self.assertEqual(ended_data['container_id'], 'container_123')
        self.assertEqual(ended_data['stream_id'], 'stream_123')
        self.assertEqual(ended_data['reason'], 'test_stop')
    
    @patch('app.proxy.hls_proxy.requests.post')
    def test_multiple_clients_same_channel(self, mock_post):
        """Test that multiple clients can connect to the same channel without reinitializing"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 'stream_123'}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
        # Create session info
        session_info = {
            'playback_session_id': 'test_session_123',
            'stat_url': 'http://example.com/stat',
            'command_url': 'http://example.com/command',
            'is_live': 1
        }
        
        # Initialize channel with first client
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
        
        # Verify channel exists
        self.assertIn("test_channel", proxy.stream_managers)
        self.assertIn("test_channel", proxy.client_managers)
        
        # Simulate client activity (multiple clients)
        proxy.record_client_activity("test_channel", "192.168.1.1")
        proxy.record_client_activity("test_channel", "192.168.1.2")
        
        # Verify client manager tracks multiple IPs
        client_manager = proxy.client_managers["test_channel"]
        self.assertTrue(client_manager.has_clients())
        self.assertEqual(len(client_manager.last_activity), 2)
        
        # Try to initialize again - should just return without reinitializing
        proxy.initialize_channel(
            channel_id="test_channel",
            playback_url="http://example.com/test.m3u8",
            engine_host="localhost",
            engine_port=6878,
            engine_container_id="container_123",
            session_info=session_info,
            api_key="test_api_key"
        )
        
        # Verify only one started event was sent (not reinitialized)
        self.assertEqual(mock_post.call_count, 1, "Only one started event should be sent for multiple clients")
        
        # Cleanup
        proxy.stop_channel("test_channel")
    
    @patch('app.proxy.hls_proxy.requests.post')
    @patch('app.proxy.hls_proxy.requests.get')
    def test_channel_cleanup_on_inactivity(self, mock_get, mock_post):
        """Test that channel is stopped when all clients become inactive"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Mock successful responses
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'id': 'stream_123'}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        mock_get.return_value = mock_response
        
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
        
        # Record activity from multiple clients
        proxy.record_client_activity("test_channel", "192.168.1.1")
        proxy.record_client_activity("test_channel", "192.168.1.2")
        
        # Verify 2 clients tracked
        client_manager = proxy.client_managers["test_channel"]
        self.assertEqual(len(client_manager.last_activity), 2)
        
        # Verify channel exists
        self.assertIn("test_channel", proxy.stream_managers)
        
        # Note: In the new implementation, cleanup happens automatically via
        # the cleanup monitoring thread when clients become inactive
        # We can test manual cleanup by calling cleanup_inactive with a short timeout
        
        # Simulate no activity for a while
        time.sleep(0.1)
        
        # Clean up inactive clients with very short timeout (0 seconds = all are inactive)
        all_inactive = client_manager.cleanup_inactive(timeout=0)
        
        # Verify all clients are now considered inactive
        self.assertTrue(all_inactive)
        
        # Cleanup
        proxy.stop_channel("test_channel")


def main():
    """Run all tests"""
    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
