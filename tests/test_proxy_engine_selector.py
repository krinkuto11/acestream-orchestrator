"""Tests for proxy engine selector"""

import pytest
import asyncio
from app.services.proxy.engine_selector import EngineSelector, EngineInfo
from app.services.state import state
from app.models.schemas import EngineState


@pytest.fixture
def setup_state():
    """Setup state with test engines"""
    from datetime import datetime, timezone
    
    # Clear state
    state.engines.clear()
    state.streams.clear()
    
    now = datetime.now(timezone.utc)
    
    # Add test engines
    # Engine 1: Forwarded, 2 active streams
    state.engines["engine1"] = EngineState(
        container_id="engine1",
        host="127.0.0.1",
        port=19001,
        status="running",
        labels={"acestream.forwarded": "true"},
        health_status="healthy",
        first_seen=now,
        last_seen=now,
    )
    
    # Engine 2: Not forwarded, 1 active stream
    state.engines["engine2"] = EngineState(
        container_id="engine2",
        host="127.0.0.1",
        port=19002,
        status="running",
        labels={},
        health_status="healthy",
        first_seen=now,
        last_seen=now,
    )
    
    # Engine 3: Forwarded, no active streams
    state.engines["engine3"] = EngineState(
        container_id="engine3",
        host="127.0.0.1",
        port=19003,
        status="running",
        labels={"acestream.forwarded": "true"},
        health_status="healthy",
        first_seen=now,
        last_seen=now,
    )
    
    # Engine 4: Unhealthy
    state.engines["engine4"] = EngineState(
        container_id="engine4",
        host="127.0.0.1",
        port=19004,
        status="running",
        labels={},
        health_status="unhealthy",
        first_seen=now,
        last_seen=now,
    )
    
    yield
    
    # Cleanup
    state.engines.clear()
    state.streams.clear()


def test_engine_score_calculation():
    """Test that engine scoring works correctly"""
    # Forwarded engine with no streams should have highest score
    engine1 = EngineInfo(
        container_id="e1",
        host="127.0.0.1",
        port=19001,
        is_forwarded=True,
        active_streams=0,
        health_status="healthy",
    )
    
    # Non-forwarded engine with no streams
    engine2 = EngineInfo(
        container_id="e2",
        host="127.0.0.1",
        port=19002,
        is_forwarded=False,
        active_streams=0,
        health_status="healthy",
    )
    
    # Forwarded engine with 2 streams
    engine3 = EngineInfo(
        container_id="e3",
        host="127.0.0.1",
        port=19003,
        is_forwarded=True,
        active_streams=2,
        health_status="healthy",
    )
    
    # Unhealthy engine
    engine4 = EngineInfo(
        container_id="e4",
        host="127.0.0.1",
        port=19004,
        is_forwarded=True,
        active_streams=0,
        health_status="unhealthy",
    )
    
    # Test scores
    assert engine1.get_score() == 1000  # Forwarded + 0 streams
    assert engine2.get_score() == 0  # Not forwarded + 0 streams
    assert engine3.get_score() == 980  # Forwarded - 20 (2 streams * 10)
    assert engine4.get_score() == -1000  # Unhealthy
    
    # Verify ordering
    assert engine1.get_score() > engine3.get_score()  # Fewer streams preferred
    assert engine3.get_score() > engine2.get_score()  # Forwarded preferred
    assert engine2.get_score() > engine4.get_score()  # Healthy preferred


@pytest.mark.asyncio
async def test_select_best_engine_prioritizes_forwarded(setup_state):
    """Test that forwarded engines are prioritized"""
    selector = EngineSelector()
    
    engine = await selector.select_best_engine()
    
    assert engine is not None
    # Should select a forwarded engine (engine1 or engine3, both have 0 streams)
    # Both are equally good candidates
    assert engine["is_forwarded"] is True
    assert engine["container_id"] in ["engine1", "engine3"]


@pytest.mark.asyncio
async def test_select_best_engine_balances_load(setup_state):
    """Test that load balancing works within forwarded engines"""
    from app.models.schemas import StreamState
    from datetime import datetime, timezone
    
    # Add streams to engine3 to make it equal to engine1
    state.streams["stream1"] = StreamState(
        id="s1",
        key_type="infohash",
        key="test123",
        container_id="engine3",
        playback_session_id="session1",
        stat_url="http://127.0.0.1:19003/ace/stat/test/session1",
        command_url="http://127.0.0.1:19003/ace/cmd/test/session1",
        is_live=False,
        status="started",
        started_at=datetime.now(timezone.utc),
    )
    state.streams["stream2"] = StreamState(
        id="s2",
        key_type="infohash",
        key="test456",
        container_id="engine3",
        playback_session_id="session2",
        stat_url="http://127.0.0.1:19003/ace/stat/test/session2",
        command_url="http://127.0.0.1:19003/ace/cmd/test/session2",
        is_live=False,
        status="started",
        started_at=datetime.now(timezone.utc),
    )
    
    selector = EngineSelector()
    
    # Invalidate cache to force refresh
    selector.invalidate_cache()
    
    engine = await selector.select_best_engine()
    
    assert engine is not None
    # Now both forwarded engines have 2 streams, should select either one
    # (both have same score)
    assert engine["is_forwarded"] is True
    assert engine["container_id"] in ["engine1", "engine3"]


@pytest.mark.asyncio
async def test_select_best_engine_filters_unhealthy(setup_state):
    """Test that unhealthy engines are filtered out"""
    # Make all engines except engine4 unhealthy
    state.engines["engine1"].health_status = "unhealthy"
    state.engines["engine2"].health_status = "unhealthy"
    state.engines["engine3"].health_status = "unhealthy"
    
    selector = EngineSelector()
    selector.invalidate_cache()
    
    engine = await selector.select_best_engine()
    
    # No healthy engines available
    assert engine is None


@pytest.mark.asyncio
async def test_engine_cache(setup_state):
    """Test that engine cache works correctly"""
    selector = EngineSelector(cache_ttl=1)
    
    # First call should populate cache
    engines1 = await selector._get_engines()
    
    # Second call should use cache (same list object)
    engines2 = await selector._get_engines()
    
    # Should be the same cached list (same object identity)
    assert engines1 is engines2
    
    # Wait for cache to expire
    await asyncio.sleep(1.1)
    
    # Third call should refresh cache
    engines3 = await selector._get_engines()
    
    # Should be a different list object
    assert engines1 is not engines3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
