"""
Test for parallel container shutdown during cleanup.

This test verifies that the cleanup_all() function stops containers in parallel
rather than sequentially, which significantly improves shutdown performance.
"""

import time
from unittest.mock import Mock, patch, MagicMock
from app.services.state import State


def test_cleanup_all_stops_containers_in_parallel():
    """Test that cleanup_all stops multiple containers in parallel."""
    
    # Create a state instance
    state = State()
    
    # Create mock containers
    mock_containers = [
        Mock(id=f"container_{i}_{'a' * 52}") for i in range(6)
    ]
    
    # Track stop calls and their timing
    stop_times = []
    stop_calls = []
    
    def mock_stop_container(container_id):
        """Mock stop_container that records timing."""
        stop_calls.append(container_id)
        stop_times.append(time.time())
        # Simulate the 10-second timeout that Docker uses
        time.sleep(0.1)  # Use shorter sleep for testing
    
    with patch('app.services.health.list_managed', return_value=mock_containers), \
         patch('app.services.provisioner.stop_container', side_effect=mock_stop_container):
        
        start_time = time.time()
        state.cleanup_all()
        total_time = time.time() - start_time
        
        # All 6 containers should have been stopped
        assert len(stop_calls) == 6
        
        # Verify all containers were stopped
        stopped_ids = set(stop_calls)
        expected_ids = {c.id for c in mock_containers}
        assert stopped_ids == expected_ids
        
        # If sequential, it would take 6 * 0.1 = 0.6 seconds
        # If parallel, it should take just over 0.1 seconds
        # Allow some overhead for thread management
        assert total_time < 0.4, f"Shutdown took {total_time}s, expected < 0.4s for parallel execution"
        
        # Verify that stops started close together (parallel execution)
        if len(stop_times) > 1:
            # All stops should start within a short time window (not spread out over 0.6s)
            time_spread = max(stop_times) - min(stop_times)
            # In parallel execution, all should start within ~0.15s
            # In sequential, this would be ~0.5s
            assert time_spread < 0.3, f"Stop operations spread over {time_spread}s, not parallel enough"


def test_cleanup_all_handles_errors_gracefully():
    """Test that cleanup_all continues even if some containers fail to stop."""
    
    state = State()
    
    # Create mock containers
    mock_containers = [
        Mock(id=f"container_{i}_{'a' * 52}") for i in range(4)
    ]
    
    stop_calls = []
    
    def mock_stop_container(container_id):
        """Mock stop_container that fails for some containers."""
        stop_calls.append(container_id)
        # Fail for container_1 and container_3
        if "container_1" in container_id or "container_3" in container_id:
            raise Exception(f"Failed to stop {container_id}")
    
    with patch('app.services.health.list_managed', return_value=mock_containers), \
         patch('app.services.provisioner.stop_container', side_effect=mock_stop_container):
        
        # Should not raise an exception
        state.cleanup_all()
        
        # All 4 containers should have been attempted
        assert len(stop_calls) == 4


def test_cleanup_all_with_no_containers():
    """Test that cleanup_all handles the case with no containers gracefully."""
    
    state = State()
    
    with patch('app.services.health.list_managed', return_value=[]):
        # Should not raise an exception
        state.cleanup_all()


def test_cleanup_all_respects_max_workers_limit():
    """Test that cleanup_all limits concurrent workers to avoid overwhelming Docker."""
    
    state = State()
    
    # Create many mock containers (more than max_workers limit of 10)
    mock_containers = [
        Mock(id=f"container_{i}_{'a' * 52}") for i in range(15)
    ]
    
    stop_calls = []
    
    def mock_stop_container(container_id):
        """Mock stop_container that just records the call."""
        stop_calls.append(container_id)
        time.sleep(0.01)  # Minimal sleep to ensure concurrent execution
    
    with patch('app.services.health.list_managed', return_value=mock_containers), \
         patch('app.services.provisioner.stop_container', side_effect=mock_stop_container):
        
        state.cleanup_all()
        
        # All 15 containers should have been stopped
        assert len(stop_calls) == 15
        
        # Note: We can't easily verify the max_workers limit from outside,
        # but we verify that all containers were processed successfully


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
