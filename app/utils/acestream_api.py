"""
Utility functions for interacting with AceStream engine APIs.
"""
import httpx
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlparse, quote

logger = logging.getLogger(__name__)


async def fetch_infohash_from_stat_url(stat_url: str) -> Optional[str]:
    """
    Fetch the infohash from a stream's stat URL.
    
    Args:
        stat_url: The stat URL for a stream (e.g., http://host:port/ace/stat?id=...)
        
    Returns:
        The infohash string, or None if it cannot be fetched
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(stat_url)
            response.raise_for_status()
            data = response.json()
            return data.get("infohash")
    except Exception as e:
        logger.debug(f"Failed to fetch infohash from {stat_url}: {e}")
        return None


async def fetch_extended_content_info(engine_host: str, engine_port: int, infohash: str) -> Optional[Dict[str, Any]]:
    """
    Fetch extended content information from the AceStream analyze_content API.
    
    Args:
        engine_host: The host of the AceStream engine
        engine_port: The HTTP API port of the AceStream engine
        infohash: The infohash of the content
        
    Returns:
        A dictionary containing extended content information, or None if it cannot be fetched
    """
    try:
        # Build the analyze_content URL
        # /server/api?api_version=3&method=analyze_content&query=acestream%3A%3Finfohash%3D<infohash>
        query = f"acestream:?infohash={infohash}"
        encoded_query = quote(query, safe='')
        url = f"http://{engine_host}:{engine_port}/server/api"
        params = {
            "api_version": "3",
            "method": "analyze_content",
            "query": query
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Extract the result from the response
            if "result" in data and isinstance(data["result"], dict):
                # Filter out null values
                return {k: v for k, v in data["result"].items() if v is not None}
            
            return None
    except Exception as e:
        logger.debug(f"Failed to fetch extended content info for infohash {infohash}: {e}")
        return None


async def get_stream_extended_stats(stat_url: str) -> Optional[Dict[str, Any]]:
    """
    Get extended statistics for a stream by first fetching its infohash,
    then querying the analyze_content API.
    
    Args:
        stat_url: The stat URL for a stream
        
    Returns:
        A dictionary containing extended content information, or None if it cannot be fetched
    """
    try:
        # Parse the stat URL to get host and port
        parsed = urlparse(stat_url)
        engine_host = parsed.hostname
        engine_port = parsed.port
        
        if not engine_host or not engine_port:
            logger.debug(f"Invalid stat URL: {stat_url}")
            return None
        
        # First, fetch the infohash
        infohash = await fetch_infohash_from_stat_url(stat_url)
        if not infohash:
            return None
        
        # Then, fetch extended content info
        return await fetch_extended_content_info(engine_host, engine_port, infohash)
    except Exception as e:
        logger.debug(f"Failed to get extended stats for stream: {e}")
        return None
