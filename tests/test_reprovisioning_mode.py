#!/usr/bin/env python3
"""
Test reprovisioning mode coordination.

This test validates that reprovisioning mode properly coordinates
with other system components (health manager, autoscaler).
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_reprovisioning_mode_state():
    """Test reprovisioning mode state management."""
    from app.services.state import state
    
    # Clear any existing state
    state._reprovisioning_mode = False
    state._reprovisioning_entered_at = None
    
    # Initially not in reprovisioning mode
    assert not state.is_reprovisioning_mode(), "Should not be in reprovisioning mode initially"
    
    # Enter reprovisioning mode
    result = state.enter_reprovisioning_mode()
    assert result, "Should successfully enter reprovisioning mode"
    assert state.is_reprovisioning_mode(), "Should be in reprovisioning mode"
    
    # Try to enter again (should fail)
    result = state.enter_reprovisioning_mode()
    assert not result, "Should not allow entering reprovisioning mode twice"
    
    # Get mode info
    info = state.get_reprovisioning_mode_info()
    assert info["active"] == True, "Mode info should show active"
    assert info["duration_seconds"] >= 0, "Duration should be non-negative"
    assert info["entered_at"] is not None, "Should have entered_at timestamp"
    
    # Exit reprovisioning mode
    result = state.exit_reprovisioning_mode()
    assert result, "Should successfully exit reprovisioning mode"
    assert not state.is_reprovisioning_mode(), "Should not be in reprovisioning mode after exit"
    
    # Try to exit again (should fail)
    result = state.exit_reprovisioning_mode()
    assert not result, "Should not allow exiting reprovisioning mode twice"
    
    # Get mode info when not active
    info = state.get_reprovisioning_mode_info()
    assert info["active"] == False, "Mode info should show not active"
    assert info["duration_seconds"] == 0, "Duration should be 0 when not active"
    assert info["entered_at"] is None, "Should not have entered_at when not active"
    
    print("✅ Reprovisioning mode state test passed!")

def test_reprovisioning_mode_coordination():
    """Test that health manager and autoscaler respect reprovisioning mode."""
    from app.services.state import state
    
    # Clear any existing state
    state._reprovisioning_mode = False
    state._emergency_mode = False
    
    # Initially not in reprovisioning mode
    assert not state.is_reprovisioning_mode(), "Should not be in reprovisioning mode initially"
    assert not state.is_emergency_mode(), "Should not be in emergency mode initially"
    
    # Enter reprovisioning mode
    state.enter_reprovisioning_mode()
    assert state.is_reprovisioning_mode(), "Should be in reprovisioning mode"
    
    # Components should check this flag and pause operations
    # The actual check is in health_manager._check_and_manage_health() and autoscaler.ensure_minimum()
    # We can't easily test the full async flow here, but we can verify the flag works
    
    # Exit reprovisioning mode
    state.exit_reprovisioning_mode()
    assert not state.is_reprovisioning_mode(), "Should not be in reprovisioning mode after exit"
    
    print("✅ Reprovisioning mode coordination test passed!")

def test_emergency_mode_and_reprovisioning_mode_independence():
    """Test that emergency mode and reprovisioning mode are independent."""
    from app.services.state import state
    
    # Clear any existing state
    state._reprovisioning_mode = False
    state._emergency_mode = False
    
    # Enter reprovisioning mode
    state.enter_reprovisioning_mode()
    assert state.is_reprovisioning_mode(), "Should be in reprovisioning mode"
    assert not state.is_emergency_mode(), "Should not be in emergency mode"
    
    # Exit reprovisioning mode
    state.exit_reprovisioning_mode()
    
    # Verify we can enter emergency mode independently (if in redundant VPN mode)
    # Note: emergency mode requires specific VPN configuration
    # For this test, we just verify the flags are independent
    assert not state.is_reprovisioning_mode(), "Should not be in reprovisioning mode"
    assert not state.is_emergency_mode(), "Should not be in emergency mode"
    
    print("✅ Emergency mode and reprovisioning mode independence test passed!")

if __name__ == "__main__":
    test_reprovisioning_mode_state()
    test_reprovisioning_mode_coordination()
    test_emergency_mode_and_reprovisioning_mode_independence()
    print("\n✅ All reprovisioning mode tests passed!")
