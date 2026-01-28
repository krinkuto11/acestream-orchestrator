"""
Test per-VPN stabilization period implementation.

This tests the fix for the issue where stabilization period blocked ALL provisioning
when ANY VPN was stabilizing. Now it should only block provisioning on the specific
VPN that is stabilizing.

Scenario:
1. VPN1 fails and recovers -> enters 120s stabilization
2. VPN2 is healthy and stable
3. System should be able to provision engines on VPN2 while VPN1 is stabilizing
4. VPN2 fails and recovers -> enters 120s stabilization
5. VPN1's stabilization ends, system should provision on VPN1 while VPN2 is stabilizing
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta


@pytest.fixture
def mock_state():
    """Mock state with engine count methods."""
    with patch('app.services.health_manager.state') as state:
        state.get_vpn_recovery_target.return_value = None
        state.is_emergency_mode.return_value = False
        state.get_engines_by_vpn.return_value = []
        yield state


@pytest.fixture
def mock_gluetun_monitor():
    """Mock gluetun monitor with VPN monitors."""
    with patch('app.services.gluetun.gluetun_monitor') as monitor:
        vpn1_monitor = MagicMock()
        vpn2_monitor = MagicMock()
        
        # Default: both healthy, not in stabilization
        vpn1_monitor.is_in_recovery_stabilization_period.return_value = False
        vpn2_monitor.is_in_recovery_stabilization_period.return_value = False
        monitor.is_healthy.side_effect = lambda vpn: True
        
        monitor.get_vpn_monitor.side_effect = lambda vpn: (
            vpn1_monitor if vpn == 'gluetun' else vpn2_monitor
        )
        
        yield monitor, vpn1_monitor, vpn2_monitor


def test_per_vpn_stabilization_vpn1_stabilizing(mock_state, mock_gluetun_monitor):
    """
    Test that when VPN1 is stabilizing, VPN2 can still receive engines.
    
    Setup:
    - VPN1: healthy, in stabilization period
    - VPN2: healthy, not in stabilization
    - Both have 0 engines (balanced)
    
    Expected: VPN2 should be selected as target, provisioning should NOT wait
    """
    from app.services.health_manager import HealthManager
    from app.core.config import cfg
    
    # Configure redundant mode
    with patch.object(cfg, 'VPN_MODE', 'redundant'), \
         patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'), \
         patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun2'), \
         patch.object(cfg, 'MIN_REPLICAS', 2):
        
        monitor, vpn1_monitor, vpn2_monitor = mock_gluetun_monitor
        
        # VPN1 is stabilizing, VPN2 is not
        vpn1_monitor.is_in_recovery_stabilization_period.return_value = True
        vpn2_monitor.is_in_recovery_stabilization_period.return_value = False
        
        # Both VPNs healthy
        monitor.is_healthy.side_effect = lambda vpn: True
        
        # Both have equal engines (will select VPN1 by default, but VPN1 is stabilizing)
        mock_state.get_engines_by_vpn.side_effect = lambda vpn: [] if vpn == 'gluetun' else []
        
        health_manager = HealthManager()
        
        # Get target VPN - should select VPN2 because both have equal engines
        target_vpn = health_manager._get_target_vpn_for_provisioning()
        
        # With both healthy and equal engines, round-robin selects VPN1
        # But VPN1 is stabilizing, so we check that case
        healthy_engines = []
        
        # If target would be VPN1, should wait
        if target_vpn == 'gluetun':
            should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
            assert should_wait, "Should wait when target VPN1 is in stabilization"
            print("✓ Correctly blocks provisioning when target VPN (VPN1) is in stabilization")
        
        # Now simulate VPN2 having fewer engines, making it the target
        mock_state.get_engines_by_vpn.side_effect = lambda vpn: [1] if vpn == 'gluetun' else []
        
        target_vpn = health_manager._get_target_vpn_for_provisioning()
        assert target_vpn == 'gluetun2', "VPN2 should be selected when it has fewer engines"
        
        should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
        assert not should_wait, "Should NOT wait when target VPN2 is NOT in stabilization"
        print("✓ Correctly allows provisioning when target VPN (VPN2) is NOT in stabilization")


def test_per_vpn_stabilization_vpn2_stabilizing(mock_state, mock_gluetun_monitor):
    """
    Test that when VPN2 is stabilizing, VPN1 can still receive engines.
    
    Setup:
    - VPN1: healthy, not in stabilization
    - VPN2: healthy, in stabilization period
    - VPN1 has fewer engines
    
    Expected: VPN1 should be selected as target, provisioning should NOT wait
    """
    from app.services.health_manager import HealthManager
    from app.core.config import cfg
    
    with patch.object(cfg, 'VPN_MODE', 'redundant'), \
         patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'), \
         patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun2'), \
         patch.object(cfg, 'MIN_REPLICAS', 2):
        
        monitor, vpn1_monitor, vpn2_monitor = mock_gluetun_monitor
        
        # VPN1 is NOT stabilizing, VPN2 is stabilizing
        vpn1_monitor.is_in_recovery_stabilization_period.return_value = False
        vpn2_monitor.is_in_recovery_stabilization_period.return_value = True
        
        # Both VPNs healthy
        monitor.is_healthy.side_effect = lambda vpn: True
        
        # VPN1 has fewer engines, so it will be selected
        mock_state.get_engines_by_vpn.side_effect = lambda vpn: [] if vpn == 'gluetun' else [1]
        
        health_manager = HealthManager()
        
        # Get target VPN - should select VPN1 (fewer engines)
        target_vpn = health_manager._get_target_vpn_for_provisioning()
        assert target_vpn == 'gluetun', "VPN1 should be selected when it has fewer engines"
        
        # Should NOT wait because target VPN1 is not in stabilization
        healthy_engines = []
        should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
        assert not should_wait, "Should NOT wait when target VPN1 is NOT in stabilization"
        print("✓ Correctly allows provisioning on VPN1 when VPN2 is stabilizing")


def test_per_vpn_stabilization_both_stabilizing(mock_state, mock_gluetun_monitor):
    """
    Test that when both VPNs are stabilizing, provisioning is blocked.
    
    This is a safety check - if both VPNs just recovered, we should wait.
    """
    from app.services.health_manager import HealthManager
    from app.core.config import cfg
    
    with patch.object(cfg, 'VPN_MODE', 'redundant'), \
         patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'), \
         patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun2'), \
         patch.object(cfg, 'MIN_REPLICAS', 2):
        
        monitor, vpn1_monitor, vpn2_monitor = mock_gluetun_monitor
        
        # Both VPNs in stabilization
        vpn1_monitor.is_in_recovery_stabilization_period.return_value = True
        vpn2_monitor.is_in_recovery_stabilization_period.return_value = True
        
        # Both VPNs healthy
        monitor.is_healthy.side_effect = lambda vpn: True
        
        # Equal engines
        mock_state.get_engines_by_vpn.side_effect = lambda vpn: []
        
        health_manager = HealthManager()
        
        # Get target VPN - could be either
        target_vpn = health_manager._get_target_vpn_for_provisioning()
        
        # Should wait because the target VPN (whichever it is) is in stabilization
        healthy_engines = []
        should_wait = health_manager._should_wait_for_vpn_recovery(healthy_engines)
        assert should_wait, "Should wait when target VPN is in stabilization"
        print("✓ Correctly blocks provisioning when both VPNs are stabilizing")


def test_per_vpn_stabilization_target_selection():
    """
    Test that target VPN selection logic matches provisioner logic.
    
    This verifies the _get_target_vpn_for_provisioning helper replicates
    the provisioner's VPN selection accurately.
    """
    from app.services.health_manager import HealthManager
    from app.core.config import cfg
    
    with patch('app.services.health_manager.state') as state, \
         patch('app.services.gluetun.gluetun_monitor') as monitor:
        
        with patch.object(cfg, 'VPN_MODE', 'redundant'), \
             patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'), \
             patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun2'):
            
            health_manager = HealthManager()
            
            # Test 1: Recovery mode should return recovery target
            state.get_vpn_recovery_target.return_value = 'gluetun2'
            target = health_manager._get_target_vpn_for_provisioning()
            assert target == 'gluetun2', "Should select recovery target VPN"
            print("✓ Correctly selects recovery target VPN")
            
            # Test 2: Emergency mode should return healthy VPN
            state.get_vpn_recovery_target.return_value = None
            state.is_emergency_mode.return_value = True
            state.get_emergency_mode_info.return_value = {'healthy_vpn': 'gluetun'}
            target = health_manager._get_target_vpn_for_provisioning()
            assert target == 'gluetun', "Should select emergency mode healthy VPN"
            print("✓ Correctly selects emergency mode healthy VPN")
            
            # Test 3: Normal mode with both healthy - round robin based on count
            state.get_vpn_recovery_target.return_value = None
            state.is_emergency_mode.return_value = False
            monitor.is_healthy.side_effect = lambda vpn: True
            
            # VPN1 has fewer engines
            state.get_engines_by_vpn.side_effect = lambda vpn: [] if vpn == 'gluetun' else [1]
            target = health_manager._get_target_vpn_for_provisioning()
            assert target == 'gluetun', "Should select VPN with fewer engines (VPN1)"
            print("✓ Correctly selects VPN with fewer engines")
            
            # VPN2 has fewer engines
            state.get_engines_by_vpn.side_effect = lambda vpn: [1] if vpn == 'gluetun' else []
            target = health_manager._get_target_vpn_for_provisioning()
            assert target == 'gluetun2', "Should select VPN with fewer engines (VPN2)"
            
            # Test 4: Only VPN1 healthy
            monitor.is_healthy.side_effect = lambda vpn: vpn == 'gluetun'
            target = health_manager._get_target_vpn_for_provisioning()
            assert target == 'gluetun', "Should select only healthy VPN (VPN1)"
            print("✓ Correctly selects only healthy VPN")
            
            # Test 5: Only VPN2 healthy
            monitor.is_healthy.side_effect = lambda vpn: vpn == 'gluetun2'
            target = health_manager._get_target_vpn_for_provisioning()
            assert target == 'gluetun2', "Should select only healthy VPN (VPN2)"
            
            # Test 6: Both unhealthy
            monitor.is_healthy.side_effect = lambda vpn: False
            target = health_manager._get_target_vpn_for_provisioning()
            assert target is None, "Should return None when both VPNs unhealthy"
            print("✓ Correctly returns None when both VPNs unhealthy")


if __name__ == '__main__':
    print("\n" + "="*80)
    print("Testing Per-VPN Stabilization Period")
    print("="*80)
    
    # Run tests manually
    print("\nTest 1: Target VPN selection logic")
    test_per_vpn_stabilization_target_selection()
    
    print("\nTest 2: VPN1 stabilizing, VPN2 can receive engines")
    test_per_vpn_stabilization_vpn1_stabilizing(
        MagicMock(), 
        (MagicMock(), MagicMock(), MagicMock())
    )
    
    print("\nTest 3: VPN2 stabilizing, VPN1 can receive engines")
    test_per_vpn_stabilization_vpn2_stabilizing(
        MagicMock(),
        (MagicMock(), MagicMock(), MagicMock())
    )
    
    print("\nTest 4: Both VPNs stabilizing, provisioning blocked")
    test_per_vpn_stabilization_both_stabilizing(
        MagicMock(),
        (MagicMock(), MagicMock(), MagicMock())
    )
    
    print("\n" + "="*80)
    print("All tests completed!")
    print("="*80)
