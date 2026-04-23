from unittest.mock import Mock

import pytest

import app.proxy.stream_manager as stream_manager_module
from app.proxy.redis_keys import RedisKeys
from app.proxy.stream_buffer import StreamBuffer


class _InitRedis:
    def get(self, _key):
        return b"0"


def _build_manager(redis_client=None):
    from app.proxy.stream_manager import StreamManager

    base_redis = redis_client or _InitRedis()
    buffer = StreamBuffer(content_id="test_id", redis_client=base_redis)
    client_manager = Mock()

    manager = StreamManager(
        content_id="test_content_id",
        engine_host="127.0.0.1",
        engine_port=6878,
        engine_container_id="test_container_id",
        buffer=buffer,
        client_manager=client_manager,
        worker_id="test_worker",
        api_key="test_key",
    )
    return manager


def test_get_max_client_buffer_seconds_pipeline_path(monkeypatch):
    manager = _build_manager()

    now = {"value": 1000.0}
    monkeypatch.setattr(stream_manager_module.time, "time", lambda: now["value"])

    class _Pipeline:
        def __init__(self):
            self.keys = []

        def hmget(self, key, _fields):
            self.keys.append(key)
            return self

        def execute(self):
            return [
                [b"10.0", b"10.0", b"1.0", b"999.0", b"999.0"],
                [b"5.0", b"5.0", b"0.9", b"999.5", b"999.5"],
            ]

    class _FakeRedis:
        def __init__(self):
            self.pipe = _Pipeline()

        def smembers(self, _key):
            return {b"client-a", b"client-b"}

        def pipeline(self, transaction=False):
            assert transaction is False
            return self.pipe

        def hmget(self, _key, _fields):
            raise AssertionError("pipeline path should be used")

    fake_redis = _FakeRedis()
    manager.client_manager.redis_client = fake_redis
    manager.client_manager.client_set_key = "stream:clients"

    runway = manager._get_max_client_buffer_seconds()

    # client-a effective: (10 - 1) * 1.0 = 9.0
    # client-b effective: (5 - 0.5) * 0.95 = 4.275
    # p10 with two samples picks lower value.
    assert runway == pytest.approx(4.275, abs=0.001)
    assert len(fake_redis.pipe.keys) == 2


def test_publish_dynamic_tolerance_writes_stream_metadata(monkeypatch):
    manager = _build_manager(redis_client=_InitRedis())

    now = {"value": 5000.0}
    monkeypatch.setattr(stream_manager_module.time, "time", lambda: now["value"])

    class _FakeRedis:
        def __init__(self):
            self.calls = []

        def hset(self, key, mapping):
            self.calls.append((key, dict(mapping)))
            return 1

    fake_redis = _FakeRedis()
    manager.buffer.redis_client = fake_redis

    manager._publish_dynamic_tolerance(7.1234, 8.5, 15.0, 2.0)

    assert len(fake_redis.calls) == 1
    key, mapping = fake_redis.calls[0]
    assert key == RedisKeys.stream_metadata(manager.content_id)
    assert mapping["dynamic_threshold_seconds"] == "7.123"
    assert mapping["current_client_buffer_seconds"] == "8.500"
    assert mapping["max_tolerance_seconds"] == "15.000"
    assert mapping["stream_inactivity_seconds"] == "2.000"
    assert mapping["dynamic_threshold_updated_at"] == str(now["value"])

    # Tiny change within one second should be throttled.
    now["value"] = 5000.5
    manager._publish_dynamic_tolerance(7.1240, 8.5, 15.0, 2.0)
    assert len(fake_redis.calls) == 1
