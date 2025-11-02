#!/usr/bin/env python3
"""
Test VPN connectivity double-check using engine network connection status.

Tests that the gluetun service properly uses the engine's network connection
status endpoint to verify VPN connectivity when Gluetun reports unhealthy.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.gluetun import _double_check_connectivity_via_engines
from app.services.state import State
from app.models.schemas import EngineState


def create_test_engine(container_id: str, container_name: str, port: int) -> EngineState:
    """Helper function to create a test engine with standard defaults."""
    return EngineState(
        container_id=container_id,
        container_name=container_name,
        host="127.0.0.1",
        port=port,
        labels={},
        forwarded=False,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        streams=[],
        health_status="healthy",
        last_health_check=None,
        last_stream_usage=datetime.now(timezone.utc),
        last_cache_cleanup=None,
        cache_size_bytes=None
    )


def test_no_engines_available():
    """Test that function returns unhealthy when no engines are available."""
    print("Testing VPN double-check with no engines...")
    
    # Create empty state
    test_state = State()
    
    with patch('app.services.state.state', test_state):
        result = _double_check_connectivity_via_engines()
    
    assert result == "unhealthy"
    print("✓ Returns 'unhealthy' when no engines available")


def test_all_engines_connected():
    """Test that function returns healthy when all engines report connected."""
    print("\nTesting VPN double-check with all engines connected...")
    
    # Create state with engines
    test_state = State()
    engine1 = create_test_engine("test_engine_1", "engine1", 8080)
    engine2 = create_test_engine("test_engine_2", "engine2", 8081)
    
    test_state.engines = {
        "test_engine_1": engine1,
        "test_engine_2": engine2
    }
    
    # Mock check_engine_network_connection to return True for all engines
    def mock_check(host, port):
        return True
    
    with patch('app.services.state.state', test_state):
        with patch('app.services.health.check_engine_network_connection', side_effect=mock_check):
            result = _double_check_connectivity_via_engines()
    
    assert result == "healthy"
    print("✓ Returns 'healthy' when all engines report connected")


def test_some_engines_connected():
    """Test that function returns healthy when at least one engine reports connected."""
    print("\nTesting VPN double-check with some engines connected...")
    
    # Create state with engines
    test_state = State()
    engine1 = create_test_engine("test_engine_1", "engine1", 8080)
    engine2 = create_test_engine("test_engine_2", "engine2", 8081)
    
    test_state.engines = {
        "test_engine_1": engine1,
        "test_engine_2": engine2
    }
    
    # Mock check_engine_network_connection to return True for first engine, False for second
    def mock_check(host, port):
        if port == 8080:
            return True
        return False
    
    with patch('app.services.state.state', test_state):
        with patch('app.services.health.check_engine_network_connection', side_effect=mock_check):
            result = _double_check_connectivity_via_engines()
    
    assert result == "healthy"
    print("✓ Returns 'healthy' when at least one engine reports connected")


def test_no_engines_connected():
    """Test that function returns unhealthy when no engines report connected."""
    print("\nTesting VPN double-check with no engines connected...")
    
    # Create state with engines
    test_state = State()
    engine1 = create_test_engine("test_engine_1", "engine1", 8080)
    engine2 = create_test_engine("test_engine_2", "engine2", 8081)
    
    test_state.engines = {
        "test_engine_1": engine1,
        "test_engine_2": engine2
    }
    
    # Mock check_engine_network_connection to return False for all engines
    def mock_check(host, port):
        return False
    
    with patch('app.services.state.state', test_state):
        with patch('app.services.health.check_engine_network_connection', side_effect=mock_check):
            result = _double_check_connectivity_via_engines()
    
    assert result == "unhealthy"
    print("✓ Returns 'unhealthy' when no engines report connected")


def test_check_with_exceptions():
    """Test that function handles exceptions gracefully."""
    print("\nTesting VPN double-check with exceptions...")
    
    # Create state with engines
    test_state = State()
    engine1 = create_test_engine("test_engine_1", "engine1", 8080)
    engine2 = create_test_engine("test_engine_2", "engine2", 8081)
    
    test_state.engines = {
        "test_engine_1": engine1,
        "test_engine_2": engine2
    }
    
    # Mock check_engine_network_connection to raise exception for first, return True for second
    def mock_check(host, port):
        if port == 8080:
            raise Exception("Network error")
        return True
    
    with patch('app.services.state.state', test_state):
        with patch('app.services.health.check_engine_network_connection', side_effect=mock_check):
            result = _double_check_connectivity_via_engines()
    
    assert result == "healthy"
    print("✓ Handles exceptions gracefully and continues checking other engines")


def test_check_engine_network_connection_endpoint():
    """Test that check_engine_network_connection uses the correct endpoint."""
    print("\nTesting check_engine_network_connection endpoint usage...")
    
    from app.services.health import check_engine_network_connection
    import httpx
    
    # Mock successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {
            "connected": True
        }
    }
    
    with patch('httpx.get', return_value=mock_response) as mock_get:
        result = check_engine_network_connection("127.0.0.1", 8080)
    
    # Verify the correct endpoint was called
    assert mock_get.called
    call_args = mock_get.call_args
    expected_url = "http://127.0.0.1:8080/server/api?api_version=3&method=get_network_connection_status"
    assert call_args[0][0] == expected_url
    assert result is True
    print("✓ Uses correct endpoint: /server/api?api_version=3&method=get_network_connection_status")


def test_check_engine_network_connection_disconnected():
    """Test that check_engine_network_connection handles disconnected response."""
    print("\nTesting check_engine_network_connection with disconnected status...")
    
    from app.services.health import check_engine_network_connection
    
    # Mock disconnected response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": {
            "connected": False
        }
    }
    
    with patch('httpx.get', return_value=mock_response):
        result = check_engine_network_connection("127.0.0.1", 8080)
    
    assert result is False
    print("✓ Returns False when engine reports connected=false")


if __name__ == "__main__":
    print("=" * 70)
    print("🧪 Running VPN Network Connectivity Tests...")
    print("=" * 70)
    
    test_no_engines_available()
    test_all_engines_connected()
    test_some_engines_connected()
    test_no_engines_connected()
    test_check_with_exceptions()
    test_check_engine_network_connection_endpoint()
    test_check_engine_network_connection_disconnected()
    
    print("\n" + "=" * 70)
    print("🎉 All VPN network connectivity tests passed!")
    print("=" * 70)
