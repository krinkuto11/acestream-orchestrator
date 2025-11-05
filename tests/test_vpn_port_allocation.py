"""
Test VPN-specific port allocation in redundant mode.

This test validates that:
1. In redundant mode with VPN-specific port ranges, engines get ports from their assigned VPN's range
2. Port allocation correctly tracks per-VPN ranges
3. Port release works correctly for VPN-specific ranges
"""

import sys
import os
import traceback
from unittest import mock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False


def test_vpn_specific_port_allocation():
    """Test that port allocation respects VPN-specific ranges."""
    from app.services.ports import PortAllocator
    from app.core.config import cfg
    
    # Mock configuration for redundant mode with VPN-specific port ranges
    with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
        with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun_2'):
            with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_1', '19000-19499'):
                with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_2', '19500-19999'):
                    with mock.patch.object(cfg, 'VPN_MODE', 'redundant'):
                        # Create a fresh port allocator
                        alloc = PortAllocator()
                        
                        # Allocate ports for first VPN
                        port1 = alloc.alloc_gluetun_port('gluetun')
                        port2 = alloc.alloc_gluetun_port('gluetun')
                        port3 = alloc.alloc_gluetun_port('gluetun')
                        
                        # Verify ports are in the first VPN's range
                        assert 19000 <= port1 < 19500, f"Port {port1} not in range 19000-19499"
                        assert 19000 <= port2 < 19500, f"Port {port2} not in range 19000-19499"
                        assert 19000 <= port3 < 19500, f"Port {port3} not in range 19000-19499"
                        
                        # Allocate ports for second VPN
                        port4 = alloc.alloc_gluetun_port('gluetun_2')
                        port5 = alloc.alloc_gluetun_port('gluetun_2')
                        port6 = alloc.alloc_gluetun_port('gluetun_2')
                        
                        # Verify ports are in the second VPN's range
                        assert 19500 <= port4 < 20000, f"Port {port4} not in range 19500-19999"
                        assert 19500 <= port5 < 20000, f"Port {port5} not in range 19500-19999"
                        assert 19500 <= port6 < 20000, f"Port {port6} not in range 19500-19999"
                        
                        # Verify ports are sequential within each VPN
                        assert port1 == 19000
                        assert port2 == 19001
                        assert port3 == 19002
                        assert port4 == 19500
                        assert port5 == 19501
                        assert port6 == 19502


def test_vpn_port_reserve_and_free():
    """Test that port reservation and freeing works correctly for VPN-specific ranges."""
    from app.services.ports import PortAllocator
    from app.core.config import cfg
    
    with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
        with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun_2'):
            with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_1', '19000-19499'):
                with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_2', '19500-19999'):
                    with mock.patch.object(cfg, 'VPN_MODE', 'redundant'):
                        alloc = PortAllocator()
                        
                        # Reserve a port in first VPN's range
                        alloc.reserve_gluetun_port(19000, 'gluetun')
                        
                        # Next allocation should skip the reserved port
                        port1 = alloc.alloc_gluetun_port('gluetun')
                        assert port1 == 19001
                        
                        # Free the reserved port
                        alloc.free_gluetun_port(19000, 'gluetun')
                        alloc.free_gluetun_port(19001, 'gluetun')
                        
                        # Allocate again - should get next available port (19002 since next_port is at 19002)
                        port2 = alloc.alloc_gluetun_port('gluetun')
                        assert port2 == 19002
                        
                        # Allocate another - should wrap around and get 19000 now
                        port3 = alloc.alloc_gluetun_port('gluetun')
                        assert port3 == 19003
                        
                        # Continue allocating - will eventually wrap and use freed ports
                        port4 = alloc.alloc_gluetun_port('gluetun')
                        assert port4 == 19004


