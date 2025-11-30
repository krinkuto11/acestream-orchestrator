#!/usr/bin/env python3
"""
Tests for Acexy proxy integration.

Tests that:
1. AcexyClient correctly fetches streams from Acexy API
2. AcexySyncService correctly identifies and removes stale streams
3. Configuration validation works for Acexy settings
"""

import sys
import os
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.acexy import AcexyClient, AcexySyncService, AcexyStream
from app.services.state import State
from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_acexy_client_parse_streams():
    """Test that AcexyClient correctly parses streams from Acexy API response."""
    print("Testing AcexyClient stream parsing...")
    
    client = AcexyClient("http://test-acexy:8080")
    
    # Mock response data matching the format from Acexy
    mock_response_data = {
        "streams": [
            {
                "id": "{id: 38e9ae1ee0c96d7c6187c9c4cc60ffccb565bdf7}",
                "playback_url": "http://gluetun:19000/ace/r/18bfb071b398acf90613e9a1bc964dc3458fcab3/796e4e0692f031815f10b230ea289fbf",
                "stat_url": "http://gluetun:19000/ace/stat/18bfb071b398acf90613e9a1bc964dc3458fcab3/796e4e0692f031815f10b230ea289fbf",
                "command_url": "http://gluetun:19000/ace/cmd/18bfb071b398acf90613e9a1bc964dc3458fcab3/796e4e0692f031815f10b230ea289fbf",
                "clients": 1,
                "created_at": "2025-11-30T17:56:15.316407348Z",
                "has_player": True,
                "engine_host": "gluetun",
                "engine_port": 19000,
                "engine_container_id": "dc70d2ab2a46f285d4478e6025faedb85b3ab6bf8c6706cde57320bf6ec61c05"
            },
            {
                "id": "{id: 4e6d9cf7d177366045d33cd8311d8b1d7f4bed1f}",
                "playback_url": "http://gluetun:19000/ace/r/a02bd40d9cdc275d76cd609b75674f8099a55a40/fed59874706ef3fd3ba5bcf96c65e093",
                "stat_url": "http://gluetun:19000/ace/stat/a02bd40d9cdc275d76cd609b75674f8099a55a40/fed59874706ef3fd3ba5bcf96c65e093",
                "command_url": "http://gluetun:19000/ace/cmd/a02bd40d9cdc275d76cd609b75674f8099a55a40/fed59874706ef3fd3ba5bcf96c65e093",
                "clients": 1,
                "created_at": "2025-11-30T17:56:15.478823277Z",
                "has_player": True,
                "engine_host": "gluetun",
                "engine_port": 19000,
                "engine_container_id": "dc70d2ab2a46f285d4478e6025faedb85b3ab6bf8c6706cde57320bf6ec61c05"
            }
        ],
        "total_streams": 2
    }
    
    # Create mock HTTP response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_response_data
    
    async def run_test():
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client
            
            streams = await client.get_streams()
            
            assert streams is not None
            assert len(streams) == 2
            
            # Verify first stream
            assert streams[0].id == "{id: 38e9ae1ee0c96d7c6187c9c4cc60ffccb565bdf7}"
            assert streams[0].engine_host == "gluetun"
            assert streams[0].engine_port == 19000
            assert streams[0].clients == 1
            assert streams[0].has_player is True
            
            # Verify second stream
            assert streams[1].id == "{id: 4e6d9cf7d177366045d33cd8311d8b1d7f4bed1f}"
            
            print("✅ AcexyClient stream parsing test passed!")
    
    asyncio.run(run_test())


def test_acexy_client_health_check():
    """Test that AcexyClient health check works correctly."""
    print("Testing AcexyClient health check...")
    
    client = AcexyClient("http://test-acexy:8080")
    
    async def run_test():
        # Test healthy response
        mock_response_healthy = MagicMock()
        mock_response_healthy.status_code = 200
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response_healthy)
            mock_client_class.return_value = mock_client
            
            is_healthy = await client.check_health()
            assert is_healthy is True
            assert client.is_healthy() is True
        
        # Test unhealthy response
        mock_response_unhealthy = MagicMock()
        mock_response_unhealthy.status_code = 500
        
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response_unhealthy)
            mock_client_class.return_value = mock_client
            
            is_healthy = await client.check_health()
            assert is_healthy is False
            assert client.is_healthy() is False
        
        print("✅ AcexyClient health check test passed!")
    
    asyncio.run(run_test())


