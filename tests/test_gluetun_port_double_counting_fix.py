#!/usr/bin/env python3
"""
Test to verify that Gluetun ports are not double-counted during reindex and release.
This was causing MAX_ACTIVE_REPLICAS to be reached prematurely.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_gluetun_port_single_counting():
    """Test that only one Gluetun port is reserved per container."""
    
    print("\n🧪 Testing Gluetun port single counting...")
    
    try:
        from app.services.ports import PortAllocator
        from app.core.config import cfg
        
        # Create a fresh port allocator
        alloc = PortAllocator()
        
        # Simulate Gluetun being configured
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        try:
            # Allocate ports for a container
            port1 = alloc.alloc_gluetun_port()
            print(f"✅ Allocated port {port1}")
            
            # Check that only one port is in use
            assert len(alloc._used_gluetun_ports) == 1, f"Expected 1 port in use, got {len(alloc._used_gluetun_ports)}"
            print(f"✅ Confirmed: Only 1 port in use after allocation")
            
            # Simulate reindexing behavior - should only reserve once per container
            # In the old code, this would reserve twice (HOST_LABEL_HTTP and ACESTREAM_LABEL_HTTP)
            # In the new code, it should only reserve once
            
            # Allocate a second port for a second container
            port2 = alloc.alloc_gluetun_port()
            print(f"✅ Allocated port {port2} for second container")
            
            # Should now have 2 ports in use (not 4)
            assert len(alloc._used_gluetun_ports) == 2, f"Expected 2 ports in use, got {len(alloc._used_gluetun_ports)}"
            print(f"✅ Confirmed: Only 2 ports in use for 2 containers")
            
            # Test that we can allocate up to MAX_ACTIVE_REPLICAS
            max_active = cfg.MAX_ACTIVE_REPLICAS
            print(f"\n🧪 Testing allocation up to MAX_ACTIVE_REPLICAS={max_active}...")
            
            # We've already allocated 2, so allocate MAX_ACTIVE_REPLICAS - 2 more
            for i in range(max_active - 2):
                try:
                    port = alloc.alloc_gluetun_port()
                    if (i + 3) % 5 == 0:  # Print every 5th allocation
                        print(f"  Allocated port {port} (container {i + 3}/{max_active})")
                except Exception as e:
                    print(f"❌ Failed to allocate port for container {i + 3}: {e}")
                    raise
            
            print(f"✅ Successfully allocated {max_active} ports (one per container)")
            assert len(alloc._used_gluetun_ports) == max_active
            
            # Now try to allocate one more - should fail
            print(f"\n🧪 Testing that allocation fails after reaching MAX_ACTIVE_REPLICAS...")
            try:
                alloc.alloc_gluetun_port()
                print(f"❌ ERROR: Allocated port beyond MAX_ACTIVE_REPLICAS!")
                return False
            except RuntimeError as e:
                if "Maximum active replicas limit reached" in str(e):
                    print(f"✅ Correctly rejected allocation at limit: {e}")
                else:
                    print(f"❌ Wrong error message: {e}")
                    return False
            
            # Test port release
            print(f"\n🧪 Testing port release...")
            alloc.free_gluetun_port(port1)
            assert len(alloc._used_gluetun_ports) == max_active - 1
            print(f"✅ Port released correctly, now have {len(alloc._used_gluetun_ports)} ports in use")
            
            # Should be able to allocate one more now
            port_new = alloc.alloc_gluetun_port()
            print(f"✅ Successfully allocated port {port_new} after release")
            
            print("\n🎯 All Gluetun port counting tests PASSED")
            return True
            
        finally:
            cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_reindex_single_reservation():
    """Test that reindex only reserves one port per container when using Gluetun."""
    
    print("\n🧪 Testing reindex single port reservation...")
    
    try:
        from app.services import reindex as reindex_module
        from app.services import ports as ports_module
        from app.core.config import cfg
        from app.services.health import list_managed
        from app.services import health
        
        # Get the actual allocator used by reindex
        alloc = ports_module.alloc
        
        # Mock Docker container
        class MockContainer:
            def __init__(self, container_id, host_http_port, ace_http_port):
                self.id = container_id
                self.status = "running"
                self.name = f"mock-{container_id}"
                self.labels = {
                    "host.http_port": str(host_http_port),
                    "acestream.http_port": str(ace_http_port)
                }
                self.attrs = {'Created': '2024-01-01'}
        
        # Save original functions
        original_list_managed = health.list_managed
        original_gluetun = cfg.GLUETUN_CONTAINER_NAME
        
        try:
            # Configure Gluetun mode
            cfg.GLUETUN_CONTAINER_NAME = "gluetun"
            
            # Create mock containers
            mock_containers = [
                MockContainer("container1", 19000, 40000),
                MockContainer("container2", 19001, 40001),
            ]
            
            # Mock list_managed in both health and reindex modules
            # (reindex imports list_managed at module level)
            mock_func = lambda: mock_containers
            health.list_managed = mock_func
            reindex_module.list_managed = mock_func
            
            # Clear port allocator state
            alloc._used_gluetun_ports.clear()
            
            # Run reindex
            print("  Running reindex_existing()...")
            print(f"  Before reindex: {len(alloc._used_gluetun_ports)} Gluetun ports in use")
            print(f"  GLUETUN_CONTAINER_NAME={cfg.GLUETUN_CONTAINER_NAME}")
            reindex_module.reindex_existing()
            
            # Check that only 2 ports were reserved (not 4)
            ports_used = len(alloc._used_gluetun_ports)
            print(f"  After reindex: {ports_used} Gluetun ports in use")
            expected_ports = 2  # One per container (HOST_LABEL_HTTP only)
            
            if ports_used == expected_ports:
                print(f"✅ Reindex correctly reserved {ports_used} ports for {len(mock_containers)} containers")
                print(f"   Reserved ports: {sorted(alloc._used_gluetun_ports)}")
                return True
            else:
                print(f"❌ ERROR: Reindex reserved {ports_used} ports, expected {expected_ports}")
                print(f"   This suggests double-counting is still happening!")
                print(f"   Reserved ports: {sorted(alloc._used_gluetun_ports)}")
                return False
                
        finally:
            health.list_managed = original_list_managed
            cfg.GLUETUN_CONTAINER_NAME = original_gluetun
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("🚀 Starting Gluetun port double-counting fix tests...")
    
    success = True
    success &= test_gluetun_port_single_counting()
    success &= test_reindex_single_reservation()
    
    print(f"\n🎯 Overall result: {'PASSED' if success else 'FAILED'}")
    return success

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Tests failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
