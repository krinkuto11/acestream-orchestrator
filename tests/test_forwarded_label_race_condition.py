#!/usr/bin/env python3
"""
Test to verify the fix for the forwarded engine label race condition.

This test validates that during sequential provisioning of multiple containers,
only the first container receives the forwarded=true label, and all subsequent
containers receive forwarded=false.

The bug was that all containers were getting forwarded=true because the state
was not updated until after all provisioning completed.
"""

import sys
import os
import traceback
from unittest import mock
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

from app.services.state import State
from app.models.schemas import EngineState


def test_sequential_provisioning_only_first_is_forwarded():
    """
    Test that when provisioning multiple containers sequentially,
    only the first one gets the forwarded label.
    """
    print("\n🧪 Testing sequential provisioning forwarded label assignment")
    
    # Mock database operations to avoid needing actual database
    with mock.patch('app.services.state.SessionLocal'):
        # Create a fresh state
        state = State()
        state.clear_state()
        
        # Simulate provisioning 10 containers
        provisioned_engines = []
        
        for i in range(10):
            container_id = f"engine_{i:02d}"
            
            # This is the logic from start_acestream():
            # Check if there's already a forwarded engine
            is_forwarded = not state.has_forwarded_engine()
            
            # Create engine (simulating what start_acestream does)
            now = datetime.now(timezone.utc)
            engine = EngineState(
                container_id=container_id,
                container_name=f"engine-{i}",
                host="gluetun",
                port=19000 + i,
                labels={"acestream.forwarded": "true" if is_forwarded else "false"},
                forwarded=is_forwarded,
                first_seen=now,
                last_seen=now,
                streams=[],
                health_status="unknown",
                last_health_check=None,
                last_stream_usage=None,
                last_cache_cleanup=None,
                cache_size_bytes=None
            )
            
            # Add to state immediately (this is the fix)
            state.engines[container_id] = engine
            
            # Mark as forwarded if needed
            if is_forwarded:
                state.set_forwarded_engine(container_id)
                print(f"  ✓ Engine {i+1} ({container_id}) marked as forwarded")
            else:
                print(f"  - Engine {i+1} ({container_id}) not forwarded")
            
            provisioned_engines.append(engine)
    
        # Verify results
        print("\n📊 Verification:")
        
        # Count engines with forwarded=true in labels
        forwarded_label_count = sum(
            1 for e in provisioned_engines 
            if e.labels.get("acestream.forwarded") == "true"
        )
        print(f"  Engines with forwarded=true label: {forwarded_label_count}/10")
        
        # Count engines with forwarded=true in state
        forwarded_state_count = sum(
            1 for e in state.engines.values() 
            if e.forwarded
        )
        print(f"  Engines with forwarded=true in state: {forwarded_state_count}/10")
        
        # Get the forwarded engine
        forwarded_engine = state.get_forwarded_engine()
        if forwarded_engine:
            print(f"  Forwarded engine: {forwarded_engine.container_id}")
        
        # Assertions
        assert forwarded_label_count == 1, f"Expected exactly 1 engine with forwarded label, got {forwarded_label_count}"
        assert forwarded_state_count == 1, f"Expected exactly 1 engine with forwarded state, got {forwarded_state_count}"
        assert forwarded_engine is not None, "Expected to find a forwarded engine"
        assert forwarded_engine.container_id == "engine_00", "Expected first engine to be forwarded"
        assert state.has_forwarded_engine(), "Expected has_forwarded_engine() to return True"
        
        print("\n✅ Test passed: Only first engine is marked as forwarded")


