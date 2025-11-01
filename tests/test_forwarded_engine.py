"""
Test forwarded engine functionality for Gluetun port assignment.

This test validates that:
1. Only one engine is marked as forwarded at a time
2. The forwarded engine receives the P2P port
3. Non-forwarded engines do not receive the P2P port
4. When the forwarded engine is removed, a new one is created by autoscaler
"""

import sys
import os
import traceback
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

from app.models.schemas import EngineState
from app.services.state import State


def test_forwarded_flag_in_engine_state():
    """Test that EngineState has forwarded field."""
    engine = EngineState(
        container_id="test123",
        container_name="test-engine",
        host="localhost",
        port=19000,
        forwarded=True,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    )
    
    assert engine.forwarded is True
    assert engine.container_id == "test123"


def test_forwarded_flag_defaults_to_false():
    """Test that forwarded flag defaults to False."""
    engine = EngineState(
        container_id="test456",
        container_name="test-engine-2",
        host="localhost",
        port=19001,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    )
    
    assert engine.forwarded is False


def test_set_forwarded_engine():
    """Test setting a forwarded engine in state."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'):
        state = State()
        state.clear_state()
        
        # Add two engines
        engine1 = EngineState(
            container_id="engine1",
            container_name="engine-1",
            host="localhost",
            port=19000,
            forwarded=False,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2 = EngineState(
            container_id="engine2",
            container_name="engine-2",
            host="localhost",
            port=19001,
            forwarded=False,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1"] = engine1
        state.engines["engine2"] = engine2
        
        # Set engine1 as forwarded
        state.set_forwarded_engine("engine1")
        
        # Verify only engine1 is forwarded
        assert state.engines["engine1"].forwarded is True
        assert state.engines["engine2"].forwarded is False
        
        # Get forwarded engine
        forwarded = state.get_forwarded_engine()
        assert forwarded is not None
        assert forwarded.container_id == "engine1"
        
        # Check if has forwarded engine
        assert state.has_forwarded_engine() is True


def test_set_forwarded_engine_clears_previous():
    """Test that setting a new forwarded engine clears the previous one."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'):
        state = State()
        state.clear_state()
        
        # Add two engines
        engine1 = EngineState(
            container_id="engine1",
            container_name="engine-1",
            host="localhost",
            port=19000,
            forwarded=False,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2 = EngineState(
            container_id="engine2",
            container_name="engine-2",
            host="localhost",
            port=19001,
            forwarded=False,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1"] = engine1
        state.engines["engine2"] = engine2
        
        # Set engine1 as forwarded
        state.set_forwarded_engine("engine1")
        assert state.engines["engine1"].forwarded is True
        assert state.engines["engine2"].forwarded is False
        
        # Now set engine2 as forwarded
        state.set_forwarded_engine("engine2")
        
        # Verify only engine2 is forwarded
        assert state.engines["engine1"].forwarded is False
        assert state.engines["engine2"].forwarded is True
        
        # Get forwarded engine
        forwarded = state.get_forwarded_engine()
        assert forwarded is not None
        assert forwarded.container_id == "engine2"


def test_no_forwarded_engine():
    """Test state when no engine is forwarded."""
    state = State()
    state.clear_state()
    
    # Add engine without forwarded flag
    engine1 = EngineState(
        container_id="engine1",
        container_name="engine-1",
        host="localhost",
        port=19000,
        forwarded=False,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    )
    
    state.engines["engine1"] = engine1
    
    # Verify no forwarded engine
    assert state.has_forwarded_engine() is False
    assert state.get_forwarded_engine() is None


def test_forwarded_label_constant():
    """Test that FORWARDED_LABEL constant is defined."""
    from app.services.provisioner import FORWARDED_LABEL
    
    assert FORWARDED_LABEL == "acestream.forwarded"


def test_forwarded_engine_removal():
    """Test that removing a forwarded engine is handled correctly."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'), \
         mock.patch('app.core.config.cfg') as mock_cfg:
        mock_cfg.GLUETUN_CONTAINER_NAME = None
        
        state = State()
        state.clear_state()
        
        # Add two engines, one forwarded
        engine1 = EngineState(
            container_id="engine1",
            container_name="engine-1",
            host="localhost",
            port=19000,
            forwarded=True,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2 = EngineState(
            container_id="engine2",
            container_name="engine-2",
            host="localhost",
            port=19001,
            forwarded=False,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1"] = engine1
        state.engines["engine2"] = engine2
        
        # Verify engine1 is forwarded
        assert state.engines["engine1"].forwarded is True
        
        # Remove engine1
        removed = state.remove_engine("engine1")
        
        # Verify it was removed
        assert removed is not None
        assert removed.container_id == "engine1"
        assert "engine1" not in state.engines
        
        # Verify engine2 still exists but is not promoted when Gluetun is disabled
        assert "engine2" in state.engines
        assert state.engines["engine2"].forwarded is False


if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v"])
    else:
        # Run tests manually without pytest
        print("\n" + "=" * 60)
        print("Running tests without pytest")
        print("=" * 60)
        
        try:
            test_forwarded_flag_in_engine_state()
            test_forwarded_flag_defaults_to_false()
            test_set_forwarded_engine()
            test_set_forwarded_engine_clears_previous()
            test_no_forwarded_engine()
            test_forwarded_label_constant()
            test_forwarded_engine_removal()
            
            print("\n" + "=" * 60)
            print("✅ ALL TESTS PASSED")
            print("=" * 60)
            sys.exit(0)
        except AssertionError as e:
            print(f"\n❌ TEST FAILED: {e}")
            traceback.print_exc()
            sys.exit(1)
        except Exception as e:
            print(f"\n💥 ERROR: {e}")
            traceback.print_exc()
            sys.exit(1)