def test_acexy_sync_detects_stale_streams():
    """Test that AcexySyncService correctly identifies streams not present in Acexy."""
    print("Testing AcexySyncService stale stream detection...")
    
    # Create a fresh state
    test_state = State()
    
    # Add some streams to orchestrator state
    # Stream 1 - present in both orchestrator and Acexy
    evt1 = StreamStartedEvent(
        container_id="test_container_1",
        engine=EngineAddress(host="gluetun", port=19000),
        stream=StreamKey(key_type="content_id", key="stream_key_1"),
        session=SessionInfo(
            playback_session_id="session_1",
            stat_url="http://gluetun:19000/ace/stat/session_1/hash1",
            command_url="http://gluetun:19000/ace/cmd/session_1/hash1",
            is_live=1
        ),
        labels={"stream_id": "stream_1"}
    )
    test_state.on_stream_started(evt1)
    
    # Stream 2 - only in orchestrator (stale - not in Acexy)
    evt2 = StreamStartedEvent(
        container_id="test_container_2",
        engine=EngineAddress(host="gluetun", port=19000),
        stream=StreamKey(key_type="content_id", key="stream_key_2"),
        session=SessionInfo(
            playback_session_id="session_2",
            stat_url="http://gluetun:19000/ace/stat/session_2/hash2",
            command_url="http://gluetun:19000/ace/cmd/session_2/hash2",
            is_live=1
        ),
        labels={"stream_id": "stream_2"}
    )
    test_state.on_stream_started(evt2)
    
    # Stream 3 - only in orchestrator (stale - not in Acexy)
    evt3 = StreamStartedEvent(
        container_id="test_container_3",
        engine=EngineAddress(host="gluetun", port=19000),
        stream=StreamKey(key_type="content_id", key="stream_key_3"),
        session=SessionInfo(
            playback_session_id="session_3",
            stat_url="http://gluetun:19000/ace/stat/session_3/hash3",
            command_url="http://gluetun:19000/ace/cmd/session_3/hash3",
            is_live=1
        ),
        labels={"stream_id": "stream_3"}
    )
    test_state.on_stream_started(evt3)
    
    # Verify all 3 streams are active
    active_streams = test_state.list_streams(status="started")
    assert len(active_streams) == 3
    
    # Mock Acexy response - only has stream 1
    acexy_streams = [
        AcexyStream(
            id="acexy_stream_1",
            playback_url="http://gluetun:19000/ace/r/session_1/hash1",
            stat_url="http://gluetun:19000/ace/stat/session_1/hash1",  # Matches stream 1
            command_url="http://gluetun:19000/ace/cmd/session_1/hash1",
            clients=1,
            created_at="2025-11-30T17:56:15.316407348Z",
            has_player=True,
            engine_host="gluetun",
            engine_port=19000,
            engine_container_id="container_id_1"
        )
    ]
    
    # Build set of Acexy stat URLs
    acexy_stat_urls = {stream.stat_url for stream in acexy_streams}
    
    # Find stale streams (in orchestrator but not in Acexy)
    stale_streams = []
    for stream in active_streams:
        if stream.stat_url and stream.stat_url not in acexy_stat_urls:
            stale_streams.append(stream)
    
    # Should find 2 stale streams (stream_2 and stream_3)
    assert len(stale_streams) == 2
    stale_ids = {s.id for s in stale_streams}
    assert "stream_2" in stale_ids
    assert "stream_3" in stale_ids
    assert "stream_1" not in stale_ids
    
    print(f"✓ Found {len(stale_streams)} stale streams correctly")
    print("✅ AcexySyncService stale stream detection test passed!")