def test_reindex_with_multiple_forwarded_labels():
    """
    Test that reindex handles the case where multiple containers have
    forwarded=true labels (e.g., from the bug), and only sets one as forwarded.
    """
    print("\n🧪 Testing reindex with multiple forwarded labels")
    
    with mock.patch('app.services.state.SessionLocal'):
        from app.services.reindex import reindex_existing
        from app.services.state import state
        
        # Create a fresh state
        state.clear_state()
    
        # Mock list_managed to return containers with forwarded labels
        class MockContainer:
            def __init__(self, container_id, has_forwarded_label=False):
                self.id = container_id
                self.status = "running"
                self.name = f"acestream-{container_id[:8]}"
                self.labels = {
                    "acestream.http_port": "19000",
                    "host.http_port": "19000",
                    "acestream.managed": "true"
                }
                if has_forwarded_label:
                    self.labels["acestream.forwarded"] = "true"
                self.attrs = {}
        
        # Simulate the bug scenario: all 10 containers have forwarded=true label
        mock_containers = [
            MockContainer(f"container_{i:02d}", has_forwarded_label=True)
            for i in range(10)
        ]
        
        with mock.patch('app.services.reindex.list_managed', return_value=mock_containers):
            # Run reindex
            reindex_existing()
        
        # Verify only one engine is marked as forwarded
        forwarded_count = sum(1 for e in state.engines.values() if e.forwarded)
        print(f"  Engines with forwarded=true after reindex: {forwarded_count}/10")
        
        forwarded_engine = state.get_forwarded_engine()
        if forwarded_engine:
            print(f"  Forwarded engine: {forwarded_engine.container_id}")
        
        # Assertions
        assert forwarded_count == 1, f"Expected exactly 1 forwarded engine after reindex, got {forwarded_count}"
        assert forwarded_engine is not None, "Expected to find a forwarded engine after reindex"
        
        print("\n✅ Test passed: Reindex correctly handles multiple forwarded labels")


def test_has_forwarded_engine_after_first_provision():
    """
    Test that has_forwarded_engine() returns True immediately after
    the first engine is provisioned.
    """
    print("\n🧪 Testing has_forwarded_engine() after first provision")
    
    with mock.patch('app.services.state.SessionLocal'):
        state = State()
        state.clear_state()
        
        # Verify no forwarded engine initially
        assert not state.has_forwarded_engine(), "Should have no forwarded engine initially"
        print("  ✓ Initially has_forwarded_engine() = False")
        
        # Add first engine
        now = datetime.now(timezone.utc)
        engine1 = EngineState(
            container_id="engine_01",
            container_name="engine-1",
            host="gluetun",
            port=19000,
            labels={"acestream.forwarded": "true"},
            forwarded=True,
            first_seen=now,
            last_seen=now,
            streams=[]
        )
        state.engines["engine_01"] = engine1
        state.set_forwarded_engine("engine_01")
        
        # Verify has_forwarded_engine returns True
        assert state.has_forwarded_engine(), "Should have forwarded engine after first provision"
        print("  ✓ After first engine, has_forwarded_engine() = True")
        
        # Add second engine - should not be forwarded
        engine2 = EngineState(
            container_id="engine_02",
            container_name="engine-2",
            host="gluetun",
            port=19001,
            labels={"acestream.forwarded": "false"},
            forwarded=False,
            first_seen=now,
            last_seen=now,
            streams=[]
        )
        state.engines["engine_02"] = engine2
        
        # Verify still only one forwarded engine
        assert state.has_forwarded_engine(), "Should still have forwarded engine"
        forwarded_engine = state.get_forwarded_engine()
        assert forwarded_engine.container_id == "engine_01", "First engine should remain forwarded"
        print("  ✓ After second engine, first remains forwarded")
        
        print("\n✅ Test passed: has_forwarded_engine() works correctly")


if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v", "-s"])
    else:
        # Run tests manually without pytest
        print("\n" + "=" * 60)
        print("Running tests without pytest")
        print("=" * 60)
        
        try:
            test_sequential_provisioning_only_first_is_forwarded()
            test_reindex_with_multiple_forwarded_labels()
            test_has_forwarded_engine_after_first_provision()
            
            print("\n" + "=" * 60)
            print("✅ ALL TESTS PASSED")
            print("=" * 60)
            sys.exit(0)
        except AssertionError as e:
            print(f"\n❌ TEST FAILED: {e}")
            traceback.print_exc()
            sys.exit(1)
        except Exception as e:
            print(f"\n💥 ERROR: {e}")
            traceback.print_exc()
            sys.exit(1)
