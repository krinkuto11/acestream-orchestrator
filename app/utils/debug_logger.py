"""
Debug Logger Module

Provides comprehensive debugging capabilities for tracking performance issues
during stress situations. Writes detailed logs to a persistent folder structure
with timestamps, request IDs, and performance metrics.

This module is designed to help diagnose:
- Performance degradation during high load
- Slow provisioning or health check issues
- VPN connectivity problems
- Circuit breaker activations
- Resource allocation bottlenecks
"""

import os
import logging
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from functools import wraps
import traceback

# Get logger for this module
logger = logging.getLogger(__name__)


class DebugLogger:
    """
    Centralized debug logger that writes detailed performance logs to disk.
    
    Features:
    - Session-based log files with timestamps
    - JSON-structured logs for easy parsing
    - Performance timing metrics
    - Request/operation tracking
    - Automatic stress situation detection
    """
    
    def __init__(self, enabled: bool = False, log_dir: str = "./debug_logs"):
        self.enabled = enabled
        self.log_dir = Path(log_dir)
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.session_start = time.time()
        
        if self.enabled:
            self._setup_log_directory()
            self._write_session_start()
    
    def _setup_log_directory(self):
        """Create log directory structure"""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Debug logging enabled. Logs will be written to: {self.log_dir.absolute()}")
        except Exception as e:
            logger.error(f"Failed to create debug log directory: {e}")
            self.enabled = False
    
    def _write_session_start(self):
        """Write session start marker"""
        self._write_log("session", {
            "event": "session_start",
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "log_directory": str(self.log_dir.absolute())
        })
    
    def _write_log(self, category: str, data: Dict[str, Any]):
        """Write a log entry to the appropriate category file"""
        if not self.enabled:
            return
        
        try:
            # Add common fields
            log_entry = {
                "session_id": self.session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.time() - self.session_start, 3),
                **data
            }
            
            # Write to category-specific file
            log_file = self.log_dir / f"{self.session_id}_{category}.jsonl"
            with open(log_file, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write debug log entry: {e}")
    
    def log_provisioning(self, operation: str, container_id: Optional[str] = None, 
                        duration: Optional[float] = None, success: bool = True, 
                        error: Optional[str] = None, **kwargs):
        """Log provisioning operations"""
        self._write_log("provisioning", {
            "operation": operation,
            "container_id": container_id,
            "duration_ms": round(duration * 1000, 2) if duration else None,
            "success": success,
            "error": error,
            **kwargs
        })
    
    def log_health_check(self, component: str, container_id: Optional[str] = None,
                        status: str = "unknown", duration: Optional[float] = None,
                        error: Optional[str] = None, **kwargs):
        """Log health check operations"""
        self._write_log("health", {
            "component": component,
            "container_id": container_id,
            "status": status,
            "duration_ms": round(duration * 1000, 2) if duration else None,
            "error": error,
            **kwargs
        })
    
    def log_vpn(self, operation: str, status: str = "unknown", 
                duration: Optional[float] = None, **kwargs):
        """Log VPN/Gluetun operations"""
        self._write_log("vpn", {
            "operation": operation,
            "status": status,
            "duration_ms": round(duration * 1000, 2) if duration else None,
            **kwargs
        })
    
    def log_autoscaler(self, operation: str, current_replicas: int = 0,
                      target_replicas: Optional[int] = None, **kwargs):
        """Log autoscaler operations"""
        self._write_log("autoscaler", {
            "operation": operation,
            "current_replicas": current_replicas,
            "target_replicas": target_replicas,
            **kwargs
        })
    
    def log_circuit_breaker(self, operation_type: str, state: str, 
                           failure_count: int = 0, **kwargs):
        """Log circuit breaker state changes"""
        self._write_log("circuit_breaker", {
            "operation_type": operation_type,
            "state": state,
            "failure_count": failure_count,
            **kwargs
        })
    
    def log_performance(self, operation: str, duration: float, 
                       threshold_exceeded: bool = False, **kwargs):
        """Log performance metrics"""
        self._write_log("performance", {
            "operation": operation,
            "duration_ms": round(duration * 1000, 2),
            "threshold_exceeded": threshold_exceeded,
            **kwargs
        })
    
    def log_stress_event(self, event_type: str, severity: str, 
                        description: str, **kwargs):
        """Log stress situation events (high load, failures, etc.)"""
        self._write_log("stress", {
            "event_type": event_type,
            "severity": severity,
            "description": description,
            **kwargs
        })
    
    def log_error(self, component: str, error: Exception, 
                 operation: Optional[str] = None, **kwargs):
        """Log detailed error information"""
        self._write_log("errors", {
            "component": component,
            "operation": operation,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            **kwargs
        })
    
    def log_custom(self, category: str, **kwargs):
        """Log custom events"""
        self._write_log(category, kwargs)


def timed_operation(category: str = "performance", operation_name: Optional[str] = None):
    """
    Decorator to automatically time and log operations.
    
    Usage:
        @timed_operation("provisioning", "start_container")
        def start_container(req):
            # ... implementation
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not debug_logger.enabled:
                return func(*args, **kwargs)
            
            op_name = operation_name or func.__name__
            start_time = time.time()
            success = True
            error = None
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                duration = time.time() - start_time
                debug_logger.log_performance(
                    operation=op_name,
                    duration=duration,
                    success=success,
                    error=error,
                    category=category
                )
        
        return wrapper
    return decorator


# Global debug logger instance
debug_logger: DebugLogger = None


def init_debug_logger(enabled: bool = False, log_dir: str = "./debug_logs"):
    """Initialize the global debug logger"""
    global debug_logger
    debug_logger = DebugLogger(enabled=enabled, log_dir=log_dir)
    return debug_logger


def get_debug_logger() -> DebugLogger:
    """Get the global debug logger instance"""
    global debug_logger
    if debug_logger is None:
        # Initialize with disabled state if not already initialized
        debug_logger = DebugLogger(enabled=False)
    return debug_logger
