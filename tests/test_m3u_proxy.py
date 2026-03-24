#!/usr/bin/env python3
"""
Tests for the M3U proxy service and /modify_m3u endpoint.
"""

import io
import sys
import os
from unittest.mock import patch, MagicMock

# Make app importable from the tests directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Service-level unit tests
# ---------------------------------------------------------------------------

from app.services.m3u import get_m3u_content, validate_host_port, modify_m3u_content, parse_acestream_m3u_entries


# --- validate_host_port ---

def test_validate_host_port_valid():
    ok, result = validate_host_port("192.168.1.1", "8080")
    assert ok is True
    assert result == 8080


def test_validate_host_port_hostname():
    ok, result = validate_host_port("my.host-name.local", "19000")
    assert ok is True
    assert result == 19000


def test_validate_host_port_invalid_host_empty():
    ok, msg = validate_host_port("", "8080")
    assert ok is False
    assert "host" in msg.lower()


def test_validate_host_port_invalid_host_chars():
    ok, msg = validate_host_port("host with spaces", "8080")
    assert ok is False


def test_validate_host_port_port_zero():
    ok, msg = validate_host_port("localhost", "0")
    assert ok is False
    assert "range" in msg.lower() or "port" in msg.lower()


def test_validate_host_port_port_too_high():
    ok, msg = validate_host_port("localhost", "99999")
    assert ok is False


def test_validate_host_port_port_not_integer():
    ok, msg = validate_host_port("localhost", "abc")
    assert ok is False
    assert "integer" in msg.lower() or "port" in msg.lower()


def test_validate_host_port_port_boundary_min():
    ok, result = validate_host_port("host", "1")
    assert ok is True
    assert result == 1


def test_validate_host_port_port_boundary_max():
    ok, result = validate_host_port("host", "65535")
    assert ok is True
    assert result == 65535


# --- get_m3u_content ---

def test_get_m3u_content_rejects_non_http_scheme():
    # file://, ftp://, etc. must be rejected without making a network request
    import requests as _requests

    with patch("app.services.m3u.requests.get") as mock_get:
        result = get_m3u_content("file:///etc/passwd", 5)

    assert result is None
    mock_get.assert_not_called()


def test_get_m3u_content_success():
    mock_response = MagicMock()
    mock_response.text = "#EXTM3U\n#EXTINF:-1,Channel\nhttp://127.0.0.1:8080/stream\n"
    mock_response.raise_for_status = MagicMock()

    with patch("app.services.m3u.requests.get", return_value=mock_response) as mock_get:
        result = get_m3u_content("http://example.com/playlist.m3u", 15)

    mock_get.assert_called_once_with("http://example.com/playlist.m3u", timeout=15)
    assert result is not None
    assert "#EXTM3U" in result


def test_get_m3u_content_http_error():
    import requests as _requests

    with patch("app.services.m3u.requests.get", side_effect=_requests.RequestException("timeout")):
        result = get_m3u_content("http://example.com/bad.m3u", 5)

    assert result is None


# --- modify_m3u_content (default mode) ---

SAMPLE_M3U = (
    "#EXTM3U\n"
    "#EXTINF:-1,Channel 1\n"
    "http://127.0.0.1:8080/stream/ch1\n"
    "#EXTINF:-1,Channel 2\n"
    "http://localhost:8080/stream/ch2\n"
    "#EXTINF:-1,Ace Channel\n"
    "acestream://aabbccdd1122334455667788990011223344556677\n"
)


def test_modify_default_replaces_localhost():
    result = modify_m3u_content(SAMPLE_M3U, "my.host", 9000)
    assert "http://my.host:9000/stream/ch1" in result
    assert "http://my.host:9000/stream/ch2" in result
    assert "127.0.0.1" not in result
    assert "localhost" not in result


def test_modify_default_converts_acestream():
    result = modify_m3u_content(SAMPLE_M3U, "my.host", 9000)
    assert "http://my.host:9000/ace/getstream?id=aabbccdd1122334455667788990011223344556677" in result
    assert "acestream://" not in result


def test_modify_default_preserves_extm3u_header():
    result = modify_m3u_content(SAMPLE_M3U, "my.host", 9000)
    assert result.startswith("#EXTM3U")


def test_modify_default_does_not_touch_external_urls():
    content = "#EXTM3U\n#EXTINF:-1,External\nhttp://external.cdn.com/stream\n"
    result = modify_m3u_content(content, "my.host", 9000)
    # External URLs should not be touched in default mode
    assert "http://external.cdn.com/stream" in result


# --- modify_m3u_content (proxy mode) ---

def test_modify_proxy_rewrites_http_urls():
    content = "#EXTM3U\n#EXTINF:-1,Ch\nhttp://external.cdn.com/stream\n"
    result = modify_m3u_content(content, "proxy.host", 8888, mode="proxy")
    assert "http://proxy.host:8888/proxy?url=" in result
    # The original URL should appear only percent-encoded, not as a bare http:// link
    assert "http://external.cdn.com" not in result


