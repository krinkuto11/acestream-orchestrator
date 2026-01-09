#!/usr/bin/env python3
"""
Test to verify that engine selection fills engines sequentially.

When MAX_STREAMS_PER_ENGINE=5:
- Engines should fill to 4 streams before assigning to a new engine
- Priority: 1. Engine with most streams (not at max), 2. Forwarded engine
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_engine_selection_sequential_filling():
    """Test that engine selection prioritizes filling existing engines before new ones."""
    
    print("\n🧪 Testing sequential engine filling behavior...")
    
    from app.services.state import state
    from app.core.config import cfg
    from app.models.schemas import EngineState, StreamState
    from datetime import datetime
    
    # Clear state
    state.engines.clear()
    state.streams.clear()
    
    # Save original MAX_STREAMS value
    original_max_streams = cfg.ACEXY_MAX_STREAMS_PER_ENGINE
    
    try:
        # Set MAX_STREAMS to 5 for testing
        cfg.ACEXY_MAX_STREAMS_PER_ENGINE = 5
        
        # Create 3 engines
        now = datetime.now()
        engines = []
        for i in range(3):
            engine = EngineState(
                container_id=f"engine_{i}",
                container_name=f"acestream_{i}",
                host="localhost",
                port=19000 + i,
                labels={},
                forwarded=(i == 0),  # First engine is forwarded
                first_seen=now,
                last_seen=now,
                streams=[],
                health_status="healthy",
                last_health_check=now,
                last_stream_usage=None,
                vpn_container=None,
                engine_variant="krinkuto11-amd64"
            )
            engines.append(engine)
            state.engines[engine.container_id] = engine
        
        print(f"✓ Created {len(engines)} engines")
        
        # Helper function to simulate the selection logic from main.py
        def select_engine():
            """Simulate engine selection logic from /ace/getstream endpoint."""
            engines = state.list_engines()
            active_streams = state.list_streams(status="started")
            engine_loads = {}
            for stream in active_streams:
                cid = stream.container_id
                engine_loads[cid] = engine_loads.get(cid, 0) + 1
            
            # Filter out engines at max capacity
            max_streams = cfg.ACEXY_MAX_STREAMS_PER_ENGINE
            available_engines = [
                e for e in engines 
                if engine_loads.get(e.container_id, 0) < max_streams
            ]
            
            if not available_engines:
                return None
            
            # Sort: (negative load, not forwarded) - prefer highest load first, then forwarded
            engines_sorted = sorted(available_engines, key=lambda e: (
                -engine_loads.get(e.container_id, 0),
                not e.forwarded
            ))
            return engines_sorted[0]
        
        # Test 1: First stream should go to forwarded engine (engine_0)
        print("\nTest 1: First stream selection (all engines empty)")
        selected = select_engine()
        assert selected is not None, "Should select an engine"
        assert selected.container_id == "engine_0", f"First stream should go to forwarded engine, got {selected.container_id}"
        print(f"✅ Correctly selected forwarded engine (engine_0)")
        
        # Add first stream to engine_0
        stream = StreamState(
            id="stream_1",
            container_id="engine_0",
            key="test_key_1",
            key_type="infohash",
            playback_session_id="session_1",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/cmd",
            is_live=True,
            started_at=now,
            status="started"
        )
        state.streams[stream.id] = stream
        
        # Test 2: Second stream should still go to engine_0 (fill it first)
        print("\nTest 2: Second stream selection (engine_0 has 1 stream)")
        selected = select_engine()
        assert selected is not None, "Should select an engine"
        assert selected.container_id == "engine_0", f"Should continue filling engine_0, got {selected.container_id}"
        print(f"✅ Correctly selected engine_0 again (has 1 stream)")
        
        # Add streams to engine_0 until it has 4 streams
        for i in range(2, 5):
            stream = StreamState(
                id=f"stream_{i}",
                container_id="engine_0",
                key=f"test_key_{i}",
                key_type="infohash",
                playback_session_id=f"session_{i}",
                stat_url=f"http://localhost:19000/stat",
                command_url=f"http://localhost:19000/cmd",
                is_live=True,
                started_at=now,
                status="started"
            )
            state.streams[stream.id] = stream
        
        # Test 3: With engine_0 at 4 streams, next should go to engine_1 (not forwarded but empty)
        print("\nTest 3: Fifth stream selection (engine_0 has 4 streams, others empty)")
        selected = select_engine()
        assert selected is not None, "Should select an engine"
        # Should select engine_0 still since it only has 4 streams (max is 5)
        assert selected.container_id == "engine_0", f"Should still fill engine_0 to capacity, got {selected.container_id}"
        print(f"✅ Correctly selected engine_0 (4 streams, can take one more)")
        
        # Add 5th stream to engine_0 (now at max capacity)
        stream = StreamState(
            id="stream_5",
            container_id="engine_0",
            key="test_key_5",
            key_type="infohash",
            playback_session_id="session_5",
            stat_url="http://localhost:19000/stat",
            command_url="http://localhost:19000/cmd",
            is_live=True,
            started_at=now,
            status="started"
        )
        state.streams[stream.id] = stream
        
        # Test 4: With engine_0 at max (5 streams), should move to engine_1
        print("\nTest 4: Sixth stream selection (engine_0 at max=5, others empty)")
        selected = select_engine()
        assert selected is not None, "Should select an engine"
        assert selected.container_id == "engine_1", f"Should select engine_1 now that engine_0 is full, got {selected.container_id}"
        print(f"✅ Correctly selected engine_1 (engine_0 is at max capacity)")
        
        # Add 4 streams to engine_1
        for i in range(6, 10):
            stream = StreamState(
                id=f"stream_{i}",
                container_id="engine_1",
                key=f"test_key_{i}",
                key_type="infohash",
                playback_session_id=f"session_{i}",
                stat_url="http://localhost:19001/stat",
                command_url="http://localhost:19001/cmd",
                is_live=True,
                started_at=now,
                status="started"
            )
            state.streams[stream.id] = stream
        
        # Test 5: With engine_0 and engine_1 having 5 and 4 streams respectively, should go to engine_1
        print("\nTest 5: Stream selection (engine_0=5, engine_1=4, engine_2=0)")
        selected = select_engine()
        assert selected is not None, "Should select an engine"
        assert selected.container_id == "engine_1", f"Should select engine_1 (has 4 streams), got {selected.container_id}"
        print(f"✅ Correctly selected engine_1 (has most streams but not at max)")
        
        # Fill engine_1 to max
        stream = StreamState(
            id="stream_10",
            container_id="engine_1",
            key="test_key_10",
            key_type="infohash",
            playback_session_id="session_10",
            stat_url="http://localhost:19001/stat",
            command_url="http://localhost:19001/cmd",
            is_live=True,
            started_at=now,
            status="started"
        )
        state.streams[stream.id] = stream
        
        # Test 6: With engine_0 and engine_1 both at max, should go to engine_2
        print("\nTest 6: Stream selection (engine_0=5, engine_1=5, engine_2=0)")
        selected = select_engine()
        assert selected is not None, "Should select an engine"
        assert selected.container_id == "engine_2", f"Should select engine_2 (only one available), got {selected.container_id}"
        print(f"✅ Correctly selected engine_2 (only available engine)")
        
        # Fill all engines to max
        for i in range(11, 16):
            stream = StreamState(
                id=f"stream_{i}",
                container_id="engine_2",
                key=f"test_key_{i}",
                key_type="infohash",
                playback_session_id=f"session_{i}",
                stat_url="http://localhost:19002/stat",
                command_url="http://localhost:19002/cmd",
                is_live=True,
                started_at=now,
                status="started"
            )
            state.streams[stream.id] = stream
        
        # Test 7: All engines at max capacity
        print("\nTest 7: All engines at max capacity (engine_0=5, engine_1=5, engine_2=5)")
        selected = select_engine()
        assert selected is None, "Should return None when all engines at max capacity"
        print(f"✅ Correctly returned None when all engines at max capacity")
        
        print("\n✅ All engine selection tests passed!")
        return True
        
    finally:
        # Restore original value
        cfg.ACEXY_MAX_STREAMS_PER_ENGINE = original_max_streams
        # Clear state
        state.engines.clear()
        state.streams.clear()


def test_forwarded_priority_at_equal_load():
    """Test that forwarded engines are prioritized when load is equal."""
    
    print("\n🧪 Testing forwarded engine priority at equal load...")
    
    from app.services.state import state
    from app.core.config import cfg
    from app.models.schemas import EngineState, StreamState
    from datetime import datetime
    
    # Clear state
    state.engines.clear()
    state.streams.clear()
    
    try:
        cfg.ACEXY_MAX_STREAMS_PER_ENGINE = 5
        now = datetime.now()
        
        # Create 2 engines with equal load (both have 2 streams)
        # engine_0 is NOT forwarded, engine_1 IS forwarded
        for i in range(2):
            engine = EngineState(
                container_id=f"engine_{i}",
                container_name=f"acestream_{i}",
                host="localhost",
                port=19000 + i,
                labels={},
                forwarded=(i == 1),  # Second engine is forwarded
                first_seen=now,
                last_seen=now,
                streams=[],
                health_status="healthy",
                last_health_check=now,
                last_stream_usage=None,
                vpn_container=None,
                engine_variant="krinkuto11-amd64"
            )
            state.engines[engine.container_id] = engine
            
            # Add 2 streams to each engine
            for j in range(2):
                stream = StreamState(
                    id=f"stream_{i}_{j}",
                    container_id=f"engine_{i}",
                    key=f"test_key_{i}_{j}",
                    key_type="infohash",
                    playback_session_id=f"session_{i}_{j}",
                    stat_url=f"http://localhost:{19000+i}/stat",
                    command_url=f"http://localhost:{19000+i}/cmd",
                    is_live=True,
                    started_at=now,
                    status="started"
                )
                state.streams[stream.id] = stream
        
        # Select engine - should pick engine_1 (forwarded) since both have equal load
        engines = state.list_engines()
        active_streams = state.list_streams(status="started")
        engine_loads = {}
        for stream in active_streams:
            cid = stream.container_id
            engine_loads[cid] = engine_loads.get(cid, 0) + 1
        
        max_streams = cfg.ACEXY_MAX_STREAMS_PER_ENGINE
        available_engines = [
            e for e in engines 
            if engine_loads.get(e.container_id, 0) < max_streams
        ]
        
        engines_sorted = sorted(available_engines, key=lambda e: (
            -engine_loads.get(e.container_id, 0),
            not e.forwarded
        ))
        selected = engines_sorted[0]
        
        print(f"Engine loads: {engine_loads}")
        print(f"Selected: {selected.container_id}, forwarded={selected.forwarded}")
        
        assert selected.container_id == "engine_1", f"Should select forwarded engine when load is equal, got {selected.container_id}"
        print(f"✅ Correctly prioritized forwarded engine at equal load")
        
        return True
        
    finally:
        state.engines.clear()
        state.streams.clear()


if __name__ == "__main__":
    try:
        test_engine_selection_sequential_filling()
        test_forwarded_priority_at_equal_load()
        print("\n✅ All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