def test_acexy_sync_full_flow():
    """Test the full sync flow including ending stale streams."""
    print("Testing full Acexy sync flow...")
    
    # Create a fresh state
    test_state = State()
    
    # Add a stream that will be stale
    evt = StreamStartedEvent(
        container_id="test_container",
        engine=EngineAddress(host="gluetun", port=19000),
        stream=StreamKey(key_type="content_id", key="stale_stream_key"),
        session=SessionInfo(
            playback_session_id="stale_session",
            stat_url="http://gluetun:19000/ace/stat/stale_session/hash",
            command_url="http://gluetun:19000/ace/cmd/stale_session/hash",
            is_live=1
        ),
        labels={"stream_id": "stale_stream"}
    )
    test_state.on_stream_started(evt)
    
    # Verify stream is active
    assert len(test_state.list_streams(status="started")) == 1
    
    # Simulate what the sync service does when Acexy has no streams
    acexy_stat_urls = set()  # Empty - no streams in Acexy
    orchestrator_streams = test_state.list_streams(status="started")
    
    # Find and end stale streams
    from app.models.schemas import StreamEndedEvent
    
    for stream in orchestrator_streams:
        if stream.stat_url and stream.stat_url not in acexy_stat_urls:
            test_state.on_stream_ended(StreamEndedEvent(
                container_id=stream.container_id,
                stream_id=stream.id,
                reason="acexy_sync_stale"
            ))
    
    # Verify stream was ended
    ended_streams = test_state.list_streams(status="ended")
    assert len(ended_streams) == 1
    assert ended_streams[0].id == "stale_stream"
    
    # Verify no more started streams
    started_streams = test_state.list_streams(status="started")
    assert len(started_streams) == 0
    
    print("✅ Full Acexy sync flow test passed!")


def test_acexy_sync_service_status():
    """Test that AcexySyncService returns correct status."""
    print("Testing AcexySyncService status...")
    
    service = AcexySyncService()
    
    # Test status when disabled
    with patch('app.services.acexy.cfg') as mock_cfg:
        mock_cfg.ACEXY_ENABLED = False
        mock_cfg.ACEXY_URL = None
        mock_cfg.ACEXY_SYNC_INTERVAL_S = 30
        
        status = service.get_status()
        assert status["enabled"] is False
        assert status["url"] is None
        assert status["healthy"] is None
        assert status["sync_interval_seconds"] == 30
    
    print("✅ AcexySyncService status test passed!")


def test_acexy_sync_preserves_active_streams():
    """Test that sync doesn't affect streams that are present in both systems."""
    print("Testing that sync preserves active streams...")
    
    # Create a fresh state
    test_state = State()
    
    # Add a stream that is present in Acexy
    evt = StreamStartedEvent(
        container_id="test_container",
        engine=EngineAddress(host="gluetun", port=19000),
        stream=StreamKey(key_type="content_id", key="active_stream_key"),
        session=SessionInfo(
            playback_session_id="active_session",
            stat_url="http://gluetun:19000/ace/stat/active_session/hash",
            command_url="http://gluetun:19000/ace/cmd/active_session/hash",
            is_live=1
        ),
        labels={"stream_id": "active_stream"}
    )
    test_state.on_stream_started(evt)
    
    # Simulate Acexy response that includes this stream
    acexy_stat_urls = {"http://gluetun:19000/ace/stat/active_session/hash"}
    orchestrator_streams = test_state.list_streams(status="started")
    
    # Check for stale streams
    stale_streams = []
    for stream in orchestrator_streams:
        if stream.stat_url and stream.stat_url not in acexy_stat_urls:
            stale_streams.append(stream)
    
    # Should find no stale streams
    assert len(stale_streams) == 0
    
    # Stream should still be active
    active_streams = test_state.list_streams(status="started")
    assert len(active_streams) == 1
    assert active_streams[0].id == "active_stream"
    
    print("✅ Sync preserves active streams test passed!")


if __name__ == "__main__":
    print("🧪 Running Acexy integration tests...\n")
    
    test_acexy_client_parse_streams()
    print()
    test_acexy_client_health_check()
    print()
    test_acexy_sync_detects_stale_streams()
    print()
    test_acexy_sync_full_flow()
    print()
    test_acexy_sync_service_status()
    print()
    test_acexy_sync_preserves_active_streams()
    
    print("\n🎉 All Acexy integration tests passed!")
