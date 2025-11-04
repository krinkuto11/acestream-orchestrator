#!/usr/bin/env python3
"""
Test that the collector correctly handles both camelCase and snake_case field names
for speed fields (speedDown/speed_down, speedUp/speed_up).

This addresses the issue where some AceStream engine versions return camelCase field names
while others return snake_case, causing speed metrics to always show 0.
"""

import sys
import os
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.collector import Collector
from app.services.state import State
from app.models.schemas import StreamStartedEvent, EngineAddress, StreamKey, SessionInfo
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables before running tests
Base.metadata.create_all(bind=db_engine)


def test_collector_with_snake_case_fields():
    """Test collector with traditional snake_case field names."""
    print("Testing collector with snake_case field names (speed_down, speed_up)...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_snake",
        engine=EngineAddress(host="127.0.0.1", port=8080),
        stream=StreamKey(key_type="content_id", key="test_stream_snake"),
        session=SessionInfo(
            playback_session_id="test_session_snake",
            stat_url="http://127.0.0.1:8080/ace/stat/test_session_snake",
            command_url="http://127.0.0.1:8080/ace/cmd/test_session_snake",
            is_live=1
        ),
        labels={"stream_id": "stream_snake"}
    )
    
    test_state.on_stream_started(evt)
    
    # Create a mock HTTP response with snake_case field names
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "peers": 5,
            "speed_down": 1048576,  # 1 MB/s in bytes
            "speed_up": 524288,     # 0.5 MB/s in bytes
            "downloaded": 10485760,
            "uploaded": 2621440,
            "status": "playing"
        }
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector
    collector = Collector()
    
    # Run the collector once with patched state
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "stream_snake",
                "http://127.0.0.1:8080/ace/stat/test_session_snake"
            )
    
    asyncio.run(run_test())
    
    # Verify stream stats were collected correctly
    stats = test_state.get_stream_stats("stream_snake")
    assert len(stats) == 1, "Should have 1 stat snapshot"
    assert stats[0].peers == 5, f"Expected 5 peers, got {stats[0].peers}"
    assert stats[0].speed_down == 1048576, f"Expected speed_down=1048576, got {stats[0].speed_down}"
    assert stats[0].speed_up == 524288, f"Expected speed_up=524288, got {stats[0].speed_up}"
    
    print("✅ Collector with snake_case fields test passed!")


def test_collector_with_camel_case_fields():
    """Test collector with camelCase field names (speedDown, speedUp)."""
    print("Testing collector with camelCase field names (speedDown, speedUp)...")
    
    # Create a fresh state
    test_state = State()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_camel",
        engine=EngineAddress(host="127.0.0.1", port=8081),
        stream=StreamKey(key_type="content_id", key="test_stream_camel"),
        session=SessionInfo(
            playback_session_id="test_session_camel",
            stat_url="http://127.0.0.1:8081/ace/stat/test_session_camel",
            command_url="http://127.0.0.1:8081/ace/cmd/test_session_camel",
            is_live=1
        ),
        labels={"stream_id": "stream_camel"}
    )
    
    test_state.on_stream_started(evt)
    
    # Create a mock HTTP response with camelCase field names
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "peers": 10,
            "speedDown": 2097152,   # 2 MB/s in bytes (camelCase!)
            "speedUp": 1048576,     # 1 MB/s in bytes (camelCase!)
            "downloaded": 20971520,
            "uploaded": 5242880,
            "status": "playing"
        }
    }
    
    # Create a mock HTTP client
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    
    # Create collector
    collector = Collector()
    
    # Run the collector once with patched state
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(
                mock_client,
                "stream_camel",
                "http://127.0.0.1:8081/ace/stat/test_session_camel"
            )
    
    asyncio.run(run_test())
    
    # Verify stream stats were collected correctly
    stats = test_state.get_stream_stats("stream_camel")
    assert len(stats) == 1, "Should have 1 stat snapshot"
    assert stats[0].peers == 10, f"Expected 10 peers, got {stats[0].peers}"
    assert stats[0].speed_down == 2097152, f"Expected speed_down=2097152, got {stats[0].speed_down}"
    assert stats[0].speed_up == 1048576, f"Expected speed_up=1048576, got {stats[0].speed_up}"
    
    print("✅ Collector with camelCase fields test passed!")


