#!/usr/bin/env python3
"""
Test Circuit Breaker Pattern for Engine Management

Tests the circuit breaker implementation to ensure it prevents
cascading failures and maintains system stability during problematic periods.
"""

import sys
import os
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_circuit_breaker_states():
    """Test circuit breaker state transitions."""
    from app.services.circuit_breaker import CircuitBreaker, CircuitState
    
    # Create circuit breaker with low thresholds for testing
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=2)
    
    # Initially should be closed and allow operations
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() == True
    
    # Record some failures but not enough to trip the breaker
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() == True
    
    # One more failure should trip the breaker to OPEN
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() == False
    
    # Should remain closed during recovery timeout
    assert breaker.can_execute() == False
    
    # Wait for recovery timeout
    time.sleep(2.1)
    
    # Should move to HALF_OPEN and allow testing
    assert breaker.can_execute() == True
    assert breaker.state == CircuitState.HALF_OPEN
    
    # Success during HALF_OPEN should close the circuit
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() == True
    
    print("✅ Circuit breaker state transition tests passed!")

def test_circuit_breaker_recovery_failure():
    """Test circuit breaker behavior when recovery fails."""
    from app.services.circuit_breaker import CircuitBreaker, CircuitState
    
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
    
    # Trip the breaker
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    
    # Wait for recovery period
    time.sleep(1.1)
    
    # Should be HALF_OPEN
    assert breaker.can_execute() == True
    assert breaker.state == CircuitState.HALF_OPEN
    
    # Failure during HALF_OPEN should return to OPEN
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() == False
    
    print("✅ Circuit breaker recovery failure tests passed!")

def test_circuit_breaker_manager():
    """Test the circuit breaker manager functionality."""
    with patch('app.services.circuit_breaker.cfg') as mock_cfg:
        # Configure test settings
        mock_cfg.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
        mock_cfg.CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD = 3
        
        from app.services.circuit_breaker import EngineCircuitBreakerManager
        
        manager = EngineCircuitBreakerManager()
        
        # Should have default breakers
        assert manager.can_provision("general") == True
        assert manager.can_provision("replacement") == True
        
        # Record some failures for general provisioning
        for _ in range(5):  # Default threshold
            manager.record_provisioning_failure("general")
        
        # General should be blocked, replacement should still work
        assert manager.can_provision("general") == False
        assert manager.can_provision("replacement") == True
        
        # Record success for general (should remain blocked until recovery)
        manager.record_provisioning_success("general")
        assert manager.can_provision("general") == False
        
        # Force reset should restore functionality
        manager.force_reset("general")
        assert manager.can_provision("general") == True
        
        # Test status reporting
        status = manager.get_status()
        assert "general" in status
        assert "replacement" in status
        assert "state" in status["general"]
        
        print("✅ Circuit breaker manager tests passed!")

def test_autoscaler_with_circuit_breaker():
    """Test autoscaler integration with circuit breaker."""
    with patch('app.services.autoscaler.replica_validator') as mock_validator, \
         patch('app.services.autoscaler.start_acestream') as mock_start, \
         patch('app.services.autoscaler.cfg') as mock_cfg, \
         patch('app.services.autoscaler.circuit_breaker_manager') as mock_breaker:
        
        # Configure mocks
        mock_cfg.MIN_REPLICAS = 2
        mock_cfg.TARGET_IMAGE = "test_image"
        
        # Mock docker status showing deficit
        mock_status = Mock()
        mock_status.total_running = 0
        mock_validator.get_docker_container_status.return_value = {
            'total_running': 0
        }
        
        # Mock circuit breaker initially allowing operations
        mock_breaker.can_provision.return_value = True
        
        # Mock successful provisioning
        mock_response = Mock()
        mock_response.container_id = "test_container"
        mock_response.host_http_port = 8080
        mock_start.return_value = mock_response
        
        # Mock helper function
        with patch('app.services.autoscaler._count_healthy_engines', return_value=0):
            # Import and test ensure_minimum
            from app.services.autoscaler import ensure_minimum
            
            ensure_minimum()
            
            # Should have checked circuit breaker
            mock_breaker.can_provision.assert_called_with("general")
            
            # Should have attempted provisioning
            assert mock_start.called
            
            # Should have recorded success
            mock_breaker.record_provisioning_success.assert_called_with("general")
        
        print("✅ Autoscaler circuit breaker integration tests passed!")

def test_health_manager_with_circuit_breaker():
    """Test health manager integration with circuit breaker."""
    import asyncio
    from unittest.mock import AsyncMock
    
    async def run_test():
        with patch('app.services.health_manager.state') as mock_state, \
             patch('app.services.health_manager.start_acestream') as mock_start, \
             patch('app.services.health_manager.cfg') as mock_cfg, \
             patch('app.services.health_manager.circuit_breaker_manager') as mock_breaker:
            
            mock_cfg.MIN_REPLICAS = 2
            mock_cfg.TARGET_IMAGE = "test_image"
            
            # Mock circuit breaker initially blocking operations
            mock_breaker.can_provision.return_value = False
            
            from app.services.health_manager import HealthManager
            
            health_manager = HealthManager()
            
            # Test replacement engine starting with circuit breaker open
            await health_manager._start_replacement_engines(1)
            
            # Should have checked circuit breaker
            mock_breaker.can_provision.assert_called_with("replacement")
            
            # Should not have attempted provisioning due to circuit breaker
            assert not mock_start.called
            
            print("✅ Health manager circuit breaker integration tests passed!")
    
    asyncio.run(run_test())

def test_circuit_breaker_status():
    """Test circuit breaker status reporting."""
    from app.services.circuit_breaker import CircuitBreaker
    from datetime import datetime, timezone
    
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
    
    # Initial status
    status = breaker.get_status()
    assert status["state"] == "closed"
    assert status["failure_count"] == 0
    assert status["failure_threshold"] == 3
    assert status["recovery_timeout"] == 60
    
    # After failure
    breaker.record_failure()
    status = breaker.get_status()
    assert status["failure_count"] == 1
    assert status["last_failure_time"] is not None
    
    # After success
    breaker.record_success()
    status = breaker.get_status()
    assert status["failure_count"] == 0
    assert status["last_success_time"] is not None
    
    print("✅ Circuit breaker status reporting tests passed!")

if __name__ == "__main__":
    print("🧪 Running Circuit Breaker tests...")
    
    # Test basic functionality
    test_circuit_breaker_states()
    test_circuit_breaker_recovery_failure()
    test_circuit_breaker_status()
    
    # Test manager
    test_circuit_breaker_manager()
    
    # Test integrations
    test_autoscaler_with_circuit_breaker()
    test_health_manager_with_circuit_breaker()
    
    print("🎉 All Circuit Breaker tests passed!")