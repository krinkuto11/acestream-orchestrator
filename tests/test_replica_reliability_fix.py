#!/usr/bin/env python3
"""
Integration test demonstrating the replica counting reliability fix.
This test simulates scenarios where Docker socket validation fails and shows
how the new centralized validation system handles them gracefully.
"""

import os
import sys
import time
import threading
from unittest.mock import Mock, patch, MagicMock
from concurrent.futures import ThreadPoolExecutor

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_concurrent_replica_validation():
    """Test that concurrent replica validation calls are handled correctly."""
    from app.services.replica_validator import ReplicaValidator
    from app.services.state import state
    
    print("🧪 Testing concurrent replica validation...")
    
    # Clear state
    state.clear_state()
    validator = ReplicaValidator()
    
    # Mock containers
    mock_containers = []
    for i in range(5):
        mock_container = Mock()
        mock_container.id = f"container_{i}"
        mock_container.status = "running"
        mock_container.name = f"test_{i}"
        mock_container.labels = {"test": "true"}
        mock_container.attrs = {'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}
        mock_containers.append(mock_container)
    
    call_count = 0
    def mock_list_managed():
        nonlocal call_count
        call_count += 1
        # Simulate some delay to test race conditions
        time.sleep(0.01)
        return mock_containers
    
    with patch('app.services.replica_validator.list_managed', side_effect=mock_list_managed):
        with patch('app.services.reindex.reindex_existing'):
            # Run multiple concurrent validations
            def validate():
                return validator.validate_and_sync_state()
            
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(validate) for _ in range(20)]
                results = [future.result() for future in futures]
            
            # All results should be consistent
            first_result = results[0]
            for result in results[1:]:
                assert result == first_result, f"Inconsistent results: {first_result} vs {result}"
            
            # Caching should reduce the number of Docker API calls
            print(f"   Docker API calls: {call_count} (should be much less than 20 due to caching)")
            assert call_count < 15, f"Too many Docker API calls: {call_count}"
    
    print("✅ Concurrent validation test passed")

def test_docker_socket_failure_recovery():
    """Test recovery when Docker socket operations fail."""
    from app.services.replica_validator import ReplicaValidator
    from app.services.state import state
    
    print("🧪 Testing Docker socket failure recovery...")
    
    state.clear_state()
    validator = ReplicaValidator()
    
    # First successful call
    mock_containers = [Mock(id="container_1", status="running", name="test", labels={}, 
                           attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}})]
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        with patch('app.services.reindex.reindex_existing'):
            result1 = validator.validate_and_sync_state()
            assert result1[0] == 1  # 1 running container
    
    # Simulate Docker socket failure
    def failing_list_managed():
        raise Exception("Docker socket connection failed")
    
    with patch('app.services.replica_validator.list_managed', side_effect=failing_list_managed):
        # Should handle failure gracefully - may return cached result or safe fallback
        result2 = validator.validate_and_sync_state()
        # The exact result depends on caching behavior, but should not crash
        assert isinstance(result2, tuple) and len(result2) == 3
    
    # Recovery after Docker socket comes back
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        with patch('app.services.reindex.reindex_existing'):
            result3 = validator.validate_and_sync_state()
            assert result3[0] == 1  # Should recover
    
    print("✅ Docker socket failure recovery test passed")

def test_state_docker_mismatch_detection():
    """Test detection and resolution of state/Docker mismatches."""
    from app.services.replica_validator import ReplicaValidator
    from app.services.state import state
    from app.models.schemas import EngineState
    
    print("🧪 Testing state/Docker mismatch detection...")
    
    state.clear_state()
    validator = ReplicaValidator()
    
    # Add engines to state
    now = state.now()
    state.engines["container_1"] = EngineState(
        container_id="container_1", container_name="test1", host="localhost", port=8000,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    state.engines["container_2"] = EngineState(
        container_id="container_2", container_name="test2", host="localhost", port=8001,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    state.engines["orphaned_container"] = EngineState(
        container_id="orphaned_container", container_name="orphaned", host="localhost", port=8002,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    
    # Mock Docker with different containers (missing container_2, has new container_3)
    mock_containers = [
        Mock(id="container_1", status="running", name="test1", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}),
        Mock(id="container_3", status="running", name="test3", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}})
    ]
    
    reindex_called = False
    def mock_reindex():
        nonlocal reindex_called
        reindex_called = True
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        with patch('app.services.reindex.reindex_existing', side_effect=mock_reindex):
            total_running, used_engines, free_count = validator.validate_and_sync_state()
            
            # Should detect mismatch and trigger reindex
            assert reindex_called, "Reindex should have been called"
            assert total_running == 2, f"Expected 2 running containers, got {total_running}"
            
            # Should have removed orphaned container from state
            assert "orphaned_container" not in state.engines, "Orphaned container should be removed"
    
    print("✅ State/Docker mismatch detection test passed")

def test_validation_caching_behavior():
    """Test that validation caching works correctly under various conditions."""
    from app.services.replica_validator import ReplicaValidator
    
    print("🧪 Testing validation caching behavior...")
    
    validator = ReplicaValidator()
    validator._validation_cache_ttl_s = 1  # 1 second for testing
    
    mock_containers = [Mock(id="container_1", status="running", name="test", labels={}, 
                           attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}})]
    
    call_count = 0
    def counting_list_managed():
        nonlocal call_count
        call_count += 1
        return mock_containers
    
    with patch('app.services.replica_validator.list_managed', side_effect=counting_list_managed):
        with patch('app.services.reindex.reindex_existing'):
            # First call
            result1 = validator.validate_and_sync_state()
            calls_after_first = call_count
            
            # Second call immediately (should use cache)
            result2 = validator.validate_and_sync_state()
            calls_after_second = call_count
            
            assert result1 == result2, "Cached results should be identical"
            assert calls_after_first == calls_after_second, "Second call should use cache"
            
            # Wait for cache to expire
            time.sleep(1.1)
            
            # Third call (should refresh cache)
            result3 = validator.validate_and_sync_state()
            calls_after_third = call_count
            
            assert result2 == result3, "Results should still be consistent"
            assert calls_after_third > calls_after_second, "Cache should have expired"
            
            # Force refresh (should bypass cache)
            result4 = validator.validate_and_sync_state(force_reindex=True)
            calls_after_fourth = call_count
            
            assert calls_after_fourth > calls_after_third, "Force refresh should bypass cache"
    
    print("✅ Validation caching behavior test passed")

def test_validation_status_endpoint():
    """Test the validation status endpoint provides comprehensive information."""
    from app.services.replica_validator import ReplicaValidator
    from app.services.state import state
    from app.models.schemas import EngineState
    
    print("🧪 Testing validation status endpoint...")
    
    state.clear_state()
    validator = ReplicaValidator()
    
    # Set up test scenario
    now = state.now()
    state.engines["container_1"] = EngineState(
        container_id="container_1", container_name="test1", host="localhost", port=8000,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    
    mock_containers = [
        Mock(id="container_1", status="running", name="test1", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}),
        Mock(id="container_2", status="running", name="test2", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}})
    ]
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        status = validator.get_validation_status()
        
        # Verify status structure
        assert 'timestamp' in status
        assert 'state_consistent' in status
        assert 'counts' in status
        assert 'discrepancies' in status
        assert 'cache_info' in status
        assert 'config' in status
        
        # Verify counts
        counts = status['counts']
        assert counts['state_engines'] == 1
        assert counts['docker_running'] == 2
        assert counts['docker_total'] == 2
        
        # Verify discrepancies
        discrepancies = status['discrepancies']
        assert discrepancies['missing_from_state'] == 1  # container_2 missing from state
        assert 'container_2' in discrepancies['missing_ids']
        
        # Should not be consistent
        assert status['state_consistent'] == False
    
    print("✅ Validation status endpoint test passed")

if __name__ == "__main__":
    print("🚀 Running replica reliability fix integration tests...")
    
    try:
        test_concurrent_replica_validation()
        test_docker_socket_failure_recovery()
        test_state_docker_mismatch_detection()
        test_validation_caching_behavior()
        test_validation_status_endpoint()
        
        print("\n🎯 All integration tests PASSED!")
        print("✅ Replica counting reliability has been significantly improved!")
        print("📊 Key improvements:")
        print("   - Centralized Docker socket validation")
        print("   - Race condition prevention through caching")
        print("   - Automatic state synchronization")
        print("   - Robust error handling and recovery")
        print("   - Comprehensive monitoring and debugging")
        
    except Exception as e:
        print(f"\n❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)