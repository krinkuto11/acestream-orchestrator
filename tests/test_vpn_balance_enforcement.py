#!/usr/bin/env python3
"""
Test to verify that the autoscaler maintains balanced VPN distribution in redundant mode.

When MIN_REPLICAS=6 with 2 VPNs (3 engines per VPN), adding a 4th engine to VPN1
should result in the 4th engine from VPN1 being stopped when it becomes empty,
not the 3rd engine from VPN2.
"""

import sys
import os
from unittest.mock import patch, MagicMock

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_vpn_balance_enforcement():
    """Test that autoscaler doesn't stop engines that would unbalance VPN distribution"""
    print("=" * 60)
    print("Test: VPN Balance Enforcement in Redundant Mode")
    print("=" * 60)
    
    from app.services.autoscaler import can_stop_engine, _empty_engine_timestamps
    from app.services.state import state
    from app.models.schemas import EngineState
    from app.core.config import cfg
    from datetime import datetime, timezone, timedelta
    
    # Clear state and grace period tracking
    state.engines.clear()
    state.streams.clear()
    _empty_engine_timestamps.clear()
    
    # Set up redundant VPN mode
    original_vpn_mode = cfg.VPN_MODE
    original_vpn1 = cfg.GLUETUN_CONTAINER_NAME
    original_vpn2 = cfg.GLUETUN_CONTAINER_NAME_2
    original_min_replicas = cfg.MIN_REPLICAS
    original_grace = cfg.ENGINE_GRACE_PERIOD_S
    original_list_managed = None
    
    cfg.VPN_MODE = 'redundant'
    cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
    cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun2'
    cfg.MIN_REPLICAS = 6
    cfg.ENGINE_GRACE_PERIOD_S = 0  # Bypass grace period for testing
    
    try:
        # Mock replica_validator to return counts
        from app.services import replica_validator as rv_module, autoscaler
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
        
        # Get current time for timestamps
        now = datetime.now(timezone.utc)
        
        # Scenario: VPN1 has 4 engines, VPN2 has 3 engines (unbalanced - 7 total)
        # Should be able to stop engine from VPN1 (would bring to 3-3 balance)
        # Should NOT be able to stop engine from VPN2 (would make it 4-2 even more unbalanced)
        
        vpn1_engines = [
            EngineState(
                container_id=f"vpn1_engine_{i}",
                host="127.0.0.1",
                port=6878 + i,
                status="running",
                vpn_container="gluetun",
                forwarded=True,
                first_seen=now,
                last_seen=now
            )
            for i in range(1, 5)  # 4 engines on VPN1
        ]
        
        vpn2_engines = [
            EngineState(
                container_id=f"vpn2_engine_{i}",
                host="127.0.0.1",
                port=6878 + i + 10,
                status="running",
                vpn_container="gluetun2",
                forwarded=True,
                first_seen=now,
                last_seen=now
            )
            for i in range(1, 4)  # 3 engines on VPN2
        ]
        
        all_engines = vpn1_engines + vpn2_engines
        
        # Add engines to state
        for engine in all_engines:
            state.engines[engine.container_id] = engine
        
        # Mock Docker containers
        mock_containers = [
            MockContainer(engine.container_id, "running")
            for engine in all_engines
        ]
        
        original_list_managed = autoscaler.list_managed
        def mock_list_managed():
            return mock_containers
        
        autoscaler.list_managed = mock_list_managed
        rv_module.list_managed = mock_list_managed
        rv_module.replica_validator._cached_result = None
        rv_module.replica_validator._last_validation = None
        
        print(f"\nScenario: VPN1 has 4 engines, VPN2 has 3 engines")
        print(f"Total: 7 engines (MIN_REPLICAS={cfg.MIN_REPLICAS})")
        
        # Test 1: Should be able to stop engine from VPN1 (has more engines)
        print("\n--- Test 1: Can stop engine from VPN1 (has 4 engines) ---")
        rv_module.replica_validator._cached_result = None
        can_stop_vpn1 = can_stop_engine("vpn1_engine_4", bypass_grace_period=True)
        
        if can_stop_vpn1:
            print("✓ Can stop engine from VPN1 (would balance to 3-3)")
        else:
            print("✗ Cannot stop engine from VPN1 (should be allowed)")
            return False
        
        # Test 2: Should NOT be able to stop engine from VPN2 (has fewer engines)
        print("\n--- Test 2: Cannot stop engine from VPN2 (has 3 engines) ---")
        rv_module.replica_validator._cached_result = None
        can_stop_vpn2 = can_stop_engine("vpn2_engine_3", bypass_grace_period=True)
        
        if not can_stop_vpn2:
            print("✓ Cannot stop engine from VPN2 (would unbalance to 4-2)")
        else:
            print("✗ Can stop engine from VPN2 (should be blocked)")
            return False
        
        # Test 3: When balanced above MIN_REPLICAS (4-4), can stop from either VPN
        print("\n--- Test 3: When balanced above MIN_REPLICAS (4-4), can stop from either VPN ---")
        
        # Create balanced 4-4 setup (remove one from VPN1, add one to VPN2)
        vpn2_extra = EngineState(
            container_id="vpn2_engine_4",
            host="127.0.0.1",
            port=6893,
            status="running",
            vpn_container="gluetun2",
            forwarded=True,
            first_seen=now,
            last_seen=now
        )
        
        # VPN1: engines 1,2,3,4 (4 engines)
        # VPN2: engines 1,2,3,4 (4 engines)
        balanced_engines = vpn1_engines + vpn2_engines + [vpn2_extra]
        state.engines = {e.container_id: e for e in balanced_engines}
        mock_containers = [
            MockContainer(e.container_id, "running")
            for e in balanced_engines
        ]
        
        rv_module.replica_validator._cached_result = None
        can_stop_vpn1_balanced = can_stop_engine("vpn1_engine_4", bypass_grace_period=True)
        
        rv_module.replica_validator._cached_result = None
        can_stop_vpn2_balanced = can_stop_engine("vpn2_engine_4", bypass_grace_period=True)
        
        if can_stop_vpn1_balanced and can_stop_vpn2_balanced:
            print("✓ When balanced (4-4), can stop from either VPN")
        else:
            print(f"✗ When balanced: VPN1={can_stop_vpn1_balanced}, VPN2={can_stop_vpn2_balanced} (both should be True)")
            return False
        
        print("\n🎯 All VPN balance tests PASSED!")
        return True
        
    finally:
        # Restore original values
        cfg.VPN_MODE = original_vpn_mode
        cfg.GLUETUN_CONTAINER_NAME = original_vpn1
        cfg.GLUETUN_CONTAINER_NAME_2 = original_vpn2
        cfg.MIN_REPLICAS = original_min_replicas
        cfg.ENGINE_GRACE_PERIOD_S = original_grace
        if original_list_managed:
            autoscaler.list_managed = original_list_managed
        if rv_module:
            rv_module.list_managed = original_list_managed
        state.engines.clear()
        state.streams.clear()
        _empty_engine_timestamps.clear()


def test_vpn_balance_respects_min_replicas():
    """Test that VPN balance check respects MIN_REPLICAS constraint"""
    print("\n" + "=" * 60)
    print("Test: VPN Balance Respects MIN_REPLICAS")
    print("=" * 60)
    
    from app.services.autoscaler import can_stop_engine, _empty_engine_timestamps
    from app.services.state import state
    from app.models.schemas import EngineState
    from app.core.config import cfg
    from datetime import datetime, timezone
    
    # Clear state and grace period tracking
    state.engines.clear()
    state.streams.clear()
    _empty_engine_timestamps.clear()
    
    # Set up redundant VPN mode
    original_vpn_mode = cfg.VPN_MODE
    original_vpn1 = cfg.GLUETUN_CONTAINER_NAME
    original_vpn2 = cfg.GLUETUN_CONTAINER_NAME_2
    original_min_replicas = cfg.MIN_REPLICAS
    original_grace = cfg.ENGINE_GRACE_PERIOD_S
    original_list_managed = None
    
    cfg.VPN_MODE = 'redundant'
    cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
    cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun2'
    cfg.MIN_REPLICAS = 6
    cfg.ENGINE_GRACE_PERIOD_S = 0  # Bypass grace period for testing
    
    try:
        from app.services import replica_validator as rv_module, autoscaler
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
                self.name = f"mock-{container_id}"
                self.labels = {}
        
        # Get current time for timestamps
        now = datetime.now(timezone.utc)
        
        # Scenario: VPN1 has 3 engines, VPN2 has 3 engines (balanced at MIN_REPLICAS=6)
        # Should NOT be able to stop any engine (would violate MIN_REPLICAS)
        
        vpn1_engines = [
            EngineState(
                container_id=f"vpn1_engine_{i}",
                host="127.0.0.1",
                port=6878 + i,
                status="running",
                vpn_container="gluetun",
                forwarded=True,
                first_seen=now,
                last_seen=now
            )
            for i in range(1, 4)  # 3 engines on VPN1
        ]
        
        vpn2_engines = [
            EngineState(
                container_id=f"vpn2_engine_{i}",
                host="127.0.0.1",
                port=6878 + i + 10,
                status="running",
                vpn_container="gluetun2",
                forwarded=True,
                first_seen=now,
                last_seen=now
            )
            for i in range(1, 4)  # 3 engines on VPN2
        ]
        
        all_engines = vpn1_engines + vpn2_engines
        
        # Add engines to state
        for engine in all_engines:
            state.engines[engine.container_id] = engine
        
        # Mock Docker containers
        mock_containers = [
            MockContainer(engine.container_id, "running")
            for engine in all_engines
        ]
        
        original_list_managed = autoscaler.list_managed
        def mock_list_managed():
            return mock_containers
        
        autoscaler.list_managed = mock_list_managed
        rv_module.list_managed = mock_list_managed
        rv_module.replica_validator._cached_result = None
        rv_module.replica_validator._last_validation = None
        
        print(f"\nScenario: VPN1 has 3 engines, VPN2 has 3 engines (at MIN_REPLICAS=6)")
        
        # Should not be able to stop from either VPN (MIN_REPLICAS constraint takes precedence)
        rv_module.replica_validator._cached_result = None
        can_stop_vpn1 = can_stop_engine("vpn1_engine_3", bypass_grace_period=True)
        
        rv_module.replica_validator._cached_result = None
        can_stop_vpn2 = can_stop_engine("vpn2_engine_3", bypass_grace_period=True)
        
        if not can_stop_vpn1 and not can_stop_vpn2:
            print("✓ MIN_REPLICAS constraint takes precedence over VPN balance")
            print("  Cannot stop engines from either VPN when at MIN_REPLICAS limit")
            return True
        else:
            print(f"✗ Should not be able to stop: VPN1={can_stop_vpn1}, VPN2={can_stop_vpn2}")
            print("  MIN_REPLICAS constraint should prevent both")
            return False
        
    finally:
        # Restore original values
        cfg.VPN_MODE = original_vpn_mode
        cfg.GLUETUN_CONTAINER_NAME = original_vpn1
        cfg.GLUETUN_CONTAINER_NAME_2 = original_vpn2
        cfg.MIN_REPLICAS = original_min_replicas
        cfg.ENGINE_GRACE_PERIOD_S = original_grace
        if original_list_managed:
            autoscaler.list_managed = original_list_managed
        if rv_module:
            rv_module.list_managed = original_list_managed
        state.engines.clear()
        state.streams.clear()
        _empty_engine_timestamps.clear()


def main():
    """Run all tests"""
    print("Testing VPN Distribution Balance in Redundant Mode")
    print()
    
    results = []
    
    # Test 1: VPN balance enforcement
    try:
        results.append(("VPN balance enforcement", test_vpn_balance_enforcement()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("VPN balance enforcement", False))
    
    # Test 2: VPN balance respects MIN_REPLICAS
    try:
        results.append(("VPN balance respects MIN_REPLICAS", test_vpn_balance_respects_min_replicas()))
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(("VPN balance respects MIN_REPLICAS", False))
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        symbol = "✓" if passed else "✗"
        print(f"{symbol} {test_name}: {status}")
    
    all_passed = all(passed for _, passed in results)
    print()
    if all_passed:
        print("All tests PASSED! ✓")
        return 0
    else:
        print("Some tests FAILED! ✗")
        return 1


if __name__ == "__main__":
    sys.exit(main())
