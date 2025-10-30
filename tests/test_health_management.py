#!/usr/bin/env python3
"""
Test Health Management Service

This test validates the enhanced health monitoring and automatic engine replacement
functionality to ensure service availability.
"""

import sys
import os
import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_engine_health_status():
    """Test EngineHealthStatus tracking logic."""
    from app.services.health_manager import EngineHealthStatus
    
    engine_health = EngineHealthStatus("test_container_id")
    
    # Initially should not be unhealthy
    assert not engine_health.is_considered_unhealthy()
    assert not engine_health.should_be_replaced()
    
    # After 1-2 failures, still not unhealthy
    engine_health.consecutive_failures = 1
    assert not engine_health.is_considered_unhealthy()
    
    engine_health.consecutive_failures = 2
    assert not engine_health.is_considered_unhealthy()
    
    # After 3 failures, considered unhealthy
    engine_health.consecutive_failures = 3
    engine_health.first_failure_time = datetime.now(timezone.utc)
    assert engine_health.is_considered_unhealthy()
    
    # But should not be replaced immediately (needs 60s grace period)
    assert not engine_health.should_be_replaced()
    
    # After 60+ seconds, should be replaced
    engine_health.first_failure_time = datetime.now(timezone.utc) - timedelta(seconds=65)
    assert engine_health.should_be_replaced()
    
    print("✅ EngineHealthStatus logic tests passed!")

async def test_health_manager_initialization():
    """Test health manager initialization and basic functionality."""
    from app.services.health_manager import HealthManager
    
    # Create health manager with short interval for testing
    health_manager = HealthManager(check_interval=1)
    
    # Test initialization
    assert not health_manager._running
    assert health_manager.check_interval == 1
    assert len(health_manager._engine_health) == 0
    
    # Test start
    await health_manager.start()
    assert health_manager._running
    
    # Test stop
    await health_manager.stop()
    assert not health_manager._running
    
    print("✅ Health manager initialization tests passed!")

async def test_health_management_with_mocked_engines():
    """Test health management with mocked engine data."""
    from app.services.health_manager import HealthManager
    from app.models.schemas import EngineState
    
    # Mock state and health functions
    with patch('app.services.health_manager.state') as mock_state, \
         patch('app.services.health_manager.check_acestream_health') as mock_health_check, \
         patch('app.services.health_manager.cfg') as mock_cfg:
        
        # Configure mocks
        mock_cfg.MIN_REPLICAS = 2
        mock_cfg.ENGINE_VARIANT = "krinkuto11-amd64"
        
        # Create mock engines
        healthy_engine = EngineState(
            container_id="healthy_engine",
            container_name="healthy",
            host="127.0.0.1",
            port=8080,
            labels={},
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            streams=[]
        )
        
        unhealthy_engine = EngineState(
            container_id="unhealthy_engine", 
            container_name="unhealthy",
            host="127.0.0.1",
            port=8081,
            labels={},
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            streams=[]
        )
        
        mock_state.list_engines.return_value = [healthy_engine, unhealthy_engine]
        
        # Mock health check responses
        def mock_health_response(host, port):
            if port == 8080:  # healthy engine
                return "healthy"
            else:  # unhealthy engine
                return "unhealthy"
        
        mock_health_check.side_effect = mock_health_response
        
        # Create and test health manager
        health_manager = HealthManager(check_interval=0.1)  # Very fast for testing
        
        await health_manager.start()
        
        # Let it run a few cycles to detect unhealthy engine
        await asyncio.sleep(0.5)
        
        # Check that it tracked the engines
        assert len(health_manager._engine_health) == 2
        assert "healthy_engine" in health_manager._engine_health
        assert "unhealthy_engine" in health_manager._engine_health
        
        # Check health summary
        summary = health_manager.get_health_summary()
        assert summary["total_engines"] == 2
        assert summary["minimum_required"] == 2
        
        await health_manager.stop()
        
        print("✅ Health management with mocked engines tests passed!")

def test_health_summary():
    """Test health summary generation."""
    from app.services.health_manager import HealthManager, EngineHealthStatus
    from app.models.schemas import EngineState
    
    with patch('app.services.health_manager.state') as mock_state, \
         patch('app.services.health_manager.cfg') as mock_cfg, \
         patch('app.services.health_manager.circuit_breaker_manager') as mock_cb:
        
        mock_cfg.MIN_REPLICAS = 3
        mock_cfg.HEALTH_FAILURE_THRESHOLD = 3
        mock_cb.get_status.return_value = {"general": {"state": "closed"}}
        
        # Create mock engines
        engines = [
            EngineState(
                container_id="engine1",
                container_name="engine1",
                host="127.0.0.1",
                port=8080,
                labels={},
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                streams=[]
            ),
            EngineState(
                container_id="engine2",
                container_name="engine2", 
                host="127.0.0.1",
                port=8081,
                labels={},
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                streams=[]
            )
        ]
        
        mock_state.list_engines.return_value = engines
        
        health_manager = HealthManager()
        
        # Add some health tracking data
        health_manager._engine_health["engine1"] = EngineHealthStatus("engine1")
        health_manager._engine_health["engine1"].consecutive_failures = 0  # healthy
        
        health_manager._engine_health["engine2"] = EngineHealthStatus("engine2")
        health_manager._engine_health["engine2"].consecutive_failures = 5  # unhealthy
        health_manager._engine_health["engine2"].marked_for_replacement = True
        
        summary = health_manager.get_health_summary()
        
        assert summary["total_engines"] == 2
        assert summary["healthy_engines"] == 1
        assert summary["unhealthy_engines"] == 1
        assert summary["marked_for_replacement"] == 1
        assert summary["minimum_required"] == 3
        
        print("✅ Health summary tests passed!")

async def test_engine_replacement_logic():
    """Test the engine replacement logic with safety checks."""
    from app.services.health_manager import HealthManager
    
    with patch('app.services.health_manager.state') as mock_state, \
         patch('app.services.health_manager.check_acestream_health') as mock_health_check, \
         patch('app.services.health_manager.start_acestream') as mock_start, \
         patch('app.services.health_manager.stop_container') as mock_stop, \
         patch('app.services.health_manager.cfg') as mock_cfg:
        
        mock_cfg.MIN_REPLICAS = 2
        mock_cfg.ENGINE_VARIANT = "krinkuto11-amd64"
        
        # Mock successful provisioning
        mock_response = Mock()
        mock_response.container_id = "new_engine"
        mock_start.return_value = mock_response
        
        health_manager = HealthManager(check_interval=0.1)
        
        # Test that replacement doesn't happen without enough healthy engines
        engines = []  # No engines
        mock_state.list_engines.return_value = engines
        mock_health_check.return_value = "healthy"
        
        await health_manager._check_and_manage_health()
        
        # Should have started new engines for minimum requirement
        assert mock_start.called
        
        print("✅ Engine replacement logic tests passed!")

if __name__ == "__main__":
    print("🧪 Running Health Management tests...")
    
    # Test basic functionality
    test_engine_health_status()
    
    # Test health summary
    test_health_summary()
    
    # Test async functionality
    asyncio.run(test_health_manager_initialization())
    asyncio.run(test_health_management_with_mocked_engines())
    asyncio.run(test_engine_replacement_logic())
    
    print("🎉 All Health Management tests passed!")