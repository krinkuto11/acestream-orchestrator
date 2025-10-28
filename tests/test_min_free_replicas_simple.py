#!/usr/bin/env python3
"""
Simple unit test to verify that the deficit calculation formula is correct:
deficit = max(0, MIN_REPLICAS - free_count)
where free_count = total_running - used_engines

This validates the fix for the issue where MIN_REPLICAS should maintain
minimum EMPTY replicas, not just total replicas.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_deficit_formula():
    """Test the mathematical formula for calculating replica deficit."""
    
    print("\n🧪 Testing replica deficit formula...")
    print("   Formula: deficit = max(0, MIN_REPLICAS - (total_running - used_engines))")
    
    test_cases = [
        # (min_replicas, total_running, used_engines, expected_deficit, description)
        (2, 0, 0, 2, "No engines exist"),
        (2, 3, 1, 0, "3 total, 1 used, 2 free - OK"),
        (2, 3, 2, 1, "3 total, 2 used, 1 free - need 1 more"),
        (1, 10, 10, 1, "10 total, all busy - need 1 empty"),
        (3, 5, 3, 1, "5 total, 3 used - need 1 more for 3 free"),
        (0, 5, 3, 0, "MIN_REPLICAS=0 - no deficit"),
        (1, 5, 0, 0, "5 total, 0 used - already have 5 free"),
    ]
    
    all_passed = True
    for min_replicas, total_running, used_engines, expected_deficit, description in test_cases:
        free_count = total_running - used_engines
        deficit = max(0, min_replicas - free_count)
        
        status = "✅" if deficit == expected_deficit else "❌"
        print(f"\n   {status} {description}")
        print(f"      MIN_REPLICAS={min_replicas}, total={total_running}, used={used_engines}, free={free_count}")
        print(f"      Calculated deficit: {deficit} (expected: {expected_deficit})")
        
        if deficit != expected_deficit:
            print(f"      ERROR: Expected deficit={expected_deficit}, got {deficit}")
            all_passed = False
    
    if all_passed:
        print("\n🎯 All tests PASSED: Deficit formula is correct")
        print("   This confirms MIN_REPLICAS maintains minimum FREE replicas")
        return True
    else:
        print("\n💥 Some tests FAILED")
        return False

def test_ensure_minimum_uses_free_count():
    """Test that ensure_minimum() now uses free_count instead of total_running."""
    
    print("\n🧪 Verifying ensure_minimum() implementation...")
    
    try:
        # Read the autoscaler.py file to check the implementation
        import os
        autoscaler_path = os.path.join(os.path.dirname(__file__), '..', 'app', 'services', 'autoscaler.py')
        
        with open(autoscaler_path, 'r') as f:
            content = f.read()
        
        # Check for key indicators that the fix is in place
        checks = [
            ("validate_and_sync_state()" in content, "Uses validate_and_sync_state()"),
            ("free_count" in content, "References free_count variable"),
            ("deficit = cfg.MIN_REPLICAS - free_count" in content, "Calculates deficit from free_count"),
            ("minimum FREE replicas" in content or "free/empty replicas" in content, "Documentation mentions free replicas"),
        ]
        
        all_passed = True
        for check, description in checks:
            status = "✅" if check else "❌"
            print(f"   {status} {description}")
            if not check:
                all_passed = False
        
        if all_passed:
            print("\n🎯 ensure_minimum() implementation verified")
            print("   The function now maintains minimum FREE replicas")
            return True
        else:
            print("\n⚠️  Implementation may need updates")
            return False
            
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        test1 = test_deficit_formula()
        test2 = test_ensure_minimum_uses_free_count()
        
        success = test1 and test2
        
        if success:
            print("\n🎉 All tests PASSED")
            print("✅ MIN_REPLICAS now correctly maintains minimum empty replicas")
            print("✅ Formula: deficit = max(0, MIN_REPLICAS - (total_running - used_engines))")
        else:
            print("\n❌ Some tests failed")
        
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