def test_vpn_port_allocation_without_ranges():
    """Test that port allocation falls back to global range when VPN-specific ranges are not configured."""
    from app.services.ports import PortAllocator
    from app.core.config import cfg
    
    with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
        with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', None):
            with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_1', None):
                with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_2', None):
                    with mock.patch.object(cfg, 'MAX_ACTIVE_REPLICAS', 20):
                        alloc = PortAllocator()
                        
                        # Allocate without specifying VPN - should use global allocation
                        port1 = alloc.alloc_gluetun_port()
                        port2 = alloc.alloc_gluetun_port()
                        
                        # Verify ports are sequential starting from 19000
                        assert port1 == 19000
                        assert port2 == 19001


def test_mixed_vpn_port_allocation():
    """Test that engines can be allocated alternately to different VPNs with correct port ranges."""
    from app.services.ports import PortAllocator
    from app.core.config import cfg
    
    with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
        with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun_2'):
            with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_1', '19000-19499'):
                with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_2', '19500-19999'):
                    with mock.patch.object(cfg, 'VPN_MODE', 'redundant'):
                        alloc = PortAllocator()
                        
                        # Allocate ports alternately between VPNs
                        vpn1_port1 = alloc.alloc_gluetun_port('gluetun')
                        vpn2_port1 = alloc.alloc_gluetun_port('gluetun_2')
                        vpn1_port2 = alloc.alloc_gluetun_port('gluetun')
                        vpn2_port2 = alloc.alloc_gluetun_port('gluetun_2')
                        
                        # Verify ports are in correct ranges
                        assert 19000 <= vpn1_port1 < 19500
                        assert 19500 <= vpn2_port1 < 20000
                        assert 19000 <= vpn1_port2 < 19500
                        assert 19500 <= vpn2_port2 < 20000
                        
                        # Verify sequential within each VPN
                        assert vpn1_port1 == 19000
                        assert vpn1_port2 == 19001
                        assert vpn2_port1 == 19500
                        assert vpn2_port2 == 19501


def test_clear_vpn_specific_allocations():
    """Test that clearing allocations works correctly for VPN-specific ranges."""
    from app.services.ports import PortAllocator
    from app.core.config import cfg
    
    with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME', 'gluetun'):
        with mock.patch.object(cfg, 'GLUETUN_CONTAINER_NAME_2', 'gluetun_2'):
            with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_1', '19000-19499'):
                with mock.patch.object(cfg, 'GLUETUN_PORT_RANGE_2', '19500-19999'):
                    with mock.patch.object(cfg, 'VPN_MODE', 'redundant'):
                        alloc = PortAllocator()
                        
                        # Allocate some ports
                        alloc.alloc_gluetun_port('gluetun')
                        alloc.alloc_gluetun_port('gluetun')
                        alloc.alloc_gluetun_port('gluetun_2')
                        alloc.alloc_gluetun_port('gluetun_2')
                        
                        # Clear all allocations
                        alloc.clear_all_allocations()
                        
                        # Allocate again - should start from beginning
                        port1 = alloc.alloc_gluetun_port('gluetun')
                        port2 = alloc.alloc_gluetun_port('gluetun_2')
                        
                        assert port1 == 19000
                        assert port2 == 19500


if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v"])
    else:
        # Run tests manually without pytest
        print("\n" + "=" * 60)
        print("Running VPN-specific port allocation tests")
        print("=" * 60)
        
        try:
            test_vpn_specific_port_allocation()
            print("✅ test_vpn_specific_port_allocation PASSED")
            
            test_vpn_port_reserve_and_free()
            print("✅ test_vpn_port_reserve_and_free PASSED")
            
            test_vpn_port_allocation_without_ranges()
            print("✅ test_vpn_port_allocation_without_ranges PASSED")
            
            test_mixed_vpn_port_allocation()
            print("✅ test_mixed_vpn_port_allocation PASSED")
            
            test_clear_vpn_specific_allocations()
            print("✅ test_clear_vpn_specific_allocations PASSED")
            
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
