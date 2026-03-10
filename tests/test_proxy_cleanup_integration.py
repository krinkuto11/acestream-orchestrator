#!/usr/bin/env python3
"""
Test that verifies proxy cleanup is called when streams end.
"""

import sys
import os
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.state import State
from app.models.schemas import StreamStartedEvent, StreamEndedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_proxy_cleanup_called_on_stream_end():
    """Test that proxy cleanup methods are called when a stream ends."""
    print("Testing that proxy cleanup is called on stream end...")
    
    test_state = State()
    
    # Create mock ProxyServer and HLSProxyServer
    mock_ts_proxy = MagicMock()
    mock_hls_proxy = MagicMock()
    
    # Patch the proxy imports at the right location
    with patch('app.proxy.server.ProxyServer.get_instance') as mock_ts_get, \
         patch('app.proxy.hls_proxy.HLSProxyServer.get_instance') as mock_hls_get:
        
        # Set up mock instances
        mock_ts_get.return_value = mock_ts_proxy
        mock_hls_get.return_value = mock_hls_proxy
        
        # Start a stream
        evt = StreamStartedEvent(
            container_id="test_container_cleanup",
            engine=EngineAddress(host="127.0.0.1", port=8080),
            stream=StreamKey(key_type="content_id", key="test_stream_key_cleanup"),
            session=SessionInfo(
                playback_session_id="test_session_cleanup",
                stat_url="http://127.0.0.1:8080/ace/stat/test_session_cleanup",
                command_url="http://127.0.0.1:8080/ace/cmd/test_session_cleanup",
                is_live=1
            ),
            labels={"stream_id": "test_stream_cleanup"}
        )
        
        stream_state = test_state.on_stream_started(evt)
        stream_id = stream_state.id
        stream_key = stream_state.key
        
        # End the stream
        test_state.on_stream_ended(StreamEndedEvent(
            container_id="test_container_cleanup",
            stream_id=stream_id,
            reason="test"
        ))
        
        # Verify that both proxy cleanup methods were called with the correct stream key
        mock_ts_proxy.stop_stream_by_key.assert_called_once_with(stream_key)
        mock_hls_proxy.stop_stream_by_key.assert_called_once_with(stream_key)
        
        print(f"✅ Both TS and HLS proxy cleanup methods called with stream_key={stream_key}")


def test_proxy_cleanup_resilience():
    """Test that stream ending succeeds even if proxy cleanup fails."""
    print("Testing proxy cleanup resilience...")
    
    test_state = State()
    
    # Create mock ProxyServer that raises exception
    mock_ts_proxy = MagicMock()
    mock_ts_proxy.stop_stream_by_key.side_effect = Exception("Redis connection failed")
    
    mock_hls_proxy = MagicMock()
    mock_hls_proxy.stop_stream_by_key.side_effect = Exception("HLS cleanup failed")
    
    # Patch the proxy imports
    with patch('app.proxy.server.ProxyServer.get_instance') as mock_ts_get, \
         patch('app.proxy.hls_proxy.HLSProxyServer.get_instance') as mock_hls_get:
        
        mock_ts_get.return_value = mock_ts_proxy
        mock_hls_get.return_value = mock_hls_proxy
        
        # Start a stream
        evt = StreamStartedEvent(
            container_id="test_container_resilience",
            engine=EngineAddress(host="127.0.0.1", port=8080),
            stream=StreamKey(key_type="content_id", key="test_stream_key_resilience"),
            session=SessionInfo(
                playback_session_id="test_session_resilience",
                stat_url="http://127.0.0.1:8080/ace/stat/test_session_resilience",
                command_url="http://127.0.0.1:8080/ace/cmd/test_session_resilience",
                is_live=1
            ),
            labels={"stream_id": "test_stream_resilience"}
        )
        
        stream_state = test_state.on_stream_started(evt)
        stream_id = stream_state.id
        
        # End the stream - should NOT raise exception even though proxy cleanup fails
        try:
            result = test_state.on_stream_ended(StreamEndedEvent(
                container_id="test_container_resilience",
                stream_id=stream_id,
                reason="test"
            ))
            
            # Verify stream was still removed from state despite proxy failures
            streams = test_state.list_streams_with_stats(status="started")
            assert len(streams) == 0, "Stream should be removed from state even if proxy cleanup fails"
            assert result is not None, "on_stream_ended should return the stream state"
            
            print("✅ Stream ending succeeded despite proxy cleanup failures")
            
        except Exception as e:
            raise AssertionError(f"Stream ending should not fail due to proxy cleanup errors: {e}")


def test_proxy_cleanup_only_called_for_valid_streams():
    """Test that proxy cleanup is only attempted for streams with valid keys."""
    print("Testing proxy cleanup only called for valid streams...")
    
    test_state = State()
    
    mock_ts_proxy = MagicMock()
    mock_hls_proxy = MagicMock()
    
    with patch('app.proxy.server.ProxyServer.get_instance') as mock_ts_get, \
         patch('app.proxy.hls_proxy.HLSProxyServer.get_instance') as mock_hls_get:
        
        mock_ts_get.return_value = mock_ts_proxy
        mock_hls_get.return_value = mock_hls_proxy
        
        # Start a stream
        evt = StreamStartedEvent(
            container_id="test_container_valid",
            engine=EngineAddress(host="127.0.0.1", port=8080),
            stream=StreamKey(key_type="content_id", key="test_stream_key_valid"),
            session=SessionInfo(
                playback_session_id="test_session_valid",
                stat_url="http://127.0.0.1:8080/ace/stat/test_session_valid",
                command_url="http://127.0.0.1:8080/ace/cmd/test_session_valid",
                is_live=1
            ),
            labels={"stream_id": "test_stream_valid"}
        )
        
        stream_state = test_state.on_stream_started(evt)
        stream_id = stream_state.id
        
        # End the stream
        test_state.on_stream_ended(StreamEndedEvent(
            container_id="test_container_valid",
            stream_id=stream_id,
            reason="test"
        ))
        
        # Verify cleanup was called
        assert mock_ts_proxy.stop_stream_by_key.call_count == 1
        assert mock_hls_proxy.stop_stream_by_key.call_count == 1
        
        print("✅ Proxy cleanup called correctly for valid stream")


if __name__ == "__main__":
    print("🧪 Running proxy cleanup integration tests...\n")
    
    test_proxy_cleanup_called_on_stream_end()
    test_proxy_cleanup_resilience()
    test_proxy_cleanup_only_called_for_valid_streams()
    
    print("\n🎉 All proxy cleanup integration tests passed!")
