#!/usr/bin/env python3
"""
Test to verify that MIN_REPLICAS maintains minimum EMPTY/FREE replicas,
not just total replicas.

The issue: If there are 10 active replicas with streams, and MIN_REPLICAS=1,
there should be an 11th empty replica available for the next request.
"""

import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_min_empty_replicas_logic():
    """Test that MIN_REPLICAS ensures minimum empty replicas are available."""
    
    print("\n🧪 Testing MIN_REPLICAS for empty replicas...")
    
    try:
        from app.services.autoscaler import ensure_minimum
        from app.services.state import state
        from app.models.schemas import EngineState, StreamState
        from app.core.config import cfg
        from app.services import health
        from datetime import datetime, timezone
        
        # Clear state
        state.engines.clear()
        state.streams.clear()
        
        # Mock Docker containers
        from app.services import autoscaler
        original_list_managed = autoscaler.list_managed
        
        class MockContainer:
            def __init__(self, container_id, status="running"):
                self.id = container_id
                self.status = status
        
        mock_containers = []
        
        def mock_list_managed():
            return mock_containers
        
        autoscaler.list_managed = mock_list_managed
        
        # Mock provisioner to track provisions
        from app.services import provisioner
        original_start = provisioner.start_acestream
        provisions_made = []
        
        def mock_start_acestream(request):
            container_id = f"new_engine_{len(provisions_made)}"
            
            # Create the engine in state
            engine = EngineState(
                container_id=container_id,
                container_name=f"new-engine-{len(provisions_made)}",
                host="127.0.0.1",
                port=8080 + len(provisions_made),
                labels={},
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                streams=[]
            )
            state.engines[container_id] = engine
            
            # Add to mock containers
            mock_containers.append(MockContainer(container_id))
            
            provisions_made.append(container_id)
            
            # Return mock response
            class MockResponse:
                def __init__(self, cid):
                    self.container_id = cid
                    self.host_http_port = 8080
            
            return MockResponse(container_id)
        
        provisioner.start_acestream = mock_start_acestream
        
        # Set MIN_REPLICAS for testing
        original_min = cfg.MIN_REPLICAS
        cfg.MIN_REPLICAS = 2
        
        try:
            # Scenario 1: No engines exist, MIN_REPLICAS=2
            # Should provision 2 empty engines
            print("\n📋 Scenario 1: No engines, MIN_REPLICAS=2")
            print(f"   Before: 0 engines, 0 used, 0 free")
            provisions_made.clear()
            
            # Mock circuit breaker to allow provisioning
            from app.services import circuit_breaker
            original_can_provision = circuit_breaker.circuit_breaker_manager.can_provision
            circuit_breaker.circuit_breaker_manager.can_provision = lambda x: True
            
            try:
                ensure_minimum()
            finally:
                circuit_breaker.circuit_breaker_manager.can_provision = original_can_provision
            
            print(f"   After: {len(state.engines)} engines, 0 used, {len(state.engines)} free")
            print(f"   Provisions made: {len(provisions_made)}")
            assert len(provisions_made) == 2, f"Expected 2 provisions, got {len(provisions_made)}"
            print("✅ Provisioned 2 empty engines as expected")
            
            # Scenario 2: 3 engines exist, 1 has an active stream, MIN_REPLICAS=2
            # There are 2 free engines, so no provisioning should happen
            print("\n📋 Scenario 2: 3 engines, 1 used, 2 free, MIN_REPLICAS=2")
            
            # Add a third engine manually
            container_id = "manual_engine_3"
            engine = EngineState(
                container_id=container_id,
                container_name="manual-engine-3",
                host="127.0.0.1",
                port=8083,
                labels={},
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                streams=[]
            )
            state.engines[container_id] = engine
            mock_containers.append(MockContainer(container_id))
            
            # Add a stream to the first engine
            stream = StreamState(
                id="test_stream_1",
                key_type="content_id",
                key="12345",
                container_id="new_engine_0",
                playback_session_id="session_1",
                stat_url="http://127.0.0.1:8080/stat",
                command_url="http://127.0.0.1:8080/cmd",
                is_live=True,
                started_at=datetime.now(timezone.utc),
                status="started"
            )
            state.streams["test_stream_1"] = stream
            
            print(f"   Before: {len(state.engines)} engines, 1 used, 2 free")
            provisions_made.clear()
            
            ensure_minimum()
            
            print(f"   After: {len(state.engines)} engines")
            print(f"   Provisions made: {len(provisions_made)}")
            assert len(provisions_made) == 0, f"Expected 0 provisions (already have 2 free), got {len(provisions_made)}"
            print("✅ No provisioning when 2 free engines already exist")
            
            # Scenario 3: 3 engines exist, 2 have active streams, MIN_REPLICAS=2
            # There is only 1 free engine, so should provision 1 more
            print("\n📋 Scenario 3: 3 engines, 2 used, 1 free, MIN_REPLICAS=2")
            
            # Add a second stream to a different engine
            stream2 = StreamState(
                id="test_stream_2",
                key_type="content_id",
                key="67890",
                container_id="new_engine_1",
                playback_session_id="session_2",
                stat_url="http://127.0.0.1:8081/stat",
                command_url="http://127.0.0.1:8081/cmd",
                is_live=True,
                started_at=datetime.now(timezone.utc),
                status="started"
            )
            state.streams["test_stream_2"] = stream2
            
            print(f"   Before: {len(state.engines)} engines, 2 used, 1 free")
            provisions_made.clear()
            
            ensure_minimum()
            
            print(f"   After: {len(state.engines)} engines")
            print(f"   Provisions made: {len(provisions_made)}")
            assert len(provisions_made) == 1, f"Expected 1 provision (need 1 more free), got {len(provisions_made)}"
            print("✅ Provisioned 1 additional engine to maintain 2 free")
            
            # Scenario 4: 10 engines, all 10 have active streams, MIN_REPLICAS=1
            # Should provision 1 more to have at least 1 empty
            print("\n📋 Scenario 4: 10 engines all used, MIN_REPLICAS=1")
            
            # Clear and set up 10 engines with streams
            state.engines.clear()
            state.streams.clear()
            mock_containers.clear()
            
            for i in range(10):
                container_id = f"busy_engine_{i}"
                engine = EngineState(
                    container_id=container_id,
                    container_name=f"busy-engine-{i}",
                    host="127.0.0.1",
                    port=9000 + i,
                    labels={},
                    first_seen=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                    streams=[]
                )
                state.engines[container_id] = engine
                mock_containers.append(MockContainer(container_id))
                
                # Add stream to each engine
                stream = StreamState(
                    id=f"stream_{i}",
                    key_type="content_id",
                    key=f"content_{i}",
                    container_id=container_id,
                    playback_session_id=f"session_{i}",
                    stat_url=f"http://127.0.0.1:{9000+i}/stat",
                    command_url=f"http://127.0.0.1:{9000+i}/cmd",
                    is_live=True,
                    started_at=datetime.now(timezone.utc),
                    status="started"
                )
                state.streams[f"stream_{i}"] = stream
            
            cfg.MIN_REPLICAS = 1
            print(f"   Before: {len(state.engines)} engines, 10 used, 0 free")
            provisions_made.clear()
            
            ensure_minimum()
            
            print(f"   After: {len(state.engines)} engines")
            print(f"   Provisions made: {len(provisions_made)}")
            assert len(provisions_made) == 1, f"Expected 1 provision (need 1 free), got {len(provisions_made)}"
            print("✅ Provisioned 1 engine to ensure 1 empty replica available")
            
            print("\n🎯 All tests PASSED: MIN_REPLICAS correctly maintains minimum empty replicas")
            return True
            
        finally:
            # Restore original values
            cfg.MIN_REPLICAS = original_min
            autoscaler.list_managed = original_list_managed
            provisioner.start_acestream = original_start
        
    except Exception as e:
        print(f"\n💥 Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    try:
        success = test_min_empty_replicas_logic()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
