"""
Test redundant VPN forwarding functionality.

This test validates that:
1. In redundant mode, each VPN can have its own forwarded engine
2. When both VPNs have forwarded ports, two engines are marked as forwarded
3. VPN-specific forwarded engine tracking works correctly
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


def test_vpn_specific_forwarded_engine():
    """Test getting forwarded engine for a specific VPN."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'):
        state = State()
        state.clear_state()
        
        # Add two engines, each assigned to different VPNs
        engine1 = EngineState(
            container_id="engine1",
            container_name="engine-1",
            host="gluetun1",
            port=19000,
            forwarded=True,
            vpn_container="gluetun1",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2 = EngineState(
            container_id="engine2",
            container_name="engine-2",
            host="gluetun2",
            port=19500,
            forwarded=True,
            vpn_container="gluetun2",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1"] = engine1
        state.engines["engine2"] = engine2
        
        # Verify VPN-specific forwarded engines
        vpn1_forwarded = state.get_forwarded_engine_for_vpn("gluetun1")
        assert vpn1_forwarded is not None
        assert vpn1_forwarded.container_id == "engine1"
        
        vpn2_forwarded = state.get_forwarded_engine_for_vpn("gluetun2")
        assert vpn2_forwarded is not None
        assert vpn2_forwarded.container_id == "engine2"
        
        # Verify has_forwarded_engine_for_vpn
        assert state.has_forwarded_engine_for_vpn("gluetun1") is True
        assert state.has_forwarded_engine_for_vpn("gluetun2") is True
        assert state.has_forwarded_engine_for_vpn("gluetun3") is False


def test_multiple_forwarded_engines():
    """Test that multiple engines can be forwarded in redundant mode."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'):
        state = State()
        state.clear_state()
        
        # Add multiple engines with different VPN assignments
        engines = [
            EngineState(
                container_id=f"engine{i}",
                container_name=f"engine-{i}",
                host=f"gluetun{1 if i % 2 == 0 else 2}",
                port=19000 + i,
                forwarded=(i < 2),  # First two are forwarded
                vpn_container=f"gluetun{1 if i % 2 == 0 else 2}",
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc)
            )
            for i in range(4)
        ]
        
        for engine in engines:
            state.engines[engine.container_id] = engine
        
        # Verify forwarded engines per VPN
        # engine0 should be forwarded for gluetun1
        # engine1 should be forwarded for gluetun2
        vpn1_forwarded = state.get_forwarded_engine_for_vpn("gluetun1")
        vpn2_forwarded = state.get_forwarded_engine_for_vpn("gluetun2")
        
        assert vpn1_forwarded is not None
        assert vpn1_forwarded.container_id == "engine0"
        
        assert vpn2_forwarded is not None
        assert vpn2_forwarded.container_id == "engine1"


def test_no_forwarded_engine_for_vpn():
    """Test when a VPN has no forwarded engine."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'):
        state = State()
        state.clear_state()
        
        # Add engine without forwarded flag
        engine1 = EngineState(
            container_id="engine1",
            container_name="engine-1",
            host="gluetun1",
            port=19000,
            forwarded=False,
            vpn_container="gluetun1",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1"] = engine1
        
        # Verify no forwarded engine for gluetun1
        assert state.has_forwarded_engine_for_vpn("gluetun1") is False
        assert state.get_forwarded_engine_for_vpn("gluetun1") is None


def test_mixed_forwarded_state():
    """Test state with one VPN having forwarded engine and another not."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'):
        state = State()
        state.clear_state()
        
        # VPN1 has forwarded engine, VPN2 does not
        engine1 = EngineState(
            container_id="engine1",
            container_name="engine-1",
            host="gluetun1",
            port=19000,
            forwarded=True,
            vpn_container="gluetun1",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2 = EngineState(
            container_id="engine2",
            container_name="engine-2",
            host="gluetun2",
            port=19500,
            forwarded=False,
            vpn_container="gluetun2",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1"] = engine1
        state.engines["engine2"] = engine2
        
        # Verify VPN1 has forwarded, VPN2 doesn't
        assert state.has_forwarded_engine_for_vpn("gluetun1") is True
        assert state.has_forwarded_engine_for_vpn("gluetun2") is False
        
        vpn1_forwarded = state.get_forwarded_engine_for_vpn("gluetun1")
        assert vpn1_forwarded is not None
        assert vpn1_forwarded.container_id == "engine1"
        
        vpn2_forwarded = state.get_forwarded_engine_for_vpn("gluetun2")
        assert vpn2_forwarded is None


if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v"])
    else:
        # Run tests manually without pytest
        print("\n" + "=" * 60)
        print("Running redundant VPN forwarding tests")
        print("=" * 60)
        
        try:
            test_vpn_specific_forwarded_engine()
            test_multiple_forwarded_engines()
            test_no_forwarded_engine_for_vpn()
            test_mixed_forwarded_state()
            
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
