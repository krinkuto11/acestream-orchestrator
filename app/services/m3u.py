"""M3U proxy service: download, validate and rewrite M3U playlist URLs."""

import re
import logging
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

# Simple hostname / IPv4 validation (no IP ranges – port validation handles that)
HOST_REGEX = re.compile(r'^[a-zA-Z0-9.\-]+$')

# Only allow plain HTTP/HTTPS downloads to prevent SSRF via alternative schemes
_ALLOWED_SCHEMES = {"http", "https"}


def _validate_m3u_url(url: str) -> bool:
    """Return True if *url* has an allowed scheme (http or https)."""
    try:
        parsed = urlparse(url)
        return parsed.scheme.lower() in _ALLOWED_SCHEMES
    except Exception:
        return False


def get_m3u_content(url: str, timeout: float) -> str | None:
    """Download an M3U playlist from *url* and return its text content.

    Only ``http`` and ``https`` schemes are accepted. Returns ``None`` if the
    request fails or the URL scheme is not allowed.
    """
    if not _validate_m3u_url(url):
        logger.error(f"Rejected M3U URL with disallowed scheme: {url!r}")
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.error(f"Error downloading M3U: {e}")
        return None


def validate_host_port(host: str, port_str: str) -> tuple[bool, str | int]:
    """Validate *host* and *port_str*.

    Returns ``(True, port_int)`` on success or ``(False, error_message)`` on
    failure.
    """
    if not host or not HOST_REGEX.match(host):
        return False, "Invalid 'host' parameter."
    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            return False, "Parameter 'port' out of range (1-65535)."
    except (TypeError, ValueError):
        return False, "Parameter 'port' must be an integer."
    return True, port


def modify_m3u_content(content: str, host: str, port: int, mode: str = "default") -> str:
    """Rewrite URLs inside an M3U *content* string.

    Two modes are supported:

    * ``"default"`` – replaces ``http://127.0.0.1:<any-port>/`` and
      ``http://localhost:<any-port>/`` with ``http://host:port/``, and converts
      ``acestream://<40-hex-id>`` to ``http://host:port/ace/getstream?id=<id>``.

    * ``"proxy"`` – rewrites every ``http``/``https`` URL as
      ``http://host:port/proxy?url=<percent-encoded-original>``, and similarly
      converts ``acestream://`` links.
    """
    if mode == "proxy":
        # Rewrite all http/https URLs through the proxy
        url_pattern = re.compile(r'(https?://[^\s\n\'"<>]+)')

        def proxy_replacement(match: re.Match) -> str:
            original_url = match.group(1)
            encoded_url = quote(original_url, safe="")
            return f"http://{host}:{port}/proxy?url={encoded_url}"

        modified = url_pattern.sub(proxy_replacement, content)

        # Also convert acestream:// links in proxy mode
        acestream_pattern = re.compile(r"acestream://([a-fA-F0-9]{40})")

        def acestream_proxy_replacement(match: re.Match) -> str:
            acestream_url = match.group(0)
            encoded_url = quote(acestream_url, safe="")
            return f"http://{host}:{port}/proxy?url={encoded_url}"

        modified = acestream_pattern.sub(acestream_proxy_replacement, modified)

        return modified
    else:
        # Default mode: replace only 127.0.0.1/localhost prefix, keep path intact
        pattern = re.compile(r"http://(?:127\.0\.0\.1|localhost):\d+(?=/)")
        replacement = f"http://{host}:{port}"
        modified = pattern.sub(replacement, content)

        # Convert acestream://<id> → http://host:port/ace/getstream?id=<id>
        acestream_pattern = re.compile(r"acestream://([a-fA-F0-9]{40})")
        acestream_replacement = f"http://{host}:{port}/ace/getstream?id=\\1"
        modified = acestream_pattern.sub(acestream_replacement, modified)

        return modified