def test_modify_proxy_rewrites_https_urls():
    content = "#EXTM3U\n#EXTINF:-1,Ch\nhttps://secure.cdn.com/stream\n"
    result = modify_m3u_content(content, "proxy.host", 8888, mode="proxy")
    assert "http://proxy.host:8888/proxy?url=" in result
    # The original HTTPS URL should appear only percent-encoded
    assert "https://secure.cdn.com" not in result


def test_modify_proxy_converts_acestream():
    content = "#EXTM3U\n#EXTINF:-1,Ace\nacestream://aabbccdd1122334455667788990011223344556677\n"
    result = modify_m3u_content(content, "proxy.host", 8888, mode="proxy")
    assert "http://proxy.host:8888/proxy?url=" in result
    assert "acestream://" not in result


def test_parse_acestream_m3u_entries_extracts_name_and_id():
    content = (
        "#EXTM3U\n"
        "#EXTINF:-1 tvg-id=\"x\",My Channel\n"
        "acestream://AABBCCDDEEFF00112233445566778899AABBCCDD\n"
    )

    parsed = parse_acestream_m3u_entries(content)
    assert len(parsed) == 1
    assert parsed[0]["content_id"] == "aabbccddeeff00112233445566778899aabbccdd"
    assert parsed[0]["name"] == "My Channel"


def test_parse_acestream_m3u_entries_deduplicates_ids():
    content = (
        "#EXTM3U\n"
        "#EXTINF:-1,One\n"
        "acestream://aabbccddeeff00112233445566778899aabbccdd\n"
        "#EXTINF:-1,Two\n"
        "acestream://aabbccddeeff00112233445566778899aabbccdd\n"
    )

    parsed = parse_acestream_m3u_entries(content)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "One"


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

from app.services.db import engine as db_engine
from app.models.db_models import Base

Base.metadata.create_all(bind=db_engine)


def test_endpoint_missing_required_params():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    # m3u_url, host, port are all required
    resp = client.get("/modify_m3u")
    assert resp.status_code == 422  # FastAPI validation error


def test_endpoint_invalid_mode():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    mock_response = MagicMock()
    mock_response.text = SAMPLE_M3U
    mock_response.raise_for_status = MagicMock()

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    with patch("app.services.m3u.requests.get", return_value=mock_response):
        resp = client.get(
            "/modify_m3u",
            params={"m3u_url": "http://example.com/p.m3u", "host": "myhost", "port": "8080", "mode": "invalid"},
        )
    assert resp.status_code == 400


def test_endpoint_invalid_host():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    mock_response = MagicMock()
    mock_response.text = SAMPLE_M3U
    mock_response.raise_for_status = MagicMock()

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    with patch("app.services.m3u.requests.get", return_value=mock_response):
        resp = client.get(
            "/modify_m3u",
            params={"m3u_url": "http://example.com/p.m3u", "host": "bad host!", "port": "8080"},
        )
    assert resp.status_code == 400


def test_endpoint_download_failure():
    import requests as _requests
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    with patch("app.services.m3u.requests.get", side_effect=_requests.RequestException("network error")):
        resp = client.get(
            "/modify_m3u",
            params={"m3u_url": "http://example.com/p.m3u", "host": "myhost", "port": "8080"},
        )
    assert resp.status_code == 400


def test_endpoint_default_mode_success():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    mock_response = MagicMock()
    mock_response.text = SAMPLE_M3U
    mock_response.raise_for_status = MagicMock()

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    with patch("app.services.m3u.requests.get", return_value=mock_response):
        resp = client.get(
            "/modify_m3u",
            params={"m3u_url": "http://example.com/p.m3u", "host": "myhost", "port": "9000"},
        )
    assert resp.status_code == 200
    assert "application/x-mpegurl" in resp.headers["content-type"].lower()
    body = resp.content.decode("utf-8")
    assert "http://myhost:9000/stream/ch1" in body
    assert "http://myhost:9000/ace/getstream?id=" in body


def test_endpoint_proxy_mode_success():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    mock_response = MagicMock()
    mock_response.text = SAMPLE_M3U
    mock_response.raise_for_status = MagicMock()

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    with patch("app.services.m3u.requests.get", return_value=mock_response):
        resp = client.get(
            "/modify_m3u",
            params={"m3u_url": "http://example.com/p.m3u", "host": "myhost", "port": "9000", "mode": "proxy"},
        )
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")
    assert "http://myhost:9000/proxy?url=" in body


def test_endpoint_custom_timeout():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    mock_response = MagicMock()
    mock_response.text = SAMPLE_M3U
    mock_response.raise_for_status = MagicMock()

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    with patch("app.services.m3u.requests.get", return_value=mock_response) as mock_get:
        resp = client.get(
            "/modify_m3u",
            params={"m3u_url": "http://example.com/p.m3u", "host": "myhost", "port": "9000", "timeout": "30"},
        )
    assert resp.status_code == 200
    # Ensure the custom timeout was passed through
    _, kwargs = mock_get.call_args
    assert kwargs.get("timeout") == 30.0


def test_endpoint_invalid_timeout():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    resp = client.get(
        "/modify_m3u",
        params={"m3u_url": "http://example.com/p.m3u", "host": "myhost", "port": "9000", "timeout": "-1"},
    )
    assert resp.status_code == 400


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
