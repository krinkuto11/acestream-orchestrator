"""
Test debug mode functionality

This test verifies that:
1. Debug logger can be initialized and writes logs
2. Logs are written to correct categories
3. Session ID is consistent across logs
4. Stress events are detected and logged
5. Performance metrics are captured
"""

import os
import json
import tempfile
import time
from pathlib import Path
from datetime import datetime

# Add app to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.utils.debug_logger import DebugLogger, init_debug_logger, get_debug_logger


def test_debug_logger_initialization():
    """Test that debug logger initializes correctly"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        # Check session ID format
        assert logger.session_id is not None
        assert len(logger.session_id) == 15  # YYYYMMDD_HHMMSS
        
        # Check log directory was created
        assert os.path.exists(tmpdir)
        
        # Check session log was created
        session_files = list(Path(tmpdir).glob("*_session.jsonl"))
        assert len(session_files) == 1
        
        # Verify session start entry
        with open(session_files[0], 'r') as f:
            line = f.readline()
            entry = json.loads(line)
            assert entry["session_id"] == logger.session_id
            assert entry["event"] == "session_start"
            assert "timestamp" in entry
            assert "elapsed_seconds" in entry


def test_debug_logger_disabled():
    """Test that debug logger doesn't write when disabled"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=False, log_dir=tmpdir)
        
        # Try to write various logs
        logger.log_provisioning("test", success=True)
        logger.log_health_check("test", status="healthy")
        logger.log_vpn("test", status="connected")
        
        # No log files should be created (except maybe empty dir)
        log_files = list(Path(tmpdir).glob("*.jsonl"))
        assert len(log_files) == 0


def test_provisioning_logging():
    """Test provisioning operation logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        # Log successful provisioning
        logger.log_provisioning(
            operation="start_container",
            container_id="abc123",
            duration=2.5,
            success=True,
            image="test-image"
        )
        
        # Log failed provisioning
        logger.log_provisioning(
            operation="start_container_failed",
            container_id="def456",
            duration=1.2,
            success=False,
            error="Timeout"
        )
        
        # Check provisioning log file
        prov_files = list(Path(tmpdir).glob("*_provisioning.jsonl"))
        assert len(prov_files) == 1
        
        # Verify entries
        with open(prov_files[0], 'r') as f:
            lines = f.readlines()
            assert len(lines) == 2
            
            # Check success entry
            entry1 = json.loads(lines[0])
            assert entry1["operation"] == "start_container"
            assert entry1["container_id"] == "abc123"
            assert entry1["duration_ms"] == 2500.0
            assert entry1["success"] is True
            
            # Check failure entry
            entry2 = json.loads(lines[1])
            assert entry2["operation"] == "start_container_failed"
            assert entry2["success"] is False
            assert entry2["error"] == "Timeout"


def test_health_check_logging():
    """Test health check logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        logger.log_health_check(
            component="health_monitor",
            status="completed",
            duration=0.5,
            total_engines=10,
            healthy=9,
            unhealthy=1
        )
        
        # Check health log file
        health_files = list(Path(tmpdir).glob("*_health.jsonl"))
        assert len(health_files) == 1
        
        # Verify entry
        with open(health_files[0], 'r') as f:
            entry = json.loads(f.readline())
            assert entry["component"] == "health_monitor"
            assert entry["status"] == "completed"
            assert entry["duration_ms"] == 500.0
            assert entry["total_engines"] == 10


def test_vpn_logging():
    """Test VPN operation logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        logger.log_vpn(
            operation="health_check",
            status="healthy",
            duration=0.1,
            health_status="healthy"
        )
        
        logger.log_vpn(
            operation="transition",
            status="unhealthy",
            old_status=True,
            new_status=False
        )
        
        # Check VPN log file
        vpn_files = list(Path(tmpdir).glob("*_vpn.jsonl"))
        assert len(vpn_files) == 1
        
        # Verify entries
        with open(vpn_files[0], 'r') as f:
            lines = f.readlines()
            assert len(lines) == 2
            
            entry1 = json.loads(lines[0])
            assert entry1["operation"] == "health_check"
            assert entry1["status"] == "healthy"
            
            entry2 = json.loads(lines[1])
            assert entry2["operation"] == "transition"
            assert entry2["old_status"] is True
            assert entry2["new_status"] is False


def test_stress_event_logging():
    """Test stress situation detection and logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        logger.log_stress_event(
            event_type="slow_provisioning",
            severity="warning",
            description="Container provisioning took 15.5s",
            container_id="xyz789",
            duration=15.5
        )
        
        logger.log_stress_event(
            event_type="circuit_breaker_opened",
            severity="critical",
            description="Circuit breaker opened after 5 failures",
            failure_count=5
        )
        
        # Check stress log file
        stress_files = list(Path(tmpdir).glob("*_stress.jsonl"))
        assert len(stress_files) == 1
        
        # Verify entries
        with open(stress_files[0], 'r') as f:
            lines = f.readlines()
            assert len(lines) == 2
            
            entry1 = json.loads(lines[0])
            assert entry1["event_type"] == "slow_provisioning"
            assert entry1["severity"] == "warning"
            assert entry1["duration"] == 15.5
            
            entry2 = json.loads(lines[1])
            assert entry2["event_type"] == "circuit_breaker_opened"
            assert entry2["severity"] == "critical"


