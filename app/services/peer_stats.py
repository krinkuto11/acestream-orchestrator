"""
Service for collecting peer statistics from AceStream torrents.

This service collects peer information from active streams and enriches
it with geolocation data from ipwhois.io API.
"""
import asyncio
import logging
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from urllib.parse import urlparse
import json

logger = logging.getLogger(__name__)

# Cache for peer stats to prevent excessive API calls
_peer_stats_cache: Dict[str, Dict[str, Any]] = {}
_cache_ttl_seconds = 30  # Cache for 30 seconds

# Cache for IP geolocation data to avoid repeated lookups
_ip_geo_cache: Dict[str, Dict[str, Any]] = {}
_ip_geo_cache_ttl_seconds = 3600  # Cache IP geolocation for 1 hour


async def fetch_peer_list_from_stat_url(stat_url: str) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch the peer list from a stream's stat URL.
    
    The AceStream stat API may return peer information in the response.
    We'll parse it to extract IP addresses and other peer data.
    
    Args:
        stat_url: The stat URL for a stream (e.g., http://host:port/ace/stat?id=...)
        
    Returns:
        A list of peer dictionaries, or None if it cannot be fetched
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(stat_url)
            response.raise_for_status()
            data = response.json()
            
            # Try to extract peer information from the response
            # AceStream stat response structure may vary, so we'll check multiple paths
            peers = []
            
            # Check in response.peers
            if isinstance(data, dict):
                response_data = data.get("response", {})
                if isinstance(response_data, dict):
                    peer_data = response_data.get("peers", [])
                    if isinstance(peer_data, list):
                        peers = peer_data
                    
                    # Also check for peer info in other possible locations
                    if not peers:
                        # Try peers_info
                        peer_data = response_data.get("peers_info", [])
                        if isinstance(peer_data, list):
                            peers = peer_data
            
            logger.debug(f"Found {len(peers)} peers for {stat_url}")
            return peers if peers else None
            
    except Exception as e:
        logger.debug(f"Failed to fetch peer list from {stat_url}: {e}")
        return None


async def enrich_peer_with_geolocation(peer_ip: str) -> Optional[Dict[str, Any]]:
    """
    Enrich peer data with geolocation information from ipwhois.io API.
    
    Args:
        peer_ip: The IP address of the peer
        
    Returns:
        A dictionary containing geolocation data, or None if it cannot be fetched
    """
    # Check cache first
    if peer_ip in _ip_geo_cache:
        cached_data = _ip_geo_cache[peer_ip]
        if (datetime.now(timezone.utc).timestamp() - cached_data.get("_cached_at", 0)) < _ip_geo_cache_ttl_seconds:
            logger.debug(f"Using cached geolocation for {peer_ip}")
            return cached_data
    
    try:
        url = f"http://ipwhois.app/json/{peer_ip}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract relevant fields
            geo_data = {
                "ip": peer_ip,
                "country": data.get("country", "Unknown"),
                "country_code": data.get("country_code", "??"),
                "city": data.get("city", "Unknown"),
                "region": data.get("region", "Unknown"),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "timezone": data.get("timezone"),
                "isp": data.get("isp", "Unknown"),
                "_cached_at": datetime.now(timezone.utc).timestamp()
            }
            
            # Cache the result
            _ip_geo_cache[peer_ip] = geo_data
            logger.debug(f"Fetched and cached geolocation for {peer_ip}: {geo_data.get('country')} / {geo_data.get('city')}")
            
            return geo_data
            
    except Exception as e:
        logger.debug(f"Failed to fetch geolocation for {peer_ip}: {e}")
        # Return basic data even if geolocation fails
        return {
            "ip": peer_ip,
            "country": "Unknown",
            "country_code": "??",
            "city": "Unknown",
            "region": "Unknown",
            "isp": "Unknown"
        }


async def get_stream_peer_stats(stream_id: str, stat_url: str) -> Optional[Dict[str, Any]]:
    """
    Get peer statistics for a stream, including geolocation data.
    
    This function fetches the peer list from the stream's stat URL and enriches
    each peer with geolocation data. Results are cached to prevent excessive API calls.
    
    Args:
        stream_id: The unique ID of the stream
        stat_url: The stat URL for the stream
        
    Returns:
        A dictionary containing peer statistics, or None if it cannot be fetched
    """
    # Check cache first
    if stream_id in _peer_stats_cache:
        cached_data = _peer_stats_cache[stream_id]
        if (datetime.now(timezone.utc).timestamp() - cached_data.get("cached_at", 0)) < _cache_ttl_seconds:
            logger.debug(f"Using cached peer stats for stream {stream_id}")
            return cached_data
    
    try:
        # Fetch peer list from stat URL
        peers = await fetch_peer_list_from_stat_url(stat_url)
        
        if peers is None:
            # No peers found or error fetching
            result = {
                "stream_id": stream_id,
                "peers": [],
                "peer_count": 0,
                "cached_at": datetime.now(timezone.utc).timestamp(),
                "error": "No peer data available from AceStream engine"
            }
            _peer_stats_cache[stream_id] = result
            return result
        
        # Enrich each peer with geolocation data
        # Limit concurrent requests to avoid overwhelming the API
        enriched_peers = []
        
        # Extract IP addresses from peers
        # The structure may vary, so we'll try different fields
        for peer in peers[:100]:  # Limit to first 100 peers
            peer_ip = None
            
            # Try different possible IP field names
            if isinstance(peer, dict):
                peer_ip = peer.get("ip") or peer.get("address") or peer.get("peer_ip")
                
                # Handle tuple format (ip, port)
                if isinstance(peer_ip, (list, tuple)) and len(peer_ip) > 0:
                    peer_ip = peer_ip[0]
            elif isinstance(peer, str):
                peer_ip = peer
            
            if peer_ip:
                # Skip localhost and private IPs
                if peer_ip.startswith("127.") or peer_ip.startswith("192.168.") or peer_ip.startswith("10.") or peer_ip.startswith("172."):
                    continue
                
                # Fetch geolocation (this will use cache if available)
                geo_data = await enrich_peer_with_geolocation(peer_ip)
                
                if geo_data:
                    # Merge with original peer data
                    enriched_peer = {**peer} if isinstance(peer, dict) else {}
                    enriched_peer.update(geo_data)
                    enriched_peers.append(enriched_peer)
                    
                # Add a small delay to avoid rate limiting
                await asyncio.sleep(0.1)
        
        # Build result
        result = {
            "stream_id": stream_id,
            "peers": enriched_peers,
            "peer_count": len(enriched_peers),
            "cached_at": datetime.now(timezone.utc).timestamp()
        }
        
        # Cache the result
        _peer_stats_cache[stream_id] = result
        logger.info(f"Fetched and cached peer stats for stream {stream_id}: {len(enriched_peers)} peers")
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get peer stats for stream {stream_id}: {e}", exc_info=True)
        return None


def clear_peer_stats_cache(stream_id: Optional[str] = None):
    """
    Clear the peer stats cache.
    
    Args:
        stream_id: If provided, only clear cache for this stream. Otherwise clear all.
    """
    if stream_id:
        _peer_stats_cache.pop(stream_id, None)
        logger.debug(f"Cleared peer stats cache for stream {stream_id}")
    else:
        _peer_stats_cache.clear()
        logger.debug("Cleared all peer stats cache")


def clear_ip_geo_cache():
    """Clear the IP geolocation cache."""
    _ip_geo_cache.clear()
    logger.debug("Cleared IP geolocation cache")
