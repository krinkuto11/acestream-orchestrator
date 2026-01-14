"""
Performance metrics collection for critical system operations.

Tracks timing and throughput for key operations to identify bottlenecks:
- HLS manifest generation
- HLS segment fetching
- MPEG-TS stream data processing
- Docker stats collection
- Event handling
"""

import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import deque
import threading

logger = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    """Single metric measurement"""
    timestamp: datetime
    duration_ms: float
    operation: str
    success: bool
    metadata: Dict = field(default_factory=dict)


class PerformanceMetrics:
    """Collects and aggregates performance metrics"""
    
    def __init__(self, max_samples: int = 1000):
        """Initialize metrics collector
        
        Args:
            max_samples: Maximum number of samples to keep per operation type
        """
        self._metrics: Dict[str, deque] = {}
        self._max_samples = max_samples
        self._lock = threading.Lock()
        
    def record(self, operation: str, duration_ms: float, success: bool = True, metadata: Optional[Dict] = None):
        """Record a performance metric
        
        Args:
            operation: Name of operation (e.g., 'hls_manifest_generation')
            duration_ms: Duration in milliseconds
            success: Whether operation succeeded
            metadata: Optional additional context
        """
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            operation=operation,
            success=success,
            metadata=metadata or {}
        )
        
        with self._lock:
            if operation not in self._metrics:
                self._metrics[operation] = deque(maxlen=self._max_samples)
            
            self._metrics[operation].append(snapshot)
    
    def get_stats(self, operation: str, window_seconds: Optional[int] = None) -> Dict:
        """Get statistics for an operation
        
        Args:
            operation: Name of operation
            window_seconds: Optional time window to consider (None = all samples)
            
        Returns:
            Dictionary with statistics (count, avg, p50, p95, p99, success_rate)
        """
        with self._lock:
            if operation not in self._metrics or not self._metrics[operation]:
                return {
                    'count': 0,
                    'avg_ms': 0,
                    'p50_ms': 0,
                    'p95_ms': 0,
                    'p99_ms': 0,
                    'min_ms': 0,
                    'max_ms': 0,
                    'success_rate': 0.0
                }
            
            samples = list(self._metrics[operation])
        
        # Filter by time window if specified
        if window_seconds:
            cutoff = datetime.now(timezone.utc).timestamp() - window_seconds
            samples = [s for s in samples if s.timestamp.timestamp() >= cutoff]
        
        if not samples:
            return {
                'count': 0,
                'avg_ms': 0,
                'p50_ms': 0,
                'p95_ms': 0,
                'p99_ms': 0,
                'min_ms': 0,
                'max_ms': 0,
                'success_rate': 0.0
            }
        
        # Calculate statistics
        durations = sorted([s.duration_ms for s in samples])
        successes = sum(1 for s in samples if s.success)
        
        def percentile(data, p):
            if not data:
                return 0
            k = (len(data) - 1) * p / 100
            f = int(k)
            c = f + 1 if f < len(data) - 1 else f
            return data[f] + (data[c] - data[f]) * (k - f) if c != f else data[f]
        
        return {
            'count': len(samples),
            'avg_ms': sum(durations) / len(durations),
            'p50_ms': percentile(durations, 50),
            'p95_ms': percentile(durations, 95),
            'p99_ms': percentile(durations, 99),
            'min_ms': min(durations),
            'max_ms': max(durations),
            'success_rate': (successes / len(samples)) * 100 if samples else 0.0
        }
    
    def get_all_stats(self, window_seconds: Optional[int] = None) -> Dict[str, Dict]:
        """Get statistics for all operations
        
        Args:
            window_seconds: Optional time window to consider
            
        Returns:
            Dictionary mapping operation names to their statistics
        """
        with self._lock:
            operations = list(self._metrics.keys())
        
        return {op: self.get_stats(op, window_seconds) for op in operations}


# Context manager for timing operations
class Timer:
    """Context manager for timing operations"""
    
    def __init__(self, metrics: PerformanceMetrics, operation: str, metadata: Optional[Dict] = None):
        self.metrics = metrics
        self.operation = operation
        self.metadata = metadata or {}
        self.start_time = None
        self.success = True
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.time() - self.start_time) * 1000
        self.success = exc_type is None
        self.metrics.record(self.operation, duration_ms, self.success, self.metadata)
        return False  # Don't suppress exceptions


# Global metrics instance
performance_metrics = PerformanceMetrics()


# Convenience function for timing
def timed(operation: str, metadata: Optional[Dict] = None):
    """Decorator for timing functions
    
    Usage:
        @timed('my_operation')
        def my_function():
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with Timer(performance_metrics, operation, metadata):
                return func(*args, **kwargs)
        return wrapper
    return decorator
