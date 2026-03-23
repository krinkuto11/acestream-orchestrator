from unittest.mock import Mock, patch

from app.services.engine_info import (
    clear_engine_version_cache,
    get_engine_version_info_sync,
    invalidate_engine_version_cache,
)


def setup_function():
    clear_engine_version_cache()


def test_get_engine_version_info_sync_uses_cache_for_same_revision():
    with patch("app.services.engine_info.httpx.Client") as client_cls:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "result": {
                "platform": "linux",
                "version": "3.2.3",
                "code": 123,
                "websocket_port": 8621,
            }
        }

        client = Mock()
        client.get.return_value = response
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client_cls.return_value = client

        first = get_engine_version_info_sync(
            "127.0.0.1",
            6878,
            cache_key="engine-1",
            cache_revision="started-a",
        )
        second = get_engine_version_info_sync(
            "127.0.0.1",
            6878,
            cache_key="engine-1",
            cache_revision="started-a",
        )

        assert first is not None
        assert second is not None
        assert first["version"] == "3.2.3"
        assert second["version"] == "3.2.3"
        assert client.get.call_count == 1


def test_get_engine_version_info_sync_refreshes_on_revision_change():
    with patch("app.services.engine_info.httpx.Client") as client_cls:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = [
            {
                "result": {
                    "platform": "linux",
                    "version": "1.0.0",
                    "code": 1,
                    "websocket_port": 8621,
                }
            },
            {
                "result": {
                    "platform": "linux",
                    "version": "1.0.1",
                    "code": 2,
                    "websocket_port": 8621,
                }
            },
        ]

        client = Mock()
        client.get.return_value = response
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client_cls.return_value = client

        first = get_engine_version_info_sync(
            "127.0.0.1",
            6878,
            cache_key="engine-1",
            cache_revision="started-a",
        )
        second = get_engine_version_info_sync(
            "127.0.0.1",
            6878,
            cache_key="engine-1",
            cache_revision="started-b",
        )

        assert first is not None
        assert second is not None
        assert first["version"] == "1.0.0"
        assert second["version"] == "1.0.1"
        assert client.get.call_count == 2


def test_invalidate_engine_version_cache_forces_refetch():
    with patch("app.services.engine_info.httpx.Client") as client_cls:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "result": {
                "platform": "linux",
                "version": "2.0.0",
                "code": 11,
                "websocket_port": 8621,
            }
        }

        client = Mock()
        client.get.return_value = response
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client_cls.return_value = client

        _ = get_engine_version_info_sync(
            "127.0.0.1",
            6878,
            cache_key="engine-1",
            cache_revision="started-a",
        )
        invalidate_engine_version_cache("engine-1")
        _ = get_engine_version_info_sync(
            "127.0.0.1",
            6878,
            cache_key="engine-1",
            cache_revision="started-a",
        )

        assert client.get.call_count == 2
