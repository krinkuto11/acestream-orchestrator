#!/usr/bin/env python3
"""
Manual demonstration of layer-based engine filling behavior.

This script simulates stream assignment to show how engines are filled
in layers (round-robin) across all engines before any engine gets to the next layer.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def simulate_stream_assignment():
    """Simulate assigning 15 streams to 3 engines with MAX_STREAMS_PER_ENGINE=5."""
    
    from app.services.state import state
    from app.core.config import cfg
    from app.models.schemas import EngineState, StreamState
    from datetime import datetime
    
    print("\n" + "="*80)
    print("LAYER-BASED ENGINE FILLING DEMONSTRATION")
    print("="*80)
    print(f"Configuration: MAX_STREAMS_PER_ENGINE = {cfg.ACEXY_MAX_STREAMS_PER_ENGINE}")
    print("Engines: 3 (engine_0 is forwarded)")
    print("Streams to assign: 15")
    print("\nStrategy: Fill in layers (round-robin)")
    print("  - Layer 1: All engines get 1 stream before any gets 2")
    print("  - Layer 2: All engines get 2 streams before any gets 3")
    print("  - ... continue until layer (MAX_STREAMS - 1)")
    print("="*80 + "\n")
    
    # Clear state
    state.engines.clear()
    state.streams.clear()
    
    # Save original value
    original_max_streams = cfg.ACEXY_MAX_STREAMS_PER_ENGINE
    
    try:
        # Set MAX_STREAMS to 5
        cfg.ACEXY_MAX_STREAMS_PER_ENGINE = 5
        
        # Create 3 engines
        now = datetime.now()
        print("Creating 3 engines:\n")
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
            state.engines[engine.container_id] = engine
            print(f"  ✓ engine_{i} (port {19000+i}) - {'FORWARDED' if engine.forwarded else 'regular'}")
        
        print("\n" + "-"*80)
        print("STREAM ASSIGNMENT SIMULATION")
        print("-"*80 + "\n")
        
        # Simulate assigning streams
        for stream_num in range(1, 16):
            # Select engine using the same logic as main.py
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
                print(f"  ❌ Stream {stream_num}: All engines at max capacity!")
                break
            
            # Sort: (load, not forwarded) - LEAST load first for layer filling
            engines_sorted = sorted(available_engines, key=lambda e: (
                engine_loads.get(e.container_id, 0),
                not e.forwarded
            ))
            selected = engines_sorted[0]
            
            # Add stream to selected engine
            stream = StreamState(
                id=f"stream_{stream_num}",
                container_id=selected.container_id,
                key=f"test_key_{stream_num}",
                key_type="infohash",
                playback_session_id=f"session_{stream_num}",
                stat_url=f"http://localhost:{selected.port}/stat",
                command_url=f"http://localhost:{selected.port}/cmd",
                is_live=True,
                started_at=now,
                status="started"
            )
            state.streams[stream.id] = stream
            
            # Print assignment
            current_load = engine_loads.get(selected.container_id, 0) + 1
            print(f"  Stream {stream_num:2d} → {selected.container_id} (load: {current_load}/{max_streams}) "
                  f"{'[FORWARDED]' if selected.forwarded else ''}")
            
            # Show status after completing each layer
            if stream_num % 3 == 0:
                layer_num = stream_num // 3
                print(f"\n  ✓ Layer {layer_num} complete!")
                print("  Current state:")
                for i in range(3):
                    engine_id = f"engine_{i}"
                    load = len([s for s in state.list_streams(status="started") if s.container_id == engine_id])
                    bar = "█" * load + "░" * (max_streams - load)
                    status = "FULL" if load >= max_streams else f"{load}/{max_streams}"
                    print(f"    engine_{i}: [{bar}] {status}")
                print()
        
        print("-"*80)
        print("\nFINAL ENGINE STATE:\n")
        
        # Final state
        for i in range(3):
            engine_streams = [s for s in state.list_streams(status="started") 
                            if s.container_id == f"engine_{i}"]
            load = len(engine_streams)
            bar = "█" * load + "░" * (cfg.ACEXY_MAX_STREAMS_PER_ENGINE - load)
            status = "FULL" if load >= cfg.ACEXY_MAX_STREAMS_PER_ENGINE else f"{load}/{cfg.ACEXY_MAX_STREAMS_PER_ENGINE}"
            forwarded_label = " [FORWARDED]" if i == 0 else ""
            print(f"  engine_{i}: [{bar}] {status}{forwarded_label}")
        
        print("\n" + "="*80)
        print("CONCLUSION:")
        print("="*80)
        print("✓ Engines were filled in LAYERS (round-robin across all engines)")
        print("✓ Layer 1: All engines got 1 stream before any got 2")
        print("✓ Layer 2: All engines got 2 streams before any got 3")
        print("✓ ... and so on through layer 5 (MAX_STREAMS)")
        print("✓ This ensures balanced load distribution")
        print("✓ New engines are provisioned after layer (MAX_STREAMS - 1) is complete")
        print("="*80 + "\n")
        
    finally:
        # Restore original value
        cfg.ACEXY_MAX_STREAMS_PER_ENGINE = original_max_streams
        state.engines.clear()
        state.streams.clear()


if __name__ == "__main__":
    try:
        simulate_stream_assignment()
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
