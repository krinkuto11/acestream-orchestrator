#!/usr/bin/env python3
"""
Simplified Circuit Breaker Tests

Focus on core functionality without complex mocking.
"""

import sys
import os
import time

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_basic_circuit_breaker():
    """Test basic circuit breaker functionality."""
    from app.services.circuit_breaker import CircuitBreaker, CircuitState
    
    # Create a circuit breaker with known parameters
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
    
    print("Testing basic circuit breaker functionality...")
    
    # Initial state should be closed
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() == True, "Should initially allow execution"
    
    # Record failures up to threshold
    for i in range(3):
        breaker.record_failure()
        if i < 2:
            assert breaker.can_execute() == True, f"Should allow execution after {i+1} failures"
        else:
            assert breaker.can_execute() == False, "Should block execution after threshold failures"
    
    assert breaker.state == CircuitState.OPEN, "Should be in OPEN state after threshold failures"
    
    # Test recovery after timeout
    time.sleep(1.1)  # Wait for recovery timeout
    
    # Should move to HALF_OPEN
    can_execute_after_timeout = breaker.can_execute()
    assert can_execute_after_timeout == True, "Should allow execution after recovery timeout"
    assert breaker.state == CircuitState.HALF_OPEN, "Should be in HALF_OPEN state"
    
    # Success should close the circuit
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED, "Should be CLOSED after success in HALF_OPEN"
    assert breaker.can_execute() == True, "Should allow execution after recovery"
    
    print("✅ Basic circuit breaker test passed!")

def test_circuit_breaker_integration():
    """Test circuit breaker integration with the system."""
    print("Testing circuit breaker integration...")
    
    # Test that we can import and create the manager
    from app.services.circuit_breaker import EngineCircuitBreakerManager
    
    manager = EngineCircuitBreakerManager()
    
    # Basic functionality test
    assert manager.can_provision("general") == True, "Should initially allow provisioning"
    
    # Test status reporting
    status = manager.get_status()
    assert isinstance(status, dict), "Status should be a dictionary"
    assert "general" in status, "Should have general breaker status"
    assert "replacement" in status, "Should have replacement breaker status"
    
    # Test force reset
    manager.force_reset()  # Should not raise exception
    
    print("✅ Circuit breaker integration test passed!")

def test_provisioning_with_circuit_breaker():
    """Test that provisioning operations integrate with circuit breaker."""
    print("Testing provisioning integration...")
    
    # Mock a provisioning scenario
    from app.services.circuit_breaker import EngineCircuitBreakerManager
    
    manager = EngineCircuitBreakerManager()
    
    # Simulate successful provisioning
    assert manager.can_provision("replacement"), "Should allow replacement provisioning"
    manager.record_provisioning_success("replacement")
    
    # Simulate failed provisioning
    manager.record_provisioning_failure("replacement")
    assert manager.can_provision("replacement"), "Should still allow after one failure"
    
    print("✅ Provisioning integration test passed!")

if __name__ == "__main__":
    print("🧪 Running Simplified Circuit Breaker tests...")
    
    test_basic_circuit_breaker()
    test_circuit_breaker_integration()
    test_provisioning_with_circuit_breaker()
    
    print("🎉 All Simplified Circuit Breaker tests passed!")