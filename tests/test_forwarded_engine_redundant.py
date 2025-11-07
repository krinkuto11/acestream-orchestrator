"""
Test forwarded engine functionality in redundant VPN mode.

This test validates that:
1. Each VPN can have its own forwarded engine
2. Setting a forwarded engine for VPN1 doesn't affect VPN2's forwarded engine
3. Multiple VPNs can have forwarded engines simultaneously
"""

import sys
import os
from datetime import datetime, timezone
import traceback

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

from app.models.schemas import EngineState
from app.services.state import State


def test_redundant_mode_multiple_forwarded_engines():
    """Test that redundant mode allows one forwarded engine per VPN."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'), \
         mock.patch('app.core.config.cfg') as mock_cfg:
        # Configure redundant mode
        mock_cfg.VPN_MODE = 'redundant'
        mock_cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
        mock_cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun_2'
        
        state = State()
        state.clear_state()
        
        # Add engines for VPN1 (gluetun)
        engine1_vpn1 = EngineState(
            container_id="engine1_vpn1",
            container_name="engine-1-vpn1",
            host="gluetun",
            port=19000,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2_vpn1 = EngineState(
            container_id="engine2_vpn1",
            container_name="engine-2-vpn1",
            host="gluetun",
            port=19001,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        # Add engines for VPN2 (gluetun_2)
        engine1_vpn2 = EngineState(
            container_id="engine1_vpn2",
            container_name="engine-1-vpn2",
            host="gluetun_2",
            port=19002,
            forwarded=False,
            vpn_container="gluetun_2",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2_vpn2 = EngineState(
            container_id="engine2_vpn2",
            container_name="engine-2-vpn2",
            host="gluetun_2",
            port=19003,
            forwarded=False,
            vpn_container="gluetun_2",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1_vpn1"] = engine1_vpn1
        state.engines["engine2_vpn1"] = engine2_vpn1
        state.engines["engine1_vpn2"] = engine1_vpn2
        state.engines["engine2_vpn2"] = engine2_vpn2
        
        # Set engine1_vpn1 as forwarded for gluetun
        state.set_forwarded_engine("engine1_vpn1")
        
        # Verify only engine1_vpn1 is forwarded
        assert state.engines["engine1_vpn1"].forwarded is True
        assert state.engines["engine2_vpn1"].forwarded is False
        assert state.engines["engine1_vpn2"].forwarded is False
        assert state.engines["engine2_vpn2"].forwarded is False
        
        # Verify VPN-specific queries work
        assert state.has_forwarded_engine_for_vpn("gluetun") is True
        assert state.has_forwarded_engine_for_vpn("gluetun_2") is False
        
        # Now set engine1_vpn2 as forwarded for gluetun_2
        state.set_forwarded_engine("engine1_vpn2")
        
        # Verify both VPNs have their own forwarded engine
        assert state.engines["engine1_vpn1"].forwarded is True  # Should still be forwarded
        assert state.engines["engine2_vpn1"].forwarded is False
        assert state.engines["engine1_vpn2"].forwarded is True  # Now forwarded
        assert state.engines["engine2_vpn2"].forwarded is False
        
        # Verify VPN-specific queries
        assert state.has_forwarded_engine_for_vpn("gluetun") is True
        assert state.has_forwarded_engine_for_vpn("gluetun_2") is True
        
        # Get forwarded engines for each VPN
        vpn1_forwarded = state.get_forwarded_engine_for_vpn("gluetun")
        vpn2_forwarded = state.get_forwarded_engine_for_vpn("gluetun_2")
        
        assert vpn1_forwarded is not None
        assert vpn1_forwarded.container_id == "engine1_vpn1"
        assert vpn2_forwarded is not None
        assert vpn2_forwarded.container_id == "engine1_vpn2"


def test_redundant_mode_change_forwarded_engine_per_vpn():
    """Test that changing forwarded engine only affects the same VPN."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'), \
         mock.patch('app.core.config.cfg') as mock_cfg:
        # Configure redundant mode
        mock_cfg.VPN_MODE = 'redundant'
        mock_cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
        mock_cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun_2'
        
        state = State()
        state.clear_state()
        
        # Add engines for each VPN
        engine1_vpn1 = EngineState(
            container_id="engine1_vpn1",
            container_name="engine-1-vpn1",
            host="gluetun",
            port=19000,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2_vpn1 = EngineState(
            container_id="engine2_vpn1",
            container_name="engine-2-vpn1",
            host="gluetun",
            port=19001,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine1_vpn2 = EngineState(
            container_id="engine1_vpn2",
            container_name="engine-1-vpn2",
            host="gluetun_2",
            port=19002,
            forwarded=False,
            vpn_container="gluetun_2",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1_vpn1"] = engine1_vpn1
        state.engines["engine2_vpn1"] = engine2_vpn1
        state.engines["engine1_vpn2"] = engine1_vpn2
        
        # Set forwarded engines for both VPNs
        state.set_forwarded_engine("engine1_vpn1")
        state.set_forwarded_engine("engine1_vpn2")
        
        # Both should be forwarded
        assert state.engines["engine1_vpn1"].forwarded is True
        assert state.engines["engine1_vpn2"].forwarded is True
        
        # Now change VPN1's forwarded engine to engine2_vpn1
        state.set_forwarded_engine("engine2_vpn1")
        
        # VPN1 should now have engine2_vpn1 as forwarded
        # VPN2 should still have engine1_vpn2 as forwarded
        assert state.engines["engine1_vpn1"].forwarded is False
        assert state.engines["engine2_vpn1"].forwarded is True
        assert state.engines["engine1_vpn2"].forwarded is True  # Should NOT be affected


def test_single_mode_clears_all_forwarded():
    """Test that single mode behavior still works (clears all forwarded engines)."""
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'), \
         mock.patch('app.core.config.cfg') as mock_cfg:
        # Configure single mode
        mock_cfg.VPN_MODE = 'single'
        mock_cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
        mock_cfg.GLUETUN_CONTAINER_NAME_2 = None
        
        state = State()
        state.clear_state()
        
        # Add engines
        engine1 = EngineState(
            container_id="engine1",
            container_name="engine-1",
            host="gluetun",
            port=19000,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2 = EngineState(
            container_id="engine2",
            container_name="engine-2",
            host="gluetun",
            port=19001,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1"] = engine1
        state.engines["engine2"] = engine2
        
        # Set engine1 as forwarded
        state.set_forwarded_engine("engine1")
        assert state.engines["engine1"].forwarded is True
        assert state.engines["engine2"].forwarded is False
        
        # Set engine2 as forwarded - should clear engine1
        state.set_forwarded_engine("engine2")
        assert state.engines["engine1"].forwarded is False
        assert state.engines["engine2"].forwarded is True


def test_redundant_mode_remove_forwarded_engine_clears_state():
    """Test that removing a forwarded engine clears it from state.
    
    Note: The autoscaler will automatically provision a new engine to maintain
    MIN_REPLICAS. That new engine will become the forwarded engine since none
    will exist for that VPN after removal.
    """
    import unittest.mock as mock
    
    with mock.patch('app.services.state.SessionLocal'), \
         mock.patch('app.core.config.cfg') as mock_cfg:
        # Configure redundant mode
        mock_cfg.VPN_MODE = 'redundant'
        mock_cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
        mock_cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun_2'
        
        state = State()
        state.clear_state()
        
        # Add engines for both VPNs
        engine1_vpn1 = EngineState(
            container_id="engine1_vpn1",
            container_name="engine-1-vpn1",
            host="gluetun",
            port=19000,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine2_vpn1 = EngineState(
            container_id="engine2_vpn1",
            container_name="engine-2-vpn1",
            host="gluetun",
            port=19001,
            forwarded=False,
            vpn_container="gluetun",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        engine1_vpn2 = EngineState(
            container_id="engine1_vpn2",
            container_name="engine-1-vpn2",
            host="gluetun_2",
            port=19002,
            forwarded=False,
            vpn_container="gluetun_2",
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        state.engines["engine1_vpn1"] = engine1_vpn1
        state.engines["engine2_vpn1"] = engine2_vpn1
        state.engines["engine1_vpn2"] = engine1_vpn2
        
        # Set forwarded engines for both VPNs
        state.set_forwarded_engine("engine1_vpn1")
        state.set_forwarded_engine("engine1_vpn2")
        
        # Both should be forwarded
        assert state.engines["engine1_vpn1"].forwarded is True
        assert state.engines["engine1_vpn2"].forwarded is True
        
        # Remove VPN1's forwarded engine
        state.remove_engine("engine1_vpn1")
        
        # Engine should be removed from state
        assert "engine1_vpn1" not in state.engines  # Removed
        
        # VPN1 should no longer have a forwarded engine
        # (autoscaler will provision a new one that becomes forwarded)
        assert state.has_forwarded_engine_for_vpn("gluetun") is False
        
        # VPN2 should still have its forwarded engine unchanged
        assert state.engines["engine2_vpn1"].forwarded is False  # Not promoted
        assert state.engines["engine1_vpn2"].forwarded is True  # Unchanged
        assert state.has_forwarded_engine_for_vpn("gluetun_2") is True


if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v"])
    else:
        # Run tests manually without pytest
        print("\n" + "=" * 60)
        print("Running tests without pytest")
        print("=" * 60)
        
        try:
            test_redundant_mode_multiple_forwarded_engines()
            print("✅ test_redundant_mode_multiple_forwarded_engines passed")
            
            test_redundant_mode_change_forwarded_engine_per_vpn()
            print("✅ test_redundant_mode_change_forwarded_engine_per_vpn passed")
            
            test_single_mode_clears_all_forwarded()
            print("✅ test_single_mode_clears_all_forwarded passed")
            
            test_redundant_mode_remove_forwarded_engine_clears_state()
            print("✅ test_redundant_mode_remove_forwarded_engine_clears_state passed")
            
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
