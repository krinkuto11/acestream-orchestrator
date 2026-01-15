"""
Test concurrent provisioning in redundant VPN mode to ensure balanced distribution.

This test validates that when multiple engines are provisioned concurrently,
they are distributed evenly across both VPNs rather than all being assigned
to the same VPN due to race conditions.
"""

import sys
import os
import threading
import time
from collections import Counter

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False


def test_concurrent_provisioning_balances_vpns():
    """Test that concurrent provisioning distributes engines evenly across VPNs."""
    import unittest.mock as mock
    from datetime import datetime, timezone
    from app.services.provisioner import start_acestream, AceProvisionRequest
    from app.services.state import state
    from app.models.schemas import EngineState
    
    # Mock configuration
    with mock.patch('app.core.config.cfg') as mock_cfg, \
         mock.patch('app.services.provisioner.get_client'), \
         mock.patch('app.services.provisioner.safe'), \
         mock.patch('app.services.provisioner._check_gluetun_health_sync', return_value=True), \
         mock.patch('app.services.gluetun.gluetun_monitor') as mock_monitor, \
         mock.patch('app.services.state.SessionLocal'):
        
        # Configure redundant mode
        mock_cfg.VPN_MODE = 'redundant'
        mock_cfg.GLUETUN_CONTAINER_NAME = 'gluetun'
        mock_cfg.GLUETUN_CONTAINER_NAME_2 = 'gluetun_2'
        mock_cfg.GLUETUN_PORT_RANGE_1 = '19000-19499'
        mock_cfg.GLUETUN_PORT_RANGE_2 = '19500-19999'
        mock_cfg.STARTUP_TIMEOUT_S = 25
        mock_cfg.ENGINE_VARIANT = 'krinkuto11-amd64'
        mock_cfg.CONTAINER_LABEL = 'test=value'
        mock_cfg.ACE_HTTP_RANGE = '40000-44999'
        mock_cfg.ACE_HTTPS_RANGE = '45000-49999'
        mock_cfg.MAX_REPLICAS = 20
        mock_cfg.MIN_REPLICAS = 2
        
        # Mock VPN monitor to report both VPNs as healthy
        mock_monitor.is_healthy.return_value = True
        
        # Clear state
        state.clear_state()
        
        # Track which VPN each engine was assigned to
        vpn_assignments = []
        assignment_lock = threading.Lock()
        
        # Mock container creation to track VPN assignments
        mock_container_id = [0]  # Use list to allow modification in nested function
        
        def mock_container_run(*args, **kwargs):
            # Extract VPN assignment from network_mode
            network_mode = kwargs.get('network_mode', '')
            if 'container:gluetun_2' in network_mode:
                vpn = 'gluetun_2'
            elif 'container:gluetun' in network_mode:
                vpn = 'gluetun'
            else:
                vpn = 'unknown'
            
            with assignment_lock:
                vpn_assignments.append(vpn)
                container_id = f"container_{mock_container_id[0]}"
                mock_container_id[0] += 1
            
            # Create a mock container
            mock_container = mock.MagicMock()
            mock_container.id = container_id
            mock_container.status = 'running'
            mock_container.attrs = {'Name': f'/engine-{container_id}'}
            
            return mock_container
        
        # Patch safe to use our mock
        with mock.patch('app.services.provisioner.safe', side_effect=mock_container_run):
            # Simulate concurrent provisioning - 10 engines at once
            threads = []
            results = []
            errors = []
            
            def provision_engine(index):
                try:
                    req = AceProvisionRequest(labels={}, env={})
                    response = start_acestream(req)
                    results.append(response)
                except Exception as e:
                    errors.append((index, str(e)))
            
            # Start 10 concurrent provisioning threads
            for i in range(10):
                thread = threading.Thread(target=provision_engine, args=(i,))
                threads.append(thread)
                thread.start()
            
            # Wait for all threads to complete
            for thread in threads:
                thread.join(timeout=10)
            
            # Check results
            assert len(errors) == 0, f"Provisioning errors: {errors}"
            assert len(results) == 10, f"Expected 10 engines, got {len(results)}"
            
            # Count VPN assignments
            vpn_counts = Counter(vpn_assignments)
            
            print(f"\nVPN assignments: {dict(vpn_counts)}")
            print(f"VPN1 (gluetun): {vpn_counts['gluetun']} engines")
            print(f"VPN2 (gluetun_2): {vpn_counts['gluetun_2']} engines")
            
            # Verify balanced distribution across VPNs
            # With the fix, concurrent provisioning should distribute engines evenly
            # We expect roughly balanced distribution (5/5 or close to it, like 6/4 or 7/3)
            # but not extreme imbalance (like 10/0 which would indicate the race condition)
            max_imbalance = 3  # Maximum difference between VPN assignments
            difference = abs(vpn_counts['gluetun'] - vpn_counts['gluetun_2'])
            
            assert difference <= max_imbalance, \
                f"VPN distribution too imbalanced: {vpn_counts['gluetun']} vs {vpn_counts['gluetun_2']} " \
                f"(difference: {difference}, max allowed: {max_imbalance})"


if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v", "-s"])
    else:
        # Run manually without pytest
        print("Running test_concurrent_provisioning_balances_vpns...")
        try:
            test_concurrent_provisioning_balances_vpns()
            print("✓ Test passed")
        except AssertionError as e:
            print(f"✗ Test failed: {e}")
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"✗ Test error: {e}")
            import traceback
            traceback.print_exc()
