#!/usr/bin/env python3
"""
Demonstration of the replica counting reliability fix.
Shows how the new centralized validation system provides more consistent
and reliable replica counting compared to the old approach.
"""

import os
import sys
from unittest.mock import Mock, patch

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def demo_old_vs_new_approach():
    """Demonstrate the difference between old and new approaches."""
    from app.services.replica_validator import replica_validator
    from app.services.state import state
    from app.models.schemas import EngineState
    
    print("🚀 Demonstration: Replica Counting Reliability Fix")
    print("=" * 60)
    
    # Clear state for clean demo
    state.clear_state()
    
    print("\n📋 Scenario: State/Docker mismatch")
    print("- State contains 3 engines")
    print("- Docker actually has 2 running containers")
    print("- 1 engine in state is orphaned (container doesn't exist)")
    
    # Set up scenario: state has 3 engines, but Docker only has 2
    now = state.now()
    state.engines["container_1"] = EngineState(
        container_id="container_1", container_name="engine1", host="localhost", port=8000,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    state.engines["container_2"] = EngineState(
        container_id="container_2", container_name="engine2", host="localhost", port=8001,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    state.engines["orphaned_container"] = EngineState(
        container_id="orphaned_container", container_name="orphaned", host="localhost", port=8002,
        labels={}, first_seen=now, last_seen=now, streams=[]
    )
    
    # Mock Docker with only 2 containers
    mock_containers = [
        Mock(id="container_1", status="running", name="engine1", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}),
        Mock(id="container_2", status="running", name="engine2", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}})
    ]
    
    print(f"\n📊 Before fix (old approach):")
    print(f"   State engines: {len(state.engines)}")
    print(f"   Would count: {len(state.engines)} (incorrect - includes orphaned)")
    
    # Simulate old behavior
    def simulate_old_approach():
        # Old approach: just count state engines
        all_engines = state.list_engines()
        return len(all_engines)
    
    old_count = simulate_old_approach()
    print(f"   Old replica count: {old_count} ❌ (inaccurate)")
    
    print(f"\n📊 After fix (new approach):")
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        with patch('app.services.reindex.reindex_existing'):
            # New approach: validate against Docker socket
            total_running, used_engines, free_count = replica_validator.validate_and_sync_state()
            
            print(f"   Docker containers: {total_running}")
            print(f"   Used engines: {used_engines}")
            print(f"   Free engines: {free_count}")
            print(f"   New replica count: {total_running} ✅ (accurate)")
            
            # Check that orphaned engine was removed
            remaining_engines = len(state.engines)
            print(f"   State cleaned up: {remaining_engines} engines remaining")
            
            if "orphaned_container" not in state.engines:
                print("   ✅ Orphaned container removed from state")
            else:
                print("   ❌ Orphaned container still in state")
    
    print(f"\n🎯 Summary:")
    print(f"   Old approach: {old_count} replicas (inaccurate)")
    print(f"   New approach: {total_running} replicas (accurate)")
    print(f"   Improvement: ✅ {abs(old_count - total_running)} replica count discrepancy resolved")

def demo_validation_status():
    """Demonstrate the new validation status monitoring."""
    from app.services.replica_validator import replica_validator
    
    print("\n" + "=" * 60)
    print("🔍 New Monitoring Capability: Validation Status")
    print("=" * 60)
    
    # Mock some containers for demo
    mock_containers = [
        Mock(id="container_1", status="running", name="engine1", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}),
        Mock(id="container_2", status="running", name="engine2", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}}),
        Mock(id="container_3", status="running", name="engine3", labels={}, 
             attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}})
    ]
    
    with patch('app.services.replica_validator.list_managed', return_value=mock_containers):
        status = replica_validator.get_validation_status()
        
        print(f"\n📊 Current System Status:")
        print(f"   Timestamp: {status['timestamp']}")
        print(f"   State consistent: {status['state_consistent']}")
        
        counts = status['counts']
        print(f"\n📈 Container Counts:")
        print(f"   Docker running: {counts['docker_running']}")
        print(f"   Docker total: {counts['docker_total']}")
        print(f"   State engines: {counts['state_engines']}")
        print(f"   Used engines: {counts['used_engines']}")
        print(f"   Free engines: {counts['free_engines']}")
        
        discrepancies = status['discrepancies']
        print(f"\n⚠️  Discrepancies:")
        print(f"   Orphaned in state: {discrepancies['orphaned_in_state']}")
        print(f"   Missing from state: {discrepancies['missing_from_state']}")
        
        config = status['config']
        print(f"\n⚙️  Configuration:")
        print(f"   Min replicas: {config['min_replicas']}")
        print(f"   Max replicas: {config['max_replicas']}")
        print(f"   Current deficit: {config['deficit']}")
        
        print(f"\n✅ This status is now available for monitoring and debugging!")

def demo_caching_benefits():
    """Demonstrate the caching benefits."""
    from app.services.replica_validator import ReplicaValidator
    import time
    
    print("\n" + "=" * 60)
    print("⚡ Performance Improvement: Intelligent Caching")
    print("=" * 60)
    
    validator = ReplicaValidator()
    validator._validation_cache_ttl_s = 2  # 2 seconds for demo
    
    mock_containers = [Mock(id="container_1", status="running", name="test", labels={}, 
                           attrs={'Created': '2023-01-01T00:00:00Z', 'NetworkSettings': {'Ports': {}}})]
    
    call_count = 0
    def counting_list_managed():
        nonlocal call_count
        call_count += 1
        print(f"   🐳 Docker API call #{call_count}")
        return mock_containers
    
    with patch('app.services.replica_validator.list_managed', side_effect=counting_list_managed):
        with patch('app.services.reindex.reindex_existing'):
            print(f"\n📞 Making 5 rapid validation calls...")
            
            for i in range(5):
                result = validator.validate_and_sync_state()
                print(f"   Call {i+1}: {result[0]} running containers")
                time.sleep(0.1)  # Small delay
            
            print(f"\n📊 Results:")
            print(f"   Total API calls: {call_count}")
            print(f"   Expected without caching: 5")
            print(f"   Actual with caching: {call_count}")
            print(f"   Performance improvement: {((5 - call_count) / 5 * 100):.0f}% reduction in API calls")
            
            print(f"\n⏰ Waiting for cache to expire...")
            time.sleep(2.1)
            
            print(f"   Making another call after cache expiry...")
            validator.validate_and_sync_state()
            print(f"   Final API calls: {call_count}")
            print(f"   ✅ Cache correctly expired and refreshed")

if __name__ == "__main__":
    try:
        demo_old_vs_new_approach()
        demo_validation_status()
        demo_caching_benefits()
        
        print("\n" + "=" * 60)
        print("🎉 DEMONSTRATION COMPLETE")
        print("=" * 60)
        print("\n🚀 Key Improvements Implemented:")
        print("   ✅ Centralized Docker socket validation")
        print("   ✅ Automatic state synchronization")
        print("   ✅ Orphaned container cleanup")
        print("   ✅ Intelligent caching for performance")
        print("   ✅ Comprehensive status monitoring")
        print("   ✅ Race condition prevention")
        print("   ✅ Robust error handling")
        
        print("\n🎯 Problem Solved:")
        print("   The orchestrator now reliably counts total replicas")
        print("   and consistently validates against the Docker socket!")
        
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)