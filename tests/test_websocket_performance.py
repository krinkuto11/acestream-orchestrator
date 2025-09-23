#!/usr/bin/env python3
"""
Test to verify WebSocket performance optimizations.
Validates that data collection is fast and efficient.
"""

import os
import sys
import time
import asyncio
from unittest.mock import Mock, patch

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

async def test_realtime_data_collection_performance():
    """Test that realtime data collection is fast and efficient."""
    
    print("\n🧪 Testing realtime data collection performance...")
    
    try:
        from app.services.realtime import realtime_service
        from app.services.state import state
        from app.models.schemas import EngineState, StreamState, StreamStatSnapshot
        
        # Clear state
        state.engines.clear()
        state.streams.clear()
        state.stream_stats.clear()
        
        # Add test data to simulate a real scenario
        print("✓ Setting up test data...")
        
        # Add multiple engines
        for i in range(10):
            container_id = f"container_{i}"
            engine = EngineState(
                container_id=container_id,
                container_name=f"test-container-{i}",
                host="127.0.0.1",
                port=8080 + i,
                labels={},
                first_seen=state.now(),
                last_seen=state.now(),
                streams=[]
            )
            state.engines[container_id] = engine
        
        # Add multiple streams
        for i in range(20):
            stream_id = f"stream_{i}"
            stream = StreamState(
                id=stream_id,
                key_type="content_id",
                key=f"12345{i}",
                container_id=f"container_{i % 10}",
                playback_session_id=f"session_{i}",
                stat_url=f"http://127.0.0.1:808{i % 10}/stat",
                command_url=f"http://127.0.0.1:808{i % 10}/cmd",
                is_live=True,
                started_at=state.now(),
                status="started"
            )
            state.streams[stream_id] = stream
            
            # Add some stats for each stream
            stats = []
            for j in range(5):
                stat = StreamStatSnapshot(
                    ts=state.now(),
                    peers=10 + j,
                    speed_down=1000000 + j * 100000,
                    speed_up=500000 + j * 50000
                )
                stats.append(stat)
            state.stream_stats[stream_id] = stats
        
        print(f"✓ Added {len(state.engines)} engines and {len(state.streams)} streams")
        
        # Mock the VPN status to avoid external calls
        with patch('app.services.gluetun.get_vpn_status', return_value={"enabled": True, "status": "healthy"}):
            
            # Test data collection speed
            start_time = time.time()
            data = await realtime_service.collect_all_data()
            end_time = time.time()
            
            collection_time = end_time - start_time
            print(f"✓ Data collection took {collection_time:.3f} seconds")
            
            # Validate the collected data
            assert len(data["engines"]) == 10, f"Expected 10 engines, got {len(data['engines'])}"
            assert len(data["streams"]) == 20, f"Expected 20 streams, got {len(data['streams'])}"
            assert len(data["stream_stats"]) == 20, f"Expected 20 stream stats, got {len(data['stream_stats'])}"
            
            # Performance assertion - should be fast (under 100ms for this dataset)
            assert collection_time < 0.1, f"Data collection too slow: {collection_time:.3f}s (should be < 0.1s)"
            
            print(f"✓ Performance test PASSED: {collection_time:.3f}s < 0.1s")
            
            # Test multiple rapid collections to ensure consistency
            print("✓ Testing rapid consecutive collections...")
            times = []
            for i in range(5):
                start_time = time.time()
                data_repeat = await realtime_service.collect_all_data()
                end_time = time.time()
                times.append(end_time - start_time)
                
                # Verify data consistency
                assert data_repeat["engines"] == data["engines"], "Data consistency failed"
                assert data_repeat["streams"] == data["streams"], "Data consistency failed"
            
            avg_time = sum(times) / len(times)
            max_time = max(times)
            print(f"✓ Average collection time: {avg_time:.3f}s, Max: {max_time:.3f}s")
            
            assert max_time < 0.1, f"Max collection time too slow: {max_time:.3f}s"
            
        print("🎯 Realtime data collection performance test PASSED")
        return True
        
    except Exception as e:
        print(f"💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_update_interval_setting():
    """Test that the update interval is set to the optimized value."""
    
    print("\n🧪 Testing update interval configuration...")
    
    try:
        from app.services.realtime import realtime_service
        
        expected_interval = 0.5  # 500ms
        actual_interval = realtime_service.update_interval
        
        assert actual_interval == expected_interval, f"Expected interval {expected_interval}s, got {actual_interval}s"
        
        print(f"✓ Update interval correctly set to {actual_interval}s")
        print("🎯 Update interval test PASSED")
        return True
        
    except Exception as e:
        print(f"💥 Test FAILED: {e}")
        return False

async def test_realtime_snapshot_method():
    """Test the new get_realtime_snapshot method."""
    
    print("\n🧪 Testing realtime snapshot method...")
    
    try:
        from app.services.state import state
        from app.models.schemas import EngineState
        
        # Clear and add test data
        state.engines.clear()
        state.streams.clear() 
        state.stream_stats.clear()
        
        # Add an engine
        engine = EngineState(
            container_id="test_container",
            container_name="test-container",
            host="127.0.0.1",
            port=8080,
            labels={},
            first_seen=state.now(),
            last_seen=state.now(),
            streams=[]
        )
        state.engines["test_container"] = engine
        
        # Test snapshot method
        start_time = time.time()
        snapshot = state.get_realtime_snapshot()
        end_time = time.time()
        
        snapshot_time = end_time - start_time
        
        # Validate snapshot
        assert "engines" in snapshot, "Snapshot missing engines"
        assert "streams" in snapshot, "Snapshot missing streams"
        assert "stream_stats" in snapshot, "Snapshot missing stream_stats"
        assert len(snapshot["engines"]) == 1, "Snapshot should have 1 engine"
        
        # Should be very fast (under 10ms)
        assert snapshot_time < 0.01, f"Snapshot too slow: {snapshot_time:.3f}s"
        
        print(f"✓ Snapshot method took {snapshot_time:.6f}s")
        print("🎯 Realtime snapshot test PASSED")
        return True
        
    except Exception as e:
        print(f"💥 Test FAILED: {e}")
        return False

async def main():
    """Run all performance tests."""
    print("🚀 Running WebSocket performance tests...\n")
    
    test1 = await test_realtime_data_collection_performance()
    test2 = await test_update_interval_setting()
    test3 = await test_realtime_snapshot_method()
    
    overall_success = test1 and test2 and test3
    
    print(f"\n🎯 Overall result: {'PASSED' if overall_success else 'FAILED'}")
    return overall_success

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)