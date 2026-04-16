
import time
import json
import redis
from app.services.client_tracker import client_tracking_service
from app.proxy.redis_keys import RedisKeys

# Mock Redis connection
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=False)
client_tracking_service.set_redis_client(r)

stream_id = "test_resync_stream"
client_id = "resync_client_1"

print(f"--- Phase 1: Initial Registration ---")
client_tracking_service.register_client(
    client_id=client_id,
    stream_id=stream_id,
    ip_address="127.0.0.1",
    user_agent="pytest",
    protocol="TS",
    worker_id="test_worker"
)

# Verify set exists
set_key = RedisKeys.clients(stream_id)
is_member = r.sismember(set_key, client_id)
print(f"Client in Redis set: {is_member}")

print(f"\n--- Phase 2: Simulating Redis Set Loss (Simulating Expiration/Restart) ---")
r.delete(set_key)
print(f"Set deleted manually.")

# Verify invisible to get_stream_clients
clients = client_tracking_service.get_stream_clients(stream_id)
print(f"Clients found after set deletion (should be empty if relying on set): {len(clients)}")

print(f"\n--- Phase 3: Secondary Registration (Simulating Heartbeat/Activity) ---")
# This used to skip Redis SADD if the client was already in memory
client_tracking_service.register_client(
    client_id=client_id,
    stream_id=stream_id,
    protocol="TS",
    worker_id="test_worker"
)

# Verify set is RESTORED
is_member = r.sismember(set_key, client_id)
print(f"Client in Redis set after re-registration: {is_member}")

# Verify visible to get_stream_clients (Set-based)
clients = client_tracking_service.get_stream_clients(stream_id)
print(f"Clients found after resync: {len(clients)}")
if len(clients) > 0:
    print(f"Found client ID: {clients[0]['id']}")

print(f"\n--- Phase 4: Verifying Pipeline Efficiency ---")
# Register 10 more clients
for i in range(10):
    client_tracking_service.register_client(
        client_id=f"bulk_client_{i}",
        stream_id=stream_id,
        ip_address="127.0.0.1",
        user_agent="pytest",
        protocol="TS"
    )

start = time.time()
clients = client_tracking_service.get_stream_clients(stream_id)
duration = time.time() - start
print(f"Fetched {len(clients)} clients via pipeline in {duration:.4f}s")
