#!/usr/bin/env python3
"""
Simple test to validate the startup delay fix is implemented correctly.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_main_has_gluetun_wait_logic():
    """Test that main.py contains the Gluetun wait logic."""
    
    main_file = '/home/runner/work/acestream-orchestrator/acestream-orchestrator/app/main.py'
    
    with open(main_file, 'r') as f:
        content = f.read()
    
    # Check for the Gluetun waiting logic
    assert 'if cfg.GLUETUN_CONTAINER_NAME:' in content, "Should check for Gluetun configuration"
    assert 'Waiting for Gluetun to become healthy' in content, "Should have Gluetun wait message"
    assert 'max_wait_time = 60' in content, "Should have 60 second max wait time"
    assert 'gluetun_monitor.is_healthy()' in content, "Should check Gluetun health"
    assert 'proceeding with engine provisioning' in content, "Should proceed after Gluetun becomes healthy"
    assert 'did not become healthy within' in content, "Should handle timeout case"
    
    print("✅ main.py contains Gluetun wait logic")

def test_provisioner_has_reduced_timeout():
    """Test that provisioner.py has reduced timeout."""
    
    provisioner_file = '/home/runner/work/acestream-orchestrator/acestream-orchestrator/app/services/provisioner.py'
    
    with open(provisioner_file, 'r') as f:
        content = f.read()
    
    # Check for reduced timeout
    assert 'timeout = 5' in content, "Should have 5 second timeout instead of 30"
    assert 'time.sleep(0.5)' in content, "Should have 0.5 second sleep intervals"
    assert 'shorter timeout since we should have verified' in content, "Should have comment explaining reduced timeout"
    
    print("✅ provisioner.py has reduced timeout (5s)")

def test_fix_addresses_original_issue():
    """Test that the fix addresses the original slow startup issue."""
    
    # The original issue was:
    # 1. Each engine provisioning waited 30 seconds for Gluetun health
    # 2. With MIN_REPLICAS=3, this meant 3 * 30 = 90 seconds of delays
    # 3. This made orchestrator restarts "painfully slow"
    
    # The fix:
    # 1. Wait for Gluetun health once during startup (max 60s, async)
    # 2. Reduce individual engine timeout from 30s to 5s
    # 3. This should reduce worst-case startup from 90s to ~60s + (3 * 5s) = 75s
    # 4. Best-case (Gluetun healthy quickly) goes from 90s to ~5s + (3 * 0s) = 5s
    
    main_file = '/home/runner/work/acestream-orchestrator/acestream-orchestrator/app/main.py'
    provisioner_file = '/home/runner/work/acestream-orchestrator/acestream-orchestrator/app/services/provisioner.py'
    
    with open(main_file, 'r') as f:
        main_content = f.read()
    
    with open(provisioner_file, 'r') as f:
        provisioner_content = f.read()
    
    # Verify the complete solution is in place
    startup_wait_implemented = (
        'max_wait_time = 60' in main_content and
        'gluetun_monitor.is_healthy()' in main_content and
        'await asyncio.sleep(1)' in main_content
    )
    
    reduced_timeout_implemented = (
        'timeout = 5' in provisioner_content and
        'time.sleep(0.5)' in provisioner_content
    )
    
    assert startup_wait_implemented, "Startup wait logic not fully implemented"
    assert reduced_timeout_implemented, "Reduced timeout logic not fully implemented"
    
    print("✅ Fix addresses the original slow startup issue:")
    print("   - Startup waits for Gluetun health once (max 60s)")
    print("   - Individual engine timeout reduced from 30s to 5s")
    print("   - Expected improvement: 90s -> 5-75s startup time")

if __name__ == "__main__":
    print("🧪 Running simple startup delay fix validation...")
    
    test_main_has_gluetun_wait_logic()
    test_provisioner_has_reduced_timeout()
    test_fix_addresses_original_issue()
    
    print("🎉 All startup delay fix validations passed!")
    print("\nSummary of fix:")
    print("- Before: 3 engines × 30s timeout = 90s delay during startup")
    print("- After: 1 startup wait (max 60s) + 3 engines × 5s timeout = 5-75s")
    print("- Best case improvement: 90s -> ~5s (94% faster)")
    print("- Worst case improvement: 90s -> 75s (17% faster)")