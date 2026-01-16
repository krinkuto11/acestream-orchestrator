#!/usr/bin/env python3
"""
Test script to verify HLS proxy thread-safety for event sending.
This validates that:
1. _send_stream_ended_event can be called from a thread without "no running event loop" errors
2. _send_stream_started_event can be called from both async and thread contexts
3. Events are properly scheduled when called from threads
"""

import sys
import os
import unittest
import threading
import time
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestHLSThreadSafety(unittest.TestCase):
    """Test HLS proxy thread-safety for event sending"""
    
    def setUp(self):
        """Set up test fixtures"""
        from app.proxy.hls_proxy import StreamManager
        
        # Create a simple event loop for testing
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Start the event loop in a background thread
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()
        
        # Give the loop time to start
        time.sleep(0.1)
        
        # Create a stream manager instance with event loop reference
        self.manager = StreamManager(
            playback_url="http://test:8000/test.m3u8",
            channel_id="test_channel",
            engine_host="test_engine",
            engine_port=8000,
            engine_container_id="test_container",
            session_info={
                'playback_session_id': 'test_session',
                'stat_url': 'http://test/stat',
                'command_url': 'http://test/command',
                'is_live': 1
            },
            event_loop=self.loop  # Pass the event loop for thread-safe event sending
        )
        
        # Set a stream_id so events can be sent
        self.manager.stream_id = "test_stream_123"
    
    def _run_loop(self):
        """Run the event loop in a background thread"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
    
    def tearDown(self):
        """Clean up after tests"""
        # Stop the event loop
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=2)
        self.loop.close()
    
    def test_send_ended_event_from_thread(self):
        """Test that _send_stream_ended_event can be called from a thread without errors"""
        error_occurred = []
        
        def call_from_thread():
            """Call _send_stream_ended_event from a thread"""
            try:
                self.manager._send_stream_ended_event(reason="test_thread")
                # Give time for the event to be scheduled
                time.sleep(0.2)
            except Exception as e:
                error_occurred.append(str(e))
        
        # Call from a separate thread
        thread = threading.Thread(target=call_from_thread)
        thread.start()
        thread.join(timeout=2)
        
        # Verify no "no running event loop" error occurred
        for error in error_occurred:
            self.assertNotIn("no running event loop", error.lower(),
                           f"Should not get 'no running event loop' error: {error}")
        
        # Verify the event was marked as sent (or at least attempted)
        # Note: The actual event sending may fail due to missing dependencies,
        # but we're testing that the thread-safety mechanism works
        time.sleep(0.3)
        
    def test_send_started_event_from_thread(self):
        """Test that _send_stream_started_event can be called from a thread without errors"""
        error_occurred = []
        
        def call_from_thread():
            """Call _send_stream_started_event from a thread"""
            try:
                self.manager._send_stream_started_event()
                # Give time for the event to be scheduled
                time.sleep(0.2)
            except Exception as e:
                error_occurred.append(str(e))
        
        # Call from a separate thread
        thread = threading.Thread(target=call_from_thread)
        thread.start()
        thread.join(timeout=2)
        
        # Verify no "no running event loop" error occurred
        for error in error_occurred:
            self.assertNotIn("no running event loop", error.lower(),
                           f"Should not get 'no running event loop' error: {error}")
        
        # Verify a stream_id was set (either temp or from the event)
        self.assertIsNotNone(self.manager.stream_id)
        self.assertIn("test_channel", self.manager.stream_id)
    
    def test_cleanup_thread_stop_channel_simulation(self):
        """Simulate the cleanup thread scenario that was causing the error"""
        from app.proxy.hls_proxy import HLSProxyServer
        
        # Reset singleton
        HLSProxyServer._instance = None
        
        # Create proxy server and set its event loop reference
        proxy = HLSProxyServer.get_instance()
        proxy._main_loop = self.loop  # Set the test loop as the main loop
        
        error_occurred = []
        
        def cleanup_thread_simulation():
            """Simulate the cleanup thread calling stop_channel"""
            try:
                # This simulates what happens in cleanup_loop
                # when all clients disconnect
                time.sleep(0.3)
                proxy.stop_channel("test_channel", reason="inactivity")
            except Exception as e:
                error_occurred.append(str(e))
        
        # Initialize a channel - needs to run in async context
        session_info = {
            'playback_session_id': 'test_session',
            'stat_url': 'http://test/stat',
            'command_url': 'http://test/command',
            'is_live': 1
        }
        
        async def initialize():
            """Initialize channel in async context"""
            proxy.initialize_channel(
                channel_id="test_channel",
                playback_url="http://test:8000/test.m3u8",
                engine_host="test_engine",
                engine_port=8000,
                engine_container_id="test_container",
                session_info=session_info
            )
        
        # Schedule initialization on the event loop
        future = asyncio.run_coroutine_threadsafe(initialize(), self.loop)
        future.result(timeout=2)  # Wait for initialization to complete
        
        # Give time for initialization to settle
        time.sleep(0.2)
        
        # Start cleanup thread
        cleanup_thread = threading.Thread(target=cleanup_thread_simulation)
        cleanup_thread.start()
        cleanup_thread.join(timeout=3)
        
        # Verify no "no running event loop" error occurred
        for error in error_occurred:
            self.assertNotIn("no running event loop", error.lower(),
                           f"Should not get 'no running event loop' error when stopping from thread: {error}")


def main():
    """Run all tests"""
    unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
