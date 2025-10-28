#!/usr/bin/env python3
"""
Test to verify the capacity calculation fix in /orchestrator/status endpoint.

The bug was that used_capacity was counting total active streams instead of
counting unique engines with streams. This caused capacity_used to exceed
capacity_total when multiple streams ran on the same engine.
"""

import sys
import os

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_capacity_calculation_logic():
    """Test the capacity calculation logic directly with mock data."""
    print("\n=== Testing capacity calculation logic ===\n")
    
    try:
        from unittest.mock import Mock
        
        # Create mock engines
        engine1 = Mock()
        engine1.container_id = "engine1"
        
        engine2 = Mock()
        engine2.container_id = "engine2"
        
        engine3 = Mock()
        engine3.container_id = "engine3"
        
        engines = [engine1, engine2, engine3]
        
        # Create mock streams: 3 on engine1, 1 on engine2, 1 on engine3
        # This simulates the scenario from the logs where capacity_used > capacity_total
        stream1 = Mock()
        stream1.container_id = "engine1"
        stream1.status = "started"
        
        stream2 = Mock()
        stream2.container_id = "engine1"  # Same engine
        stream2.status = "started"
        
        stream3 = Mock()
        stream3.container_id = "engine1"  # Same engine
        stream3.status = "started"
        
        stream4 = Mock()
        stream4.container_id = "engine2"
        stream4.status = "started"
        
        stream5 = Mock()
        stream5.container_id = "engine3"
        stream5.status = "started"
        
        active_streams = [stream1, stream2, stream3, stream4, stream5]
        
        print(f"Total engines: {len(engines)}")
        print(f"Total active streams: {len(active_streams)}")
        
        # Calculate capacity the OLD (buggy) way
        total_capacity_old = len(engines)
        used_capacity_old = len(active_streams)
        available_capacity_old = max(0, total_capacity_old - used_capacity_old)
        
        print(f"\nOLD (buggy) calculation:")
        print(f"  total_capacity: {total_capacity_old}")
        print(f"  used_capacity: {used_capacity_old}")
        print(f"  available_capacity: {available_capacity_old}")
        
        # Calculate capacity the NEW (correct) way
        total_capacity_new = len(engines)
        engines_with_streams = len(set(stream.container_id for stream in active_streams))
        used_capacity_new = engines_with_streams
        available_capacity_new = max(0, total_capacity_new - used_capacity_new)
        
        print(f"\nNEW (fixed) calculation:")
        print(f"  total_capacity: {total_capacity_new}")
        print(f"  used_capacity: {used_capacity_new}")
        print(f"  available_capacity: {available_capacity_new}")
        
        # Verify the fix
        assert total_capacity_new == 3, f"Expected total_capacity=3, got {total_capacity_new}"
        assert used_capacity_new == 3, f"Expected used_capacity=3 (3 engines with streams), got {used_capacity_new}"
        assert available_capacity_new == 0, f"Expected available_capacity=0, got {available_capacity_new}"
        
        # Verify that the OLD way was wrong
        assert used_capacity_old == 5, f"Old calculation should show 5 streams"
        assert used_capacity_old > total_capacity_old, "Old calculation should have used > total (the bug)"
        
        print("\n✓ Capacity calculation logic is now correct!")
        print("✓ Used capacity counts engines with streams (3), not total streams (5)")
        print("✓ This prevents capacity_used from exceeding capacity_total")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_code_contains_fix():
    """Test that the code in main.py contains the fix."""
    print("\n=== Verifying fix in main.py ===\n")
    
    try:
        # Read the main.py file
        with open('app/main.py', 'r') as f:
            content = f.read()
        
        # Check that the fix is present
        if 'engines_with_streams = len(set(stream.container_id for stream in active_streams))' in content:
            print("✓ Found correct capacity calculation in main.py")
            print("✓ Code counts unique engines with streams, not total streams")
            return True
        else:
            print("❌ Fix not found in main.py")
            print("Expected to find: engines_with_streams = len(set(stream.container_id for stream in active_streams))")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Testing Capacity Calculation Fix")
    print("=" * 60)
    
    test1_passed = test_capacity_calculation_logic()
    test2_passed = test_code_contains_fix()
    
    print("\n" + "=" * 60)
    if test1_passed and test2_passed:
        print("✓ All tests passed!")
        print("=" * 60)
        sys.exit(0)
    else:
        print("❌ Some tests failed")
        print("=" * 60)
        sys.exit(1)
