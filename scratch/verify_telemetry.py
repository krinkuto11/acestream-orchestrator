import sys
import os
import time
from unittest.mock import MagicMock

# Mock redis module before importing ClientManager
mock_redis = MagicMock()
sys.modules["redis"] = mock_redis
sys.modules["redis.exceptions"] = MagicMock()

# Add app directory to path
sys.path.append(os.getcwd())

from app.services.client_tracker import client_tracking_service
from app.proxy.client_manager import ClientManager

def test_telemetry_pipeline():
    print("Testing Telemetry Pipeline...")
    
    stream_id = "test_stream_123"
    client_id = "test_client_456"
    client_ip = "127.0.0.1"
    
    # 1. Initialize ClientManager (MPEG-TS version which we modified)
    # We mock redis_client to avoid needing a running Redis for this basic logic test
    class MockRedis:
        def hset(self, *args, **kwargs): pass
        def expire(self, *args, **kwargs): pass
        def sadd(self, *args, **kwargs): pass
        def scard(self, *args, **kwargs): return 1
        def delete(self, *args, **kwargs): pass
    
    cm = ClientManager(content_id=stream_id, redis_client=MockRedis(), worker_id="test_worker")
    
    # 2. Add Client
    print(f"Adding client {client_id}...")
    cm.add_client(client_id, client_ip, user_agent="TestAgent")
    
    # Verify client exists in tracker
    clients = client_tracking_service.get_stream_clients(stream_id)
    assert len(clients) == 1, f"Expected 1 client, got {len(clients)}"
    assert clients[0]["buffer_seconds_behind"] == 0.0
    print("✓ Client registered with default 0.0 runway")
    
    # 3. Update Position (The failing call)
    print("Updating client position with new signature...")
    try:
        cm.update_client_position(
            client_id=client_id,
            seconds_behind=12.5,
            source="test_verification",
            confidence=0.95,
            observed_at=time.time()
        )
        print("✓ update_client_position call succeeded (no TypeError)")
    except TypeError as e:
        print(f"✗ update_client_position FAILED with TypeError: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ update_client_position FAILED with unexpected error: {e}")
        sys.exit(1)
        
    # 4. Verify data in tracker
    clients = client_tracking_service.get_stream_clients(stream_id)
    client = clients[0]
    
    print(f"Verifying telemetry data: {client.get('buffer_seconds_behind')}s from {client.get('buffer_seconds_behind_source')}")
    
    assert client["buffer_seconds_behind"] == 12.5, f"Expected 12.5, got {client['buffer_seconds_behind']}"
    assert client["buffer_seconds_behind_source"] == "test_verification", f"Expected test_verification, got {client['buffer_seconds_behind_source']}"
    assert client["buffer_seconds_behind_confidence"] == 0.95, f"Expected 0.95, got {client['buffer_seconds_behind_confidence']}"
    
    print("✓ Telemetry data verified in ClientTrackingService")
    print("\nSUCCESS: Telemetry pipeline is working correctly!")

if __name__ == "__main__":
    test_telemetry_pipeline()
