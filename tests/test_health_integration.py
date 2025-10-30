#!/usr/bin/env python3
"""
Integration Test for Health Management Service

Tests the complete health management workflow including:
- Detection of unhealthy engines
- Automatic replacement process
- Service availability maintenance
- Configuration-driven behavior
"""

import sys
import os
import asyncio
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone, timedelta

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

async def test_complete_health_workflow():
    """Test the complete health management workflow."""
    from app.services.health_manager import HealthManager
    from app.models.schemas import EngineState
    
    with patch('app.services.health_manager.state') as mock_state, \
         patch('app.services.health_manager.check_acestream_health') as mock_health_check, \
         patch('app.services.health_manager.start_acestream') as mock_start, \
         patch('app.services.health_manager.stop_container') as mock_stop, \
         patch('app.services.health_manager.cfg') as mock_cfg, \
         patch('app.services.reindex.reindex_existing') as mock_reindex:
        
        # Configure test settings
        mock_cfg.MIN_REPLICAS = 3
        mock_cfg.ENGINE_VARIANT = "krinkuto11-amd64"
        mock_cfg.HEALTH_CHECK_INTERVAL_S = 1
        mock_cfg.HEALTH_FAILURE_THRESHOLD = 2  # Fail after 2 consecutive failures
        mock_cfg.HEALTH_UNHEALTHY_GRACE_PERIOD_S = 5  # Replace after 5 seconds
        mock_cfg.HEALTH_REPLACEMENT_COOLDOWN_S = 2  # Wait 2 seconds between replacements
        
        # Create test engines
        healthy_engine1 = EngineState(
            container_id="healthy1",
            container_name="healthy1",
            host="127.0.0.1",
            port=8080,
            labels={},
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            streams=[]
        )
        
        healthy_engine2 = EngineState(
            container_id="healthy2",
            container_name="healthy2",
            host="127.0.0.1",
            port=8081,
            labels={},
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            streams=[]
        )
        
        unhealthy_engine = EngineState(
            container_id="unhealthy1",
            container_name="unhealthy1",
            host="127.0.0.1",
            port=8082,
            labels={},
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc),
            streams=[]
        )
        
        # Track engine state changes
        engines_list = [healthy_engine1, healthy_engine2, unhealthy_engine]
        mock_state.list_engines.return_value = engines_list
        
        # Mock health responses
        def health_response(host, port):
            if port in [8080, 8081]:  # healthy engines
                return "healthy"
            elif port == 8082:  # unhealthy engine
                return "unhealthy"
            else:
                return "healthy"  # new engines
        
        mock_health_check.side_effect = health_response
        
        # Mock successful engine provisioning
        new_engine_response = Mock()
        new_engine_response.container_id = "replacement_engine"
        mock_start.return_value = new_engine_response
        
        # Mock state updates
        mock_state.update_engine_health = Mock()
        mock_state.remove_engine = Mock()
        
        # Create and start health manager
        health_manager = HealthManager()
        await health_manager.start()
        
        print("🔄 Starting health management workflow test...")
        
        # Phase 1: Let health manager detect the unhealthy engine
        print("Phase 1: Detecting unhealthy engine...")
        await asyncio.sleep(2.5)  # Let it run a few health checks
        
        # Verify unhealthy engine is tracked
        assert "unhealthy1" in health_manager._engine_health
        unhealthy_status = health_manager._engine_health["unhealthy1"]
        print(f"   Unhealthy engine failures: {unhealthy_status.consecutive_failures}")
        assert unhealthy_status.consecutive_failures >= mock_cfg.HEALTH_FAILURE_THRESHOLD
        assert unhealthy_status.is_considered_unhealthy()
        
        # Phase 2: Wait for grace period and replacement
        print("Phase 2: Waiting for replacement grace period...")
        await asyncio.sleep(6)  # Wait for grace period + replacement
        
        # Verify replacement was attempted
        unhealthy_status = health_manager._engine_health.get("unhealthy1")
        if unhealthy_status:
            print(f"   Replacement marked: {unhealthy_status.marked_for_replacement}")
            # Should be marked for replacement after grace period
            assert unhealthy_status.should_be_replaced()
        
        # Phase 3: Verify health summary
        print("Phase 3: Checking health summary...")
        summary = health_manager.get_health_summary()
        print(f"   Health summary: {summary}")
        
        assert summary["minimum_required"] == 3
        assert summary["total_engines"] == 3
        
        await health_manager.stop()
        
        print("✅ Complete health workflow test passed!")

def test_configuration_values():
    """Test that configuration values are properly used."""
    from app.services.health_manager import EngineHealthStatus
    
    with patch('app.services.health_manager.cfg') as mock_cfg:
        mock_cfg.HEALTH_FAILURE_THRESHOLD = 5
        mock_cfg.HEALTH_UNHEALTHY_GRACE_PERIOD_S = 120
        
        engine_health = EngineHealthStatus("test_engine")
        
        # Should not be unhealthy until reaching threshold
        engine_health.consecutive_failures = 4
        assert not engine_health.is_considered_unhealthy()
        
        engine_health.consecutive_failures = 5
        assert engine_health.is_considered_unhealthy()
        
        # Should not be replaced until grace period
        engine_health.first_failure_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        assert not engine_health.should_be_replaced()
        
        engine_health.first_failure_time = datetime.now(timezone.utc) - timedelta(seconds=130)
        assert engine_health.should_be_replaced()
        
        print("✅ Configuration values test passed!")

def test_health_api_endpoint():
    """Test the health status API endpoint functionality."""
    from app.services.health_manager import HealthManager
    
    with patch('app.services.health_manager.cfg') as mock_cfg:
        mock_cfg.MIN_REPLICAS = 2
        mock_cfg.HEALTH_CHECK_INTERVAL_S = 30
        
        health_manager = HealthManager()
        
        summary = health_manager.get_health_summary()
        
        # Verify all expected fields are present
        expected_fields = [
            "total_engines",
            "healthy_engines", 
            "unhealthy_engines",
            "marked_for_replacement",
            "minimum_required",
            "health_check_interval"
        ]
        
        for field in expected_fields:
            assert field in summary, f"Missing field: {field}"
        
        assert summary["minimum_required"] == 2
        assert summary["health_check_interval"] == 30
        
        print("✅ Health API endpoint test passed!")

if __name__ == "__main__":
    print("🧪 Running Health Management Integration Tests...")
    
    # Test configuration
    test_configuration_values()
    
    # Test API endpoint
    test_health_api_endpoint()
    
    # Test complete workflow
    asyncio.run(test_complete_health_workflow())
    
    print("🎉 All Health Management Integration tests passed!")