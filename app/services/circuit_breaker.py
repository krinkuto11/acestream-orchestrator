"""
Circuit Breaker Pattern for Engine Management

Implements circuit breaker pattern to prevent rapid provisioning attempts
for engines that consistently fail, helping to maintain system stability.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from enum import Enum
from ..core.config import cfg
from .event_logger import event_logger

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Prevent operations
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """
    Circuit breaker for engine provisioning operations.
    
    Prevents rapid provisioning attempts when engines consistently fail,
    allowing time for underlying issues to be resolved.
    """
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 300):
        self.failure_threshold = failure_threshold  # Failures before opening circuit
        self.recovery_timeout = recovery_timeout    # Seconds before attempting recovery
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = CircuitState.CLOSED
        self.last_success_time: Optional[datetime] = None
        
    def can_execute(self) -> bool:
        """Check if operation can be executed based on circuit state."""
        now = datetime.now(timezone.utc)
        
        if self.state == CircuitState.CLOSED:
            return True
        elif self.state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if (self.last_failure_time and 
                (now - self.last_failure_time).total_seconds() > self.recovery_timeout):
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker moving to HALF_OPEN state - testing recovery")
                return True
            return False
        elif self.state == CircuitState.HALF_OPEN:
            return True
            
        return False
    
    def record_success(self):
        """Record a successful operation."""
        self.failure_count = 0
        self.last_success_time = datetime.now(timezone.utc)
        
        if self.state != CircuitState.CLOSED:
            logger.info("Circuit breaker CLOSED - operations restored")
            # Log event for circuit breaker recovery
            event_logger.log_event(
                event_type="health",
                category="recovered",
                message="Circuit breaker closed - normal operations restored",
                details={"previous_failures": self.failure_count}
            )
            self.state = CircuitState.CLOSED
    
    def record_failure(self):
        """Record a failed operation."""
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)
        
        if self.state == CircuitState.HALF_OPEN:
            # Failed during recovery test - back to open
            logger.warning("Circuit breaker back to OPEN - recovery failed")
            logger.debug(f"Circuit breaker reopened after recovery failure (failure_count={self.failure_count})")
            # Log event for circuit breaker reopening
            event_logger.log_event(
                event_type="health",
                category="failed",
                message="Circuit breaker reopened - recovery test failed",
                details={
                    "failure_count": self.failure_count,
                    "reason": "recovery_failed"
                }
            )
            self.state = CircuitState.OPEN
        elif (self.state == CircuitState.CLOSED and 
              self.failure_count >= self.failure_threshold):
            # Too many failures - open circuit
            logger.warning(f"Circuit breaker OPENED - {self.failure_count} consecutive failures")
            logger.debug(f"Circuit breaker opened (failure_count={self.failure_count}, threshold={self.failure_threshold})")
            # Log event for circuit breaker opening
            event_logger.log_event(
                event_type="health",
                category="failed",
                message=f"Circuit breaker opened after {self.failure_count} consecutive failures",
                details={
                    "failure_count": self.failure_count,
                    "threshold": self.failure_threshold,
                    "recovery_timeout": self.recovery_timeout
                }
            )
            self.state = CircuitState.OPEN
    
    def get_status(self) -> Dict:
        """Get current circuit breaker status."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_success_time": self.last_success_time.isoformat() if self.last_success_time else None,
            "recovery_timeout": self.recovery_timeout
        }

class EngineCircuitBreakerManager:
    """
    Manages circuit breakers for different engine provisioning scenarios.
    
    Helps prevent cascading failures and provides system stability during
    problematic periods.
    """
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        
        # Default circuit breaker for general provisioning
        self._breakers["general"] = CircuitBreaker(
            failure_threshold=getattr(cfg, 'CIRCUIT_BREAKER_FAILURE_THRESHOLD', 5),
            recovery_timeout=getattr(cfg, 'CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S', 300)
        )
        
        # Circuit breaker for replacement operations (more lenient)
        self._breakers["replacement"] = CircuitBreaker(
            failure_threshold=getattr(cfg, 'CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD', 3),
            recovery_timeout=getattr(cfg, 'CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S', 180)
        )
    
    def can_provision(self, operation_type: str = "general") -> bool:
        """Check if provisioning is allowed for the given operation type."""
        breaker = self._breakers.get(operation_type, self._breakers["general"])
        return breaker.can_execute()
    
    def record_provisioning_success(self, operation_type: str = "general"):
        """Record successful engine provisioning."""
        breaker = self._breakers.get(operation_type, self._breakers["general"])
        breaker.record_success()
        logger.debug(f"Recorded successful {operation_type} provisioning")
    
    def record_provisioning_failure(self, operation_type: str = "general"):
        """Record failed engine provisioning."""
        breaker = self._breakers.get(operation_type, self._breakers["general"])
        breaker.record_failure()
        logger.warning(f"Recorded failed {operation_type} provisioning")
    
    def get_status(self) -> Dict:
        """Get status of all circuit breakers."""
        return {
            breaker_name: breaker.get_status() 
            for breaker_name, breaker in self._breakers.items()
        }
    
    def force_reset(self, operation_type: str = None):
        """Force reset circuit breakers (for manual intervention)."""
        if operation_type:
            if operation_type in self._breakers:
                self._breakers[operation_type].state = CircuitState.CLOSED
                self._breakers[operation_type].failure_count = 0
                logger.info(f"Force reset {operation_type} circuit breaker")
        else:
            for breaker in self._breakers.values():
                breaker.state = CircuitState.CLOSED
                breaker.failure_count = 0
            logger.info("Force reset all circuit breakers")

# Global circuit breaker manager
circuit_breaker_manager = EngineCircuitBreakerManager()