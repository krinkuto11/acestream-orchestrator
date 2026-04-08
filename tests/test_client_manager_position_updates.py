from app.proxy.client_manager import ClientManager


class _FakeProxyServer:
    def am_i_owner(self, _content_id):
        return True


class _FakeRedis:
    def __init__(self):
        self.hset_calls = []
        self.expire_calls = []
        self.sadd_calls = []

    def hset(self, key, field, value):
        self.hset_calls.append((key, field, value))
        return 1

    def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))
        return 1

    def sadd(self, key, value):
        self.sadd_calls.append((key, value))
        return 1


def test_update_client_position_refreshes_redis_ttl(monkeypatch):
    monkeypatch.setattr("app.proxy.client_manager.ClientManager._start_heartbeat_thread", lambda self: None)
    monkeypatch.setattr("app.proxy.server.ProxyServer.get_instance", lambda: _FakeProxyServer())

    fake_redis = _FakeRedis()
    manager = ClientManager(content_id="stream-1", redis_client=fake_redis, worker_id="worker-1")

    manager.update_client_position(
        client_id="client-1",
        seconds_behind=4.2,
        source="ts_cursor_ema",
        confidence=0.8,
        observed_at=1234.0,
    )

    assert any(field == "last_active" for _, field, _ in fake_redis.hset_calls)
    assert any(key.endswith(":clients:client-1") for key, _ in fake_redis.expire_calls)
    assert any(key.endswith(":clients") for key, _ in fake_redis.expire_calls)
    assert any(value == "client-1" for _, value in fake_redis.sadd_calls)