def test_circuit_breaker_logging():
    """Test circuit breaker state logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        logger.log_circuit_breaker(
            operation_type="provisioning",
            state="open",
            failure_count=5,
            threshold=5
        )
        
        # Check circuit breaker log file
        cb_files = list(Path(tmpdir).glob("*_circuit_breaker.jsonl"))
        assert len(cb_files) == 1
        
        # Verify entry
        with open(cb_files[0], 'r') as f:
            entry = json.loads(f.readline())
            assert entry["operation_type"] == "provisioning"
            assert entry["state"] == "open"
            assert entry["failure_count"] == 5


def test_error_logging():
    """Test error logging with exception details"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        try:
            raise ValueError("Test error")
        except Exception as e:
            logger.log_error(
                component="test_component",
                error=e,
                operation="test_operation",
                context="test_context"
            )
        
        # Check error log file
        error_files = list(Path(tmpdir).glob("*_errors.jsonl"))
        assert len(error_files) == 1
        
        # Verify entry
        with open(error_files[0], 'r') as f:
            entry = json.loads(f.readline())
            assert entry["component"] == "test_component"
            assert entry["operation"] == "test_operation"
            assert entry["error_type"] == "ValueError"
            assert "Test error" in entry["error_message"]
            assert "traceback" in entry


def test_session_consistency():
    """Test that session ID is consistent across all logs"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        # Write to multiple categories
        logger.log_provisioning("test", success=True)
        logger.log_health_check("test", status="healthy")
        logger.log_vpn("test", status="connected")
        logger.log_stress_event("test", "info", "test")
        
        # Collect all session IDs from all log files
        session_ids = set()
        for log_file in Path(tmpdir).glob("*.jsonl"):
            with open(log_file, 'r') as f:
                for line in f:
                    entry = json.loads(line)
                    session_ids.add(entry["session_id"])
        
        # All should have the same session ID
        assert len(session_ids) == 1
        assert logger.session_id in session_ids


def test_timestamp_format():
    """Test that timestamps are in correct ISO format"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        logger.log_provisioning("test", success=True)
        
        # Check timestamp format
        prov_files = list(Path(tmpdir).glob("*_provisioning.jsonl"))
        with open(prov_files[0], 'r') as f:
            entry = json.loads(f.readline())
            
            # Should be valid ISO format
            timestamp = entry["timestamp"]
            parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            assert parsed is not None


def test_elapsed_seconds():
    """Test that elapsed seconds increases over time"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        # Log first entry
        logger.log_provisioning("test1", success=True)
        time.sleep(0.1)
        
        # Log second entry
        logger.log_provisioning("test2", success=True)
        
        # Check elapsed seconds
        prov_files = list(Path(tmpdir).glob("*_provisioning.jsonl"))
        with open(prov_files[0], 'r') as f:
            lines = f.readlines()
            entry1 = json.loads(lines[0])
            entry2 = json.loads(lines[1])
            
            # Second entry should have higher elapsed seconds
            assert entry2["elapsed_seconds"] > entry1["elapsed_seconds"]


def test_global_logger():
    """Test global logger initialization and retrieval"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize global logger
        logger = init_debug_logger(enabled=True, log_dir=tmpdir)
        assert logger is not None
        
        # Get global logger
        logger2 = get_debug_logger()
        assert logger2 is logger
        
        # Can use global logger
        logger2.log_provisioning("test", success=True)
        
        # Check log was written
        prov_files = list(Path(tmpdir).glob("*_provisioning.jsonl"))
        assert len(prov_files) == 1


def test_performance_logging():
    """Test performance metric logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        logger.log_performance(
            operation="test_operation",
            duration=5.5,
            threshold_exceeded=True,
            threshold=5.0
        )
        
        # Check performance log file
        perf_files = list(Path(tmpdir).glob("*_performance.jsonl"))
        assert len(perf_files) == 1
        
        # Verify entry
        with open(perf_files[0], 'r') as f:
            entry = json.loads(f.readline())
            assert entry["operation"] == "test_operation"
            assert entry["duration_ms"] == 5500.0
            assert entry["threshold_exceeded"] is True


def test_custom_logging():
    """Test custom category logging"""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = DebugLogger(enabled=True, log_dir=tmpdir)
        
        logger.log_custom(
            "custom_category",
            field1="value1",
            field2=123,
            field3=True
        )
        
        # Check custom log file
        custom_files = list(Path(tmpdir).glob("*_custom_category.jsonl"))
        assert len(custom_files) == 1
        
        # Verify entry
        with open(custom_files[0], 'r') as f:
            entry = json.loads(f.readline())
            assert entry["field1"] == "value1"
            assert entry["field2"] == 123
            assert entry["field3"] is True


if __name__ == "__main__":
    # Run tests
    print("Testing debug mode functionality...")
    
    test_debug_logger_initialization()
    print("✓ Debug logger initialization")
    
    test_debug_logger_disabled()
    print("✓ Debug logger disabled mode")
    
    test_provisioning_logging()
    print("✓ Provisioning logging")
    
    test_health_check_logging()
    print("✓ Health check logging")
    
    test_vpn_logging()
    print("✓ VPN logging")
    
    test_stress_event_logging()
    print("✓ Stress event logging")
    
    test_circuit_breaker_logging()
    print("✓ Circuit breaker logging")
    
    test_error_logging()
    print("✓ Error logging")
    
    test_session_consistency()
    print("✓ Session consistency")
    
    test_timestamp_format()
    print("✓ Timestamp format")
    
    test_elapsed_seconds()
    print("✓ Elapsed seconds")
    
    test_global_logger()
    print("✓ Global logger")
    
    test_performance_logging()
    print("✓ Performance logging")
    
    test_custom_logging()
    print("✓ Custom logging")
    
    print("\n✅ All debug mode tests passed!")
