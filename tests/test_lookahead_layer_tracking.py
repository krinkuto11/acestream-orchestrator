#!/usr/bin/env python3
"""
Test to verify improved lookahead provisioning with layer tracking.

Scenario (with MAX_STREAMS_PER_ENGINE=5):
- Start with 5 engines (at MAX_REPLICAS limit)
- All 5 engines reach layer 3 (3 streams each)
- One engine gets a 4th stream → provisions 6th engine (lookahead trigger)
- Until the 6th engine reaches layer 3, lookahead should NOT trigger again
- Only when ALL engines (including the 6th) reach layer 3, provision 7th engine
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_lookahead_layer_tracking():
    """Test that lookahead provisioning uses layer tracking to prevent repeated triggers."""
    
    print("\n🧪 Testing improved lookahead provisioning with layer tracking...")
    
    from app.services.state import state
    from app.core.config import cfg
    from app.models.schemas import EngineState, StreamState
    from datetime import datetime
    
    # Clear state
    state.engines.clear()
    state.streams.clear()
    state.reset_lookahead_layer()
    
    # Save original values
    original_max_streams = cfg.ACEXY_MAX_STREAMS_PER_ENGINE
    original_max_replicas = cfg.MAX_REPLICAS
    original_min_free = cfg.MIN_FREE_REPLICAS
    
    try:
        # Set test configuration
        cfg.ACEXY_MAX_STREAMS_PER_ENGINE = 5
        cfg.MAX_REPLICAS = 10  # High enough to not block provisioning
        cfg.MIN_FREE_REPLICAS = 0  # Disable free replica requirement for this test
        
        now = datetime.now()
        
        print(f"\n📋 Configuration: MAX_STREAMS={cfg.ACEXY_MAX_STREAMS_PER_ENGINE}, MAX_REPLICAS={cfg.MAX_REPLICAS}")
        
        # Helper to create engines
        def create_engine(index):
            engine = EngineState(
                container_id=f"engine_{index}",
                container_name=f"acestream_{index}",
                host="localhost",
                port=19000 + index,
                labels={},
                forwarded=(index == 0),
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
            return engine
        
        # Helper to add streams to an engine
        def add_streams_to_engine(engine_id, count):
            existing = len([s for s in state.streams.values() if s.container_id == engine_id and s.status == "started"])
            for i in range(count):
                stream = StreamState(
                    id=f"stream_{engine_id}_{existing + i}",
                    container_id=engine_id,
                    key=f"test_key_{engine_id}_{existing + i}",
                    key_type="infohash",
                    playback_session_id=f"session_{engine_id}_{existing + i}",
                    stat_url=f"http://localhost/stat",
                    command_url=f"http://localhost/cmd",
                    is_live=True,
                    started_at=now,
                    status="started"
                )
                state.streams[stream.id] = stream
        
        # Helper to get stream counts per engine
        def get_engine_loads():
            loads = {}
            for engine in state.list_engines():
                count = len([s for s in state.list_streams(status="started") if s.container_id == engine.container_id])
                loads[engine.container_id] = count
            return loads
        
        # Helper to check if lookahead should trigger
        def should_trigger_lookahead():
            """Simulate the lookahead logic from autoscaler.py"""
            all_engines = state.list_engines()
            if not all_engines:
                return False
            
            # Get stream counts
            engines_with_stream_counts = []
            for engine in all_engines:
                stream_count = len(state.list_streams(status="started", container_id=engine.container_id))
                engines_with_stream_counts.append((engine.container_id, stream_count))
            
            stream_counts = [count for _, count in engines_with_stream_counts]
            min_streams = min(stream_counts)
            
            # Check threshold
            max_streams_threshold = cfg.ACEXY_MAX_STREAMS_PER_ENGINE - 1
            any_engine_near_capacity = any(count >= max_streams_threshold for _, count in engines_with_stream_counts)
            
            if not any_engine_near_capacity:
                return False
            
            # Check lookahead layer
            lookahead_layer = state.get_lookahead_layer()
            all_at_lookahead_layer = lookahead_layer is None or min_streams >= lookahead_layer
            
            return all_at_lookahead_layer
        
        # ========== SCENARIO 1: Initial setup with 5 engines at layer 3 ==========
        print("\n=== Scenario 1: Create 5 engines and fill to layer 3 ===")
        for i in range(5):
            create_engine(i)
        
        # Fill all engines to layer 3 (3 streams each)
        for i in range(5):
            add_streams_to_engine(f"engine_{i}", 3)
        
        loads = get_engine_loads()
        print(f"✓ Created 5 engines with 3 streams each: {loads}")
        assert all(count == 3 for count in loads.values()), "All engines should have 3 streams"
        assert state.get_lookahead_layer() is None, "Lookahead layer should not be set yet"
        
        # Check if lookahead should trigger (it shouldn't yet - threshold is 4)
        should_trigger = should_trigger_lookahead()
        print(f"  Lookahead should trigger: {should_trigger} (threshold is {cfg.ACEXY_MAX_STREAMS_PER_ENGINE - 1})")
        assert not should_trigger, "Lookahead should NOT trigger at layer 3"
        
        # ========== SCENARIO 2: One engine reaches layer 4 - should trigger lookahead ==========
        print("\n=== Scenario 2: One engine reaches layer 4 (threshold) ===")
        add_streams_to_engine("engine_0", 1)  # engine_0 now has 4 streams
        
        loads = get_engine_loads()
        print(f"✓ Engine loads: {loads}")
        
        should_trigger = should_trigger_lookahead()
        print(f"  Lookahead should trigger: {should_trigger}")
        assert should_trigger, "Lookahead SHOULD trigger when first engine reaches threshold"
        
        # Simulate provisioning - set lookahead layer to current minimum (3)
        min_streams = min(loads.values())
        state.set_lookahead_layer(min_streams)
        print(f"✓ Provisioned new engine, set lookahead layer to {min_streams}")
        
        # ========== SCENARIO 3: Create 6th engine but keep it at layer 0 ==========
        print("\n=== Scenario 3: Add 6th engine (empty, layer 0) ===")
        create_engine(5)  # Don't add streams yet
        
        loads = get_engine_loads()
        print(f"✓ Engine loads: {loads}")
        assert loads["engine_5"] == 0, "New engine should have 0 streams"
        
        # Check if lookahead should trigger again
        should_trigger = should_trigger_lookahead()
        print(f"  Lookahead should trigger: {should_trigger}")
        print(f"  Lookahead layer: {state.get_lookahead_layer()}")
        print(f"  Min streams: {min(loads.values())}")
        assert not should_trigger, "Lookahead should NOT trigger - new engine hasn't reached layer 3 yet"
        
        # ========== SCENARIO 4: Another engine reaches layer 4 - still blocked ==========
        print("\n=== Scenario 4: Another engine reaches layer 4 ===")
        add_streams_to_engine("engine_1", 1)  # engine_1 now has 4 streams
        
        loads = get_engine_loads()
        print(f"✓ Engine loads: {loads}")
        
        should_trigger = should_trigger_lookahead()
        print(f"  Lookahead should trigger: {should_trigger}")
        print(f"  Lookahead layer: {state.get_lookahead_layer()}")
        assert not should_trigger, "Lookahead should STILL be blocked - engine_5 hasn't reached layer 3"
        
        # ========== SCENARIO 5: Fill 6th engine to layer 3 - should allow lookahead again ==========
        print("\n=== Scenario 5: Fill 6th engine to layer 3 ===")
        add_streams_to_engine("engine_5", 3)  # engine_5 now has 3 streams
        
        loads = get_engine_loads()
        print(f"✓ Engine loads: {loads}")
        
        should_trigger = should_trigger_lookahead()
        print(f"  Lookahead should trigger: {should_trigger}")
        print(f"  Min streams: {min(loads.values())}, Lookahead layer: {state.get_lookahead_layer()}")
        assert should_trigger, "Lookahead SHOULD trigger now - all engines have reached layer 3"
        
        # Simulate provisioning 7th engine
        min_streams = min(loads.values())
        state.set_lookahead_layer(min_streams)
        print(f"✓ Would provision 7th engine, set lookahead layer to {min_streams}")
        
        print("\n✅ All lookahead layer tracking tests passed!")
        print("\n📊 Summary:")
        print("  1. Lookahead triggered when first engine reached layer 4")
        print("  2. Provisioned 6th engine, set lookahead layer to 3")
        print("  3. Lookahead blocked until ALL engines (including 6th) reached layer 3")
        print("  4. Once all at layer 3, lookahead can trigger again")
        
        return True
        
    finally:
        # Restore original values
        cfg.ACEXY_MAX_STREAMS_PER_ENGINE = original_max_streams
        cfg.MAX_REPLICAS = original_max_replicas
        cfg.MIN_FREE_REPLICAS = original_min_free
        state.engines.clear()
        state.streams.clear()
        state.reset_lookahead_layer()


if __name__ == "__main__":
    try:
        test_lookahead_layer_tracking()
        print("\n✅ Test completed successfully!")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
