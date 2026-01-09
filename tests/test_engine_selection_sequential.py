#!/usr/bin/env python3
"""
Test to verify that engine selection fills engines in layers (round-robin).

When MAX_STREAMS_PER_ENGINE=5:
- Layer 1: All engines get 1 stream before any gets 2
- Layer 2: All engines get 2 streams before any gets 3
- Continue until layer 4 (MAX-1) is complete
- Then provision new engine
- Priority: 1. Engine with LEAST streams (not at max), 2. Forwarded engine
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_engine_selection_layer_filling():
    """Test that engine selection fills engines in layers (round-robin)."""
    
    print("\n🧪 Testing layer-based engine filling behavior...")
    
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
            
            # Sort: (load, not forwarded) - prefer LEAST load first (layer filling), then forwarded
            engines_sorted = sorted(available_engines, key=lambda e: (
                engine_loads.get(e.container_id, 0),  # Ascending order (least streams first)
                not e.forwarded  # Forwarded engines preferred when load is equal
            ))
            return engines_sorted[0]
        
        # Helper to add a stream to an engine
        def add_stream_to_engine(engine_id, stream_num):
            stream = StreamState(
                id=f"stream_{stream_num}",
                container_id=engine_id,
                key=f"test_key_{stream_num}",
                key_type="infohash",
                playback_session_id=f"session_{stream_num}",
                stat_url=f"http://localhost:19000/stat",
                command_url=f"http://localhost:19000/cmd",
                is_live=True,
                started_at=now,
                status="started"
            )
            state.streams[stream.id] = stream
        
        # Test Layer 1: All engines should get 1 stream before any gets 2
        print("\n=== Testing Layer 1 (all engines get 1 stream first) ===")
        
        # Stream 1 should go to engine_0 (forwarded, has 0 streams)
        selected = select_engine()
        assert selected.container_id == "engine_0", f"First stream should go to forwarded engine, got {selected.container_id}"
        add_stream_to_engine(selected.container_id, 1)
        print(f"✓ Stream 1 → engine_0 (forwarded)")
        
        # Stream 2 should go to engine_1 (has 0 streams, engine_0 has 1)
        selected = select_engine()
        assert selected.container_id == "engine_1", f"Second stream should go to engine_1, got {selected.container_id}"
        add_stream_to_engine(selected.container_id, 2)
        print(f"✓ Stream 2 → engine_1 (completing layer 1)")
        
        # Stream 3 should go to engine_2 (has 0 streams, others have 1)
        selected = select_engine()
        assert selected.container_id == "engine_2", f"Third stream should go to engine_2, got {selected.container_id}"
        add_stream_to_engine(selected.container_id, 3)
        print(f"✓ Stream 3 → engine_2 (completing layer 1)")
        
        print("✅ Layer 1 complete: all engines have 1 stream")
        
        # Test Layer 2: All engines should get 2nd stream before any gets 3rd
        print("\n=== Testing Layer 2 (all engines get 2 streams) ===")
        
        # Stream 4 should go to engine_0 (forwarded, all have 1 stream)
        selected = select_engine()
        assert selected.container_id == "engine_0", f"Should go to forwarded engine when all equal, got {selected.container_id}"
        add_stream_to_engine(selected.container_id, 4)
        print(f"✓ Stream 4 → engine_0 (forwarded priority at equal load)")
        
        # Stream 5 should go to engine_1 or engine_2 (both have 1)
        selected = select_engine()
        assert selected.container_id in ["engine_1", "engine_2"], f"Should go to engine with 1 stream, got {selected.container_id}"
        add_stream_to_engine(selected.container_id, 5)
        print(f"✓ Stream 5 → {selected.container_id}")
        
        # Stream 6 should go to the remaining engine with 1 stream
        selected = select_engine()
        active_streams = state.list_streams(status="started")
        engine_loads = {s.container_id: 0 for s in state.list_engines()}
        for stream in active_streams:
            engine_loads[stream.container_id] = engine_loads.get(stream.container_id, 0) + 1
        
        # Should select engine with only 1 stream
        assert engine_loads[selected.container_id] == 1, f"Should select engine with 1 stream, got engine with {engine_loads[selected.container_id]}"
        add_stream_to_engine(selected.container_id, 6)
        print(f"✓ Stream 6 → {selected.container_id} (completing layer 2)")
        
        print("✅ Layer 2 complete: all engines have 2 streams")
        
        # Verify all engines have 2 streams now
        active_streams = state.list_streams(status="started")
        engine_loads = {}
        for stream in active_streams:
            engine_loads[stream.container_id] = engine_loads.get(stream.container_id, 0) + 1
        
        for i in range(3):
            assert engine_loads.get(f"engine_{i}", 0) == 2, f"Engine {i} should have 2 streams, has {engine_loads.get(f'engine_{i}', 0)}"
        
        # Continue filling to layer 4 (MAX_STREAMS - 1)
        print("\n=== Testing Layers 3 and 4 ===")
        stream_num = 7
        for layer in [3, 4]:
            for i in range(3):
                selected = select_engine()
                add_stream_to_engine(selected.container_id, stream_num)
                stream_num += 1
            print(f"✅ Layer {layer} complete: all engines have {layer} streams")
        
        # Verify all engines have 4 streams (MAX - 1)
        active_streams = state.list_streams(status="started")
        engine_loads = {}
        for stream in active_streams:
            engine_loads[stream.container_id] = engine_loads.get(stream.container_id, 0) + 1
        
        for i in range(3):
            actual_load = engine_loads.get(f"engine_{i}", 0)
            assert actual_load == 4, f"Engine {i} should have 4 streams (MAX-1), has {actual_load}"
        
        print("\n✅ All engines at layer 4 (MAX_STREAMS - 1 = 4)")
        print("   Now ready for new engine provisioning when autoscaler runs")
        
        # Test filling to max capacity
        print("\n=== Testing Layer 5 (max capacity) ===")
        for i in range(3):
            selected = select_engine()
            add_stream_to_engine(selected.container_id, stream_num)
            stream_num += 1
        
        # Verify all engines at max
        active_streams = state.list_streams(status="started")
        engine_loads = {}
        for stream in active_streams:
            engine_loads[stream.container_id] = engine_loads.get(stream.container_id, 0) + 1
        
        for i in range(3):
            assert engine_loads.get(f"engine_{i}", 0) == 5, f"Engine {i} should be at max (5), has {engine_loads.get(f'engine_{i}', 0)}"
        
        # Should return None when all at max
        selected = select_engine()
        assert selected is None, "Should return None when all engines at max capacity"
        print("✅ All engines at max capacity (5 streams)")
        
        print("\n✅ All layer-based filling tests passed!")
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
            engine_loads.get(e.container_id, 0),
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
        test_engine_selection_layer_filling()
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
