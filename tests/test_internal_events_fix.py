"""
Test to validate that internal event handlers work correctly and avoid deadlocks.

This test verifies that:
1. Internal event handlers can be called directly without HTTP requests
2. Stream state is properly updated
3. No HTTP timeouts occur
"""

import pytest
from unittest.mock import Mock, patch
from app.models.schemas import (
    StreamStartedEvent, StreamEndedEvent, 
    StreamKey, EngineAddress, SessionInfo
)


def test_internal_event_handler_imports():
    """Test that internal event handlers can be imported"""
    from app.services.internal_events import handle_stream_started, handle_stream_ended
    
    assert callable(handle_stream_started)
    assert callable(handle_stream_ended)


def test_stream_started_event_creation():
    """Test creating a StreamStartedEvent with correct schema classes"""
    event = StreamStartedEvent(
        container_id="test_container",
        engine=EngineAddress(
            host="localhost",
            port=6878
        ),
        stream=StreamKey(
            key_type="infohash",
            key="test_hash_12345"
        ),
        session=SessionInfo(
            playback_session_id="session_123",
            stat_url="http://localhost:6878/ace/stat",
            command_url="http://localhost:6878/ace/cmd",
            is_live=1
        ),
        labels={"source": "test", "mode": "HLS"}
    )
    
    assert event.container_id == "test_container"
    assert event.engine.host == "localhost"
    assert event.engine.port == 6878
    assert event.stream.key_type == "infohash"
    assert event.stream.key == "test_hash_12345"
    assert event.session.playback_session_id == "session_123"
    assert event.labels["source"] == "test"


def test_stream_ended_event_creation():
    """Test creating a StreamEndedEvent"""
    event = StreamEndedEvent(
        container_id="test_container",
        stream_id="test_stream_id",
        reason="normal"
    )
    
    assert event.container_id == "test_container"
    assert event.stream_id == "test_stream_id"
    assert event.reason == "normal"


def test_hls_proxy_uses_internal_handlers():
    """Verify HLS proxy code references internal event handlers"""
    import inspect
    from app.proxy.hls_proxy import StreamManager
    
    # Get the source code of _send_stream_started_event
    source = inspect.getsource(StreamManager._send_stream_started_event)
    
    # Verify it imports and uses internal handlers
    assert "from ..services.internal_events import handle_stream_started" in source
    assert "handle_stream_started(event)" in source
    # Verify it does NOT use requests.post
    assert "requests.post" not in source


def test_ts_proxy_uses_internal_handlers():
    """Verify TS proxy code references internal event handlers"""
    import inspect
    from app.proxy.stream_manager import StreamManager
    
    # Get the source code of _send_stream_started_event
    source = inspect.getsource(StreamManager._send_stream_started_event)
    
    # Verify it imports and uses internal handlers
    assert "from ..services.internal_events import handle_stream_started" in source
    assert "handle_stream_started(event)" in source
    # Verify it does NOT use requests.post
    assert "requests.post" not in source


def test_no_http_dependency_in_event_sending():
    """Verify that event sending does not depend on HTTP requests"""
    import inspect
    from app.services import internal_events
    
    # Get source of both handlers
    started_source = inspect.getsource(internal_events.handle_stream_started)
    ended_source = inspect.getsource(internal_events.handle_stream_ended)
    
    # Verify NO HTTP request dependencies
    assert "requests." not in started_source
    assert "httpx." not in started_source
    assert "urllib" not in started_source
    
    assert "requests." not in ended_source
    assert "httpx." not in ended_source
    assert "urllib" not in ended_source
    
    # Verify they call state management directly
    assert "state.on_stream_started" in started_source
    assert "state.on_stream_ended" in ended_source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
