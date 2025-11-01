#!/usr/bin/env python3
"""
Test to verify that the /engines endpoint returns engines in sorted order by port.
This ensures consistent, predictable ordering for UI display.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_engines_sorted_by_port():
    """Test that /engines endpoint returns engines sorted by port number."""
    print("\n🧪 Testing /engines endpoint sorting...")
    
    from app.services.state import state as global_state
    from app.models.schemas import EngineState
    from app.main import app
    from fastapi.testclient import TestClient
    
    # Clear state first
    global_state.clear_state()
    
    # Create test client
    client = TestClient(app)
    
    with patch('app.services.state.SessionLocal'):
        # Add engines in random order with different ports
        test_engines = [
            ("engine_5", "acestream-5", 19004),
            ("engine_2", "acestream-2", 19001),
            ("engine_10", "acestream-10", 19009),
            ("engine_1", "acestream-1", 19000),
            ("engine_7", "acestream-7", 19006),
            ("engine_3", "acestream-3", 19002),
        ]
        
        for container_id, container_name, port in test_engines:
            engine = EngineState(
                container_id=container_id,
                container_name=container_name,
                host="gluetun",
                port=port,
                labels={},
                forwarded=False,
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                streams=[],
                health_status="healthy"
            )
            global_state.engines[container_id] = engine
            print(f"  Added {container_name} (port {port})")
        
        # Call the /engines endpoint
        response = client.get("/engines")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        engines_from_api = response.json()
        assert len(engines_from_api) == 6, f"Expected 6 engines, got {len(engines_from_api)}"
        
        # Verify engines are sorted by port
        ports = [e["port"] for e in engines_from_api]
        expected_ports = sorted(ports)
        
        assert ports == expected_ports, f"Engines not sorted by port. Got {ports}, expected {expected_ports}"
        print(f"✅ Engines are sorted by port: {ports}")
        
        # Verify the actual order matches expectations
        expected_names = ["acestream-1", "acestream-2", "acestream-3", "acestream-5", "acestream-7", "acestream-10"]
        actual_names = [e["container_name"] for e in engines_from_api]
        
        assert actual_names == expected_names, f"Names not in expected order. Got {actual_names}, expected {expected_names}"
        print(f"✅ Engine names in correct order: {actual_names}")
    
    print("\n✅ Engine sorting test passed!")
    return True


def test_engines_sorted_with_many_engines():
    """Test sorting with 10+ engines to verify proper ordering."""
    print("\n🧪 Testing /engines sorting with 10 engines...")
    
    from app.services.state import state as global_state
    from app.models.schemas import EngineState
    from app.main import app
    from fastapi.testclient import TestClient
    
    # Clear state first
    global_state.clear_state()
    
    # Create test client
    client = TestClient(app)
    
    with patch('app.services.state.SessionLocal'):
        # Add 10 engines in reverse order (simulating the problem statement)
        for i in range(10, 0, -1):
            engine = EngineState(
                container_id=f"container_{i}",
                container_name=f"acestream-{i}",
                host="gluetun",
                port=19000 + i - 1,
                labels={},
                forwarded=(i == 1),
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                streams=[],
                health_status="healthy"
            )
            global_state.engines[f"container_{i}"] = engine
        
        # Call the /engines endpoint
        response = client.get("/engines")
        assert response.status_code == 200
        
        engines_from_api = response.json()
        assert len(engines_from_api) == 10, f"Expected 10 engines, got {len(engines_from_api)}"
        
        # Verify ports are in ascending order
        ports = [e["port"] for e in engines_from_api]
        expected_ports = list(range(19000, 19010))
        
        assert ports == expected_ports, f"Ports not in order. Got {ports}, expected {expected_ports}"
        print(f"✅ All 10 engines sorted correctly by port")
        
        # Verify names are in natural order (1 through 10, not 1, 10, 2, ...)
        names = [e["container_name"] for e in engines_from_api]
        expected_names = [f"acestream-{i}" for i in range(1, 11)]
        
        assert names == expected_names, f"Names not in natural order. Got {names}, expected {expected_names}"
        print(f"✅ Engine names in natural order: acestream-1 through acestream-10")
        
        # Verify the forwarded engine is in the correct position
        forwarded_engines = [e for e in engines_from_api if e["forwarded"]]
        assert len(forwarded_engines) == 1, f"Expected 1 forwarded engine, got {len(forwarded_engines)}"
        assert forwarded_engines[0]["container_name"] == "acestream-1", "acestream-1 should be forwarded"
        assert forwarded_engines[0]["port"] == 19000, "Forwarded engine should have port 19000"
        print(f"✅ Forwarded engine (acestream-1) is in correct position")
    
    print("\n✅ Test with 10 engines passed!")
    return True


if __name__ == "__main__":
    print("🔧 Testing Engine Ordering Fix")
    print("=" * 70)
    
    try:
        test_engines_sorted_by_port()
        test_engines_sorted_with_many_engines()
        
        print("\n" + "=" * 70)
        print("🎉 All engine ordering tests passed!")
        print("\n✅ FIX VERIFIED: /engines endpoint now returns engines sorted by port")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
