"""
Engine Information Service

Fetches engine version and platform information from AceStream engines.
"""

import logging
import httpx
import threading
from typing import Optional, Dict

logger = logging.getLogger(__name__)


_version_cache = {}
_version_cache_lock = threading.Lock()


def _cache_key(host: str, port: int, cache_key: Optional[str]) -> str:
    return str(cache_key or f"{host}:{int(port)}")


def get_cached_engine_version_info(cache_key: str, cache_revision: Optional[str] = None) -> Optional[Dict]:
    """Return cached engine version info when revision matches."""
    if not cache_key:
        return None

    with _version_cache_lock:
        entry = _version_cache.get(str(cache_key))

    if not entry:
        return None

    if cache_revision is not None and entry.get("revision") != str(cache_revision):
        return None

    return dict(entry.get("value") or {})


def invalidate_engine_version_cache(cache_key: str):
    """Invalidate cached version info for a single engine key."""
    if not cache_key:
        return
    with _version_cache_lock:
        _version_cache.pop(str(cache_key), None)


def clear_engine_version_cache():
    """Clear all cached engine version info."""
    with _version_cache_lock:
        _version_cache.clear()


async def get_engine_version_info(
    host: str,
    port: int,
    cache_key: Optional[str] = None,
    cache_revision: Optional[str] = None,
) -> Optional[Dict]:
    """
    Get engine version information from AceStream engine.
    
    Args:
        host: Engine host address
        port: Engine HTTP port
        
    Returns:
        Dict with 'platform', 'version', 'code', 'websocket_port' or None if unavailable
    """
    key = _cache_key(host, port, cache_key)
    cached = get_cached_engine_version_info(key, cache_revision=cache_revision)
    if cached:
        return cached

    try:
        url = f"http://{host}:{port}/webui/api/service?method=get_version"
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract result from response
            result = data.get("result")
            if result and isinstance(result, dict):
                version_info = {
                    "platform": result.get("platform", "unknown"),
                    "version": result.get("version", "unknown"),
                    "code": result.get("code"),
                    "websocket_port": result.get("websocket_port")
                }
                with _version_cache_lock:
                    _version_cache[key] = {
                        "revision": str(cache_revision) if cache_revision is not None else None,
                        "value": dict(version_info),
                    }
                logger.debug(f"Retrieved version info for {host}:{port}: {version_info}")
                return version_info
            else:
                logger.debug(f"Invalid response format from {host}:{port}")
                return None
                
    except Exception as e:
        logger.debug(f"Failed to get engine version from {host}:{port}: {e}")
        return None


def get_engine_version_info_sync(
    host: str,
    port: int,
    cache_key: Optional[str] = None,
    cache_revision: Optional[str] = None,
) -> Optional[Dict]:
    """
    Synchronous version of get_engine_version_info.
    
    Args:
        host: Engine host address
        port: Engine HTTP port
        
    Returns:
        Dict with 'platform', 'version', 'code', 'websocket_port' or None if unavailable
    """
    key = _cache_key(host, port, cache_key)
    cached = get_cached_engine_version_info(key, cache_revision=cache_revision)
    if cached:
        return cached

    try:
        url = f"http://{host}:{port}/webui/api/service?method=get_version"
        
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract result from response
            result = data.get("result")
            if result and isinstance(result, dict):
                version_info = {
                    "platform": result.get("platform", "unknown"),
                    "version": result.get("version", "unknown"),
                    "code": result.get("code"),
                    "websocket_port": result.get("websocket_port")
                }
                with _version_cache_lock:
                    _version_cache[key] = {
                        "revision": str(cache_revision) if cache_revision is not None else None,
                        "value": dict(version_info),
                    }
                logger.debug(f"Retrieved version info for {host}:{port}: {version_info}")
                return version_info
            else:
                logger.debug(f"Invalid response format from {host}:{port}")
                return None
                
    except Exception as e:
        logger.debug(f"Failed to get engine version from {host}:{port}: {e}")
        return None
