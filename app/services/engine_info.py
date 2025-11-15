"""
Engine Information Service

Fetches engine version and platform information from AceStream engines.
"""

import logging
import httpx
from typing import Optional, Dict

logger = logging.getLogger(__name__)


async def get_engine_version_info(host: str, port: int) -> Optional[Dict]:
    """
    Get engine version information from AceStream engine.
    
    Args:
        host: Engine host address
        port: Engine HTTP port
        
    Returns:
        Dict with 'platform', 'version', 'code', 'websocket_port' or None if unavailable
    """
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
                logger.debug(f"Retrieved version info for {host}:{port}: {version_info}")
                return version_info
            else:
                logger.warning(f"Invalid response format from {host}:{port}")
                return None
                
    except Exception as e:
        logger.debug(f"Failed to get engine version from {host}:{port}: {e}")
        return None


def get_engine_version_info_sync(host: str, port: int) -> Optional[Dict]:
    """
    Synchronous version of get_engine_version_info.
    
    Args:
        host: Engine host address
        port: Engine HTTP port
        
    Returns:
        Dict with 'platform', 'version', 'code', 'websocket_port' or None if unavailable
    """
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
                logger.debug(f"Retrieved version info for {host}:{port}: {version_info}")
                return version_info
            else:
                logger.warning(f"Invalid response format from {host}:{port}")
                return None
                
    except Exception as e:
        logger.warning(f"Failed to get engine version from {host}:{port}: {e}")
        return None
