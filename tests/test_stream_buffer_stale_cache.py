import time

import app.proxy.stream_buffer as stream_buffer_module
from app.proxy.redis_keys import RedisKeys
from app.proxy.stream_buffer import StreamBuffer


class _FakeRedisScan:
    def __init__(self):
        index_key = RedisKeys.buffer_index("test")
        self.kv = {
            index_key: b"3",
            RedisKeys.buffer_chunk("test", 1): b"a",
            RedisKeys.buffer_chunk("test", 2): b"b",
            RedisKeys.buffer_chunk("test", 3): b"c",
        }

    def get(self, key):
        return self.kv.get(key)

    def scan_iter(self, match=None, count=10):
        if match is None:
            for key in list(self.kv.keys()):
                yield key
            return
        prefix = str(match).rstrip("*")
        for key in list(self.kv.keys()):
            if str(key).startswith(prefix):
                yield key

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.kv:
                del self.kv[key]
                removed += 1
        return removed


class _FakeRedisNoScan:
    def __init__(self):
        index_key = RedisKeys.buffer_index("test")
        self.kv = {
            index_key: b"2",
            RedisKeys.buffer_chunk("test", 1): b"x",
            RedisKeys.buffer_chunk("test", 2): b"y",
        }

    def get(self, key):
        return self.kv.get(key)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.kv:
                del self.kv[key]
                removed += 1
        return removed


def test_stream_buffer_is_upstream_fresh_respects_silence(monkeypatch):
    redis = _FakeRedisScan()
    buffer = StreamBuffer(content_id="test", redis_client=redis)

    now = {"value": 100.0}
    monkeypatch.setattr(stream_buffer_module.time, "time", lambda: now["value"])

    buffer.last_upstream_write_time = 95.5
    assert buffer.is_upstream_fresh(10.0) is True

    now["value"] = 112.0
    assert buffer.is_upstream_fresh(10.0) is False


def test_stream_buffer_purge_stale_cache_with_scan_iter_resets_state():
    redis = _FakeRedisScan()
    buffer = StreamBuffer(content_id="test", redis_client=redis)

    buffer.index = 3
    buffer.last_fetch_end_index = 3
    buffer.last_upstream_write_time = time.time()
    buffer._write_buffer = bytearray(b"queued")

    deleted = buffer.purge_stale_cache(reason="unit_test")

    assert deleted == 3
    assert redis.get(RedisKeys.buffer_index("test")) is None
    assert RedisKeys.buffer_chunk("test", 1) not in redis.kv
    assert RedisKeys.buffer_chunk("test", 2) not in redis.kv
    assert RedisKeys.buffer_chunk("test", 3) not in redis.kv
    assert buffer.index == 0
    assert buffer.last_fetch_end_index == 0
    assert buffer.last_upstream_write_time == 0.0
    assert buffer._write_buffer == bytearray()


def test_stream_buffer_purge_stale_cache_without_scan_iter_fallback():
    redis = _FakeRedisNoScan()
    buffer = StreamBuffer(content_id="test", redis_client=redis)

    buffer.index = 2

    deleted = buffer.purge_stale_cache(reason="unit_test_fallback")

    assert deleted == 2
    assert redis.get(RedisKeys.buffer_index("test")) is None
    assert RedisKeys.buffer_chunk("test", 1) not in redis.kv
    assert RedisKeys.buffer_chunk("test", 2) not in redis.kv