def test_metrics_aggregation_with_camel_case():
    """Test that metrics correctly aggregate speeds from camelCase API responses."""
    print("Testing metrics aggregation with camelCase field names...")
    
    from app.services.metrics import update_custom_metrics
    from app.services.metrics import (
        orch_total_upload_speed_mbps,
        orch_total_download_speed_mbps,
        orch_total_peers
    )
    from app.services.state import state
    from app.models.schemas import StreamStatSnapshot
    from datetime import datetime, timezone
    
    # Clear state
    state.clear_state()
    
    # Start a stream
    evt = StreamStartedEvent(
        container_id="test_container_metrics",
        engine=EngineAddress(host="127.0.0.1", port=8082),
        stream=StreamKey(key_type="content_id", key="test_stream_metrics"),
        session=SessionInfo(
            playback_session_id="test_session_metrics",
            stat_url="http://127.0.0.1:8082/ace/stat/test_session_metrics",
            command_url="http://127.0.0.1:8082/ace/cmd/test_session_metrics",
            is_live=1
        ),
        labels={"stream_id": "stream_metrics"}
    )
    
    state.on_stream_started(evt)
    
    # Simulate collector having received camelCase response and processed it correctly
    stream_id = "stream_metrics"
    snap = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=15,
        speed_down=3145728,  # 3 MB/s in bytes
        speed_up=1572864,    # 1.5 MB/s in bytes
        downloaded=31457280,
        uploaded=7864320,
        status="playing"
    )
    state.append_stat(stream_id, snap)
    
    # Update metrics
    update_custom_metrics()
    
    # Verify metrics reflect the data
    assert orch_total_peers._value.get() == 15, f"Expected 15 peers, got {orch_total_peers._value.get()}"
    
    expected_download_speed = round(3145728 / (1024 * 1024), 2)  # 3.0 MB/s
    expected_upload_speed = round(1572864 / (1024 * 1024), 2)    # 1.5 MB/s
    
    actual_download = orch_total_download_speed_mbps._value.get()
    actual_upload = orch_total_upload_speed_mbps._value.get()
    
    assert actual_download == expected_download_speed, \
        f"Expected download speed {expected_download_speed} MB/s, got {actual_download} MB/s"
    assert actual_upload == expected_upload_speed, \
        f"Expected upload speed {expected_upload_speed} MB/s, got {actual_upload} MB/s"
    
    # Clean up
    state.clear_state()
    
    print("✅ Metrics aggregation with camelCase test passed!")


def test_collector_with_zero_speed_snake_case():
    """Test that zero speed values are correctly preserved with snake_case."""
    print("Testing collector preserves zero speed values (snake_case)...")
    
    test_state = State()
    
    evt = StreamStartedEvent(
        container_id="test_zero_snake",
        engine=EngineAddress(host="127.0.0.1", port=8083),
        stream=StreamKey(key_type="content_id", key="test_zero_snake"),
        session=SessionInfo(
            playback_session_id="test_zero_snake",
            stat_url="http://127.0.0.1:8083/ace/stat/test_zero_snake",
            command_url="http://127.0.0.1:8083/ace/cmd/test_zero_snake",
            is_live=1
        ),
        labels={"stream_id": "zero_snake"}
    )
    
    test_state.on_stream_started(evt)
    
    # Response with zero upload speed (valid scenario: downloading but not uploading)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "peers": 3,
            "speed_down": 1048576,  # 1 MB/s download
            "speed_up": 0,          # 0 upload (not uploading)
            "downloaded": 5242880,
            "uploaded": 0,
            "status": "playing"
        }
    }
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    collector = Collector()
    
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(mock_client, "zero_snake", 
                                        "http://127.0.0.1:8083/ace/stat/test_zero_snake")
    
    asyncio.run(run_test())
    
    stats = test_state.get_stream_stats("zero_snake")
    assert len(stats) == 1, "Should have 1 stat snapshot"
    assert stats[0].speed_down == 1048576, f"Expected speed_down=1048576, got {stats[0].speed_down}"
    assert stats[0].speed_up == 0, f"Expected speed_up=0, got {stats[0].speed_up} (zero must be preserved!)"
    
    print("✅ Zero speed values preserved (snake_case) test passed!")


def test_collector_with_zero_speed_camel_case():
    """Test that zero speed values are correctly preserved with camelCase."""
    print("Testing collector preserves zero speed values (camelCase)...")
    
    test_state = State()
    
    evt = StreamStartedEvent(
        container_id="test_zero_camel",
        engine=EngineAddress(host="127.0.0.1", port=8084),
        stream=StreamKey(key_type="content_id", key="test_zero_camel"),
        session=SessionInfo(
            playback_session_id="test_zero_camel",
            stat_url="http://127.0.0.1:8084/ace/stat/test_zero_camel",
            command_url="http://127.0.0.1:8084/ace/cmd/test_zero_camel",
            is_live=1
        ),
        labels={"stream_id": "zero_camel"}
    )
    
    test_state.on_stream_started(evt)
    
    # Response with zero download speed in camelCase
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "peers": 0,
            "speedDown": 0,         # 0 download (camelCase!)
            "speedUp": 524288,      # 0.5 MB/s upload (camelCase!)
            "downloaded": 0,
            "uploaded": 2621440,
            "status": "seeding"
        }
    }
    
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    collector = Collector()
    
    async def run_test():
        with patch('app.services.collector.state', test_state):
            await collector._collect_one(mock_client, "zero_camel",
                                        "http://127.0.0.1:8084/ace/stat/test_zero_camel")
    
    asyncio.run(run_test())
    
    stats = test_state.get_stream_stats("zero_camel")
    assert len(stats) == 1, "Should have 1 stat snapshot"
    assert stats[0].speed_down == 0, f"Expected speed_down=0, got {stats[0].speed_down} (zero must be preserved!)"
    assert stats[0].speed_up == 524288, f"Expected speed_up=524288, got {stats[0].speed_up}"
    
    print("✅ Zero speed values preserved (camelCase) test passed!")


if __name__ == "__main__":
    test_collector_with_snake_case_fields()
    test_collector_with_camel_case_fields()
    test_collector_with_zero_speed_snake_case()
    test_collector_with_zero_speed_camel_case()
    test_metrics_aggregation_with_camel_case()
    print("\n✅ All camelCase speed field tests passed!")
