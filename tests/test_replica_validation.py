#!/usr/bin/env python3
"""
Test to verify the replica validation improvements work correctly.
Tests that Docker socket validation is reliable and consistent.
"""

import os
import sys
import time
import threading
from unittest.mock import Mock, patch
import pytest

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_replica_validator_consistency():
    """Test that replica validator provides consistent Docker socket validation."""
    from app.services.replica_validator import ReplicaValidator
    from app.services.state import state
    
    # Clear state for clean test
    state.clear_state()
    
    validator = ReplicaValidator()
    
    # Mock Docker containers
    mock_containers = []
    for i in range(3):
        mock_container = Mock()
        mock_container.id = f"container_{i}"
        mock_container.status = "running"
        mock_container.name = f"test_container_{i}"
        mock_container.labels = {"test.orchestrator": "true"}
        mock_container.attrs = {
            'Created': '2023-01-01T00:00:00Z',
            'NetworkSettings': {'Ports': {}}
        }
        mock_containers.append(mock_container)
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        docker_status = validator.get_docker_container_status()
        
        assert docker_status['total_running'] == 3
        assert len(docker_status['running_container_ids']) == 3
        assert 'container_0' in docker_status['running_container_ids']
        assert 'container_1' in docker_status['running_container_ids']
        assert 'container_2' in docker_status['running_container_ids']

def test_replica_validator_sync():
    """Test that replica validator is state-derived and side-effect free."""
    from app.services.replica_validator import ReplicaValidator
    from app.services.state import state
    from app.models.schemas import EngineState
    
    # Clear state for clean test
    state.clear_state()
    
    validator = ReplicaValidator()
    
    # Add some engines to state
    now = state.now()
    state.engines["container_1"] = EngineState(
        container_id="container_1", container_name="test1", host="localhost", port=8000,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    state.engines["container_2"] = EngineState(
        container_id="container_2", container_name="test2", host="localhost", port=8001,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    
    # Mock Docker containers (only container_1 exists in Docker)
    # In declarative mode validate_and_sync_state should ignore Docker and avoid reindex side effects.
    mock_container = Mock()
    mock_container.id = "container_1"
    mock_container.status = "running"
    mock_container.name = "test_container_1"
    mock_container.labels = {"test.orchestrator": "true"}
    mock_container.attrs = {
        'Created': '2023-01-01T00:00:00Z',
        'NetworkSettings': {'Ports': {}}
    }
    
    with patch('app.services.replica_validator.list_managed', return_value=[mock_container]) as list_managed_mock:
        with patch('app.services.reindex.reindex_existing') as reindex_mock:
            total_running, used_engines, free_count = validator.validate_and_sync_state()

            # Should use state counts only.
            assert total_running == 2  # Two managed engines in state
            assert used_engines == 0   # No active streams
            assert free_count == 2     # Both are free

            # No imperative Docker sync/reindex should happen.
            assert list_managed_mock.call_count == 0
            reindex_mock.assert_not_called()

            # State should remain unchanged.
            assert len(state.engines) == 2

def test_replica_validator_caching():
    """Test that replica validator caches results appropriately."""
    from app.services.replica_validator import ReplicaValidator
    
    validator = ReplicaValidator()
    validator._validation_cache_ttl_s = 10  # 10 second cache
    
    mock_containers = [Mock()]
    mock_containers[0].id = "container_1"
    mock_containers[0].status = "running"
    mock_containers[0].name = "test"
    mock_containers[0].labels = {}
    mock_containers[0].attrs = {'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers) as mock_list:
        with patch('app.services.reindex.reindex_existing'):
            # First call
            result1 = validator.validate_and_sync_state()
            call_count_1 = mock_list.call_count
            
            # Second call immediately (should use cache)
            result2 = validator.validate_and_sync_state()
            call_count_2 = mock_list.call_count
            
            # Results should be the same
            assert result1 == result2
            # Docker should only be queried once due to caching
            assert call_count_2 == call_count_1

def test_replica_deficit_calculation():
    """Test deficit calculation with various scenarios."""
    from app.services.replica_validator import ReplicaValidator
    
    validator = ReplicaValidator()
    
    # Mock 2 running containers, 1 used
    with patch.object(validator, 'validate_and_sync_state', return_value=(2, 1, 1)):
        # Need 3 free, have 1 free -> deficit = 2
        deficit = validator.get_replica_deficit(3)
        assert deficit == 2
        
        # Need 1 free, have 1 free -> deficit = 0
        deficit = validator.get_replica_deficit(1)
        assert deficit == 0
        
        # Need 0 free, have 1 free -> deficit = 0
        deficit = validator.get_replica_deficit(0)
        assert deficit == 0

def test_state_consistency_check():
    """Test consistency checking between state and Docker."""
    from app.services.replica_validator import ReplicaValidator
    from app.services.state import state
    from app.models.schemas import EngineState
    
    state.clear_state()
    validator = ReplicaValidator()
    
    # Add 2 engines to state
    now = state.now()
    state.engines["container_1"] = EngineState(
        container_id="container_1", container_name="test1", host="localhost", port=8000,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    state.engines["container_2"] = EngineState(
        container_id="container_2", container_name="test2", host="localhost", port=8001,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    
    # Mock 2 Docker containers
    mock_containers = []
    for i in range(2):
        mock_container = Mock()
        mock_container.id = f"container_{i+1}"
        mock_container.status = "running"
        mock_container.name = f"test{i+1}"
        mock_container.labels = {}
        mock_container.attrs = {'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}
        mock_containers.append(mock_container)
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        # Should be consistent
        assert validator.is_state_consistent() == True
    
    # Remove one container from Docker
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers[:1]):
        # Should be inconsistent now
        assert validator.is_state_consistent() == False

if __name__ == "__main__":
    print("🧪 Running replica validation tests...")
    
    try:
        test_replica_validator_consistency()
        print("✅ test_replica_validator_consistency passed")
        
        test_replica_validator_sync()
        print("✅ test_replica_validator_sync passed")
        
        test_replica_validator_caching()
        print("✅ test_replica_validator_caching passed")
        
        test_replica_deficit_calculation()
        print("✅ test_replica_deficit_calculation passed")
        
        test_state_consistency_check()
        print("✅ test_state_consistency_check passed")
        
        print("\n🎯 All replica validation tests PASSED!")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)