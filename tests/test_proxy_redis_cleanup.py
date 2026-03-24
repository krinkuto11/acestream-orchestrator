from unittest.mock import MagicMock

from app.proxy.server import ProxyServer


def test_flush_stream_redis_cache_deletes_direct_and_wildcard_keys():
    server = ProxyServer.__new__(ProxyServer)

    redis_client = MagicMock()
    redis_client.delete.side_effect = lambda *keys: len(keys)

    def _scan_iter(match=None, count=None):
        if match and match.endswith(":buffer:chunk:*"):
            return iter([b"ace_proxy:stream:abc123:buffer:chunk:1", b"ace_proxy:stream:abc123:buffer:chunk:2"])
        if match and match.endswith(":clients:*"):
            return iter([b"ace_proxy:stream:abc123:clients:c1"])
        if match and match.endswith(":worker:*"):
            return iter([b"ace_proxy:stream:abc123:worker:w1"])
        return iter([])

    redis_client.scan_iter.side_effect = _scan_iter
    server.redis_client = redis_client

    deleted = server._flush_stream_redis_cache("abc123")

    # 10 direct keys + 4 wildcard-matched keys
    assert deleted == 14
    assert redis_client.delete.call_count == 4


def test_stop_stream_triggers_redis_flush():
    server = ProxyServer.__new__(ProxyServer)
    server.stream_managers = {}
    server.client_managers = {}
    server.stream_buffers = {}
    server.redis_client = MagicMock()

    server._flush_stream_redis_cache = MagicMock(return_value=3)

    server._stop_stream("abc123")

    server._flush_stream_redis_cache.assert_called_once_with("abc123")
