"""
Peer statistics service for the microservice.

This service collects peer information from AceStream torrents using libtorrent.
It's designed to run inside the Gluetun VPN container and provide peer data to the orchestrator.
"""
import logging
import httpx
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import threading
import time

try:
    import libtorrent as lt
except ImportError:
    lt = None
    logging.warning("libtorrent not available - peer stats will not work")

logger = logging.getLogger(__name__)

# Cache for peer stats to prevent excessive API calls
_peer_stats_cache: Dict[str, Dict[str, Any]] = {}
_cache_ttl_seconds = 30  # Cache for 30 seconds

# Cache for IP geolocation data to avoid repeated lookups
_ip_geo_cache: Dict[str, Dict[str, Any]] = {}
_ip_geo_cache_ttl_seconds = 3600  # Cache IP geolocation for 1 hour

# Configuration for peer discovery
PEER_DISCOVERY_WAIT_SECONDS = 2  # Time to wait for peers to connect after adding torrent

# Global libtorrent session (reused across requests)
_lt_session = None
_lt_session_lock = threading.Lock()

# Active torrent handles
_active_handles: Dict[str, Any] = {}  # infohash -> handle


def get_libtorrent_session():
    """Get or create the global libtorrent session."""
    global _lt_session
    
    if lt is None:
        return None
    
    with _lt_session_lock:
        if _lt_session is None:
            _lt_session = lt.session()
            
            # Configure session settings
            settings = {
                'user_agent': 'AceStream/3.1.74',
                'listen_interfaces': '0.0.0.0:6881',
                'enable_dht': True,
                'enable_lsd': True,
                'enable_upnp': False,
                'enable_natpmp': False,
                'announce_to_all_trackers': True,
                'announce_to_all_tiers': True,
                'auto_manage_interval': 5,
                'alert_mask': lt.alert.category_t.status_notification | lt.alert.category_t.error_notification
            }
            
            _lt_session.apply_settings(settings)
            
            # Add DHT routers
            _lt_session.add_dht_router("router.bittorrent.com", 6881)
            _lt_session.add_dht_router("dht.transmissionbt.com", 6881)
            _lt_session.add_dht_router("router.utorrent.com", 6881)
            
            logger.info("Initialized libtorrent session for peer tracking")
        
        return _lt_session


def add_torrent_for_tracking(infohash: str) -> Optional[Any]:
    """
    Add a torrent to the session for peer tracking.
    
    Args:
        infohash: The SHA1 infohash of the torrent
        
    Returns:
        The torrent handle, or None if it fails
    """
    if lt is None:
        logger.error("libtorrent not available")
        return None
    
    session = get_libtorrent_session()
    if session is None:
        return None
    
    # Check if we already have a handle for this infohash
    if infohash in _active_handles:
        handle = _active_handles[infohash]
        if handle.is_valid():
            logger.debug(f"Reusing existing handle for infohash {infohash}")
            return handle
        else:
            # Handle is invalid, remove it
            del _active_handles[infohash]
    
    try:
        # Build magnet link with common trackers
        trackers = [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://tracker.coppersurfer.tk:6969/announce",
            "udp://tracker.leechers-paradise.org:6969/announce",
            "udp://9.rarbg.me:2710/announce",
            "udp://tracker.openbittorrent.com:6969/announce",
            "http://retracker.local/announce"
        ]
        
        magnet_link = f"magnet:?xt=urn:btih:{infohash}"
        for tracker in trackers:
            magnet_link += f"&tr={tracker}"
        
        # Parse magnet URI
        params = lt.parse_magnet_uri(magnet_link)
        params.save_path = "/tmp"  # We won't download anything
        
        # Add flags to minimize downloads
        flags = (
            lt.torrent_flags.upload_mode |  # Upload-only mode
            lt.torrent_flags.auto_managed |
            lt.torrent_flags.paused  # Start paused to avoid downloading
        )
        params.flags = flags
        
        # Add torrent to session
        handle = session.add_torrent(params)
        
        # Resume to allow connecting to peers (but won't download due to upload_mode)
        handle.resume()
        
        # Store handle
        _active_handles[infohash] = handle
        
        logger.info(f"Added torrent for tracking: {infohash}")
        return handle
        
    except Exception as e:
        logger.error(f"Failed to add torrent for infohash {infohash}: {e}", exc_info=True)
        return None


def get_peers_from_handle(handle: Any, max_peers: int = 100) -> List[Dict[str, Any]]:
    """
    Get peer information from a torrent handle.
    
    Args:
        handle: The libtorrent torrent handle
        max_peers: Maximum number of peers to return
        
    Returns:
        List of peer dictionaries
    """
    if lt is None or handle is None or not handle.is_valid():
        return []
    
    try:
        peer_info_list = handle.get_peer_info()
        status = handle.status()
        
        peers = []
        for peer in peer_info_list[:max_peers]:
            try:
                # Extract IP address (peer.ip is a tuple of (ip, port))
                ip_address = peer.ip[0] if isinstance(peer.ip, tuple) else str(peer.ip)
                port = peer.ip[1] if isinstance(peer.ip, tuple) and len(peer.ip) > 1 else 0
                
                # Get client name
                try:
                    client = peer.client.decode('utf-8', errors='replace') if hasattr(peer, 'client') else 'Unknown'
                except:
                    client = 'Unknown'
                
                # Skip localhost and private IPs
                # Private ranges: 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
                if ip_address.startswith("127.") or ip_address.startswith("10.") or \
                   ip_address.startswith("192.168."):
                    continue
                # Check 172.16.0.0 - 172.31.255.255 range
                if ip_address.startswith("172."):
                    parts = ip_address.split(".")
                    if len(parts) >= 2:
                        try:
                            second_octet = int(parts[1])
                            if 16 <= second_octet <= 31:
                                continue
                        except ValueError:
                            pass
                
                peer_data = {
                    "ip": ip_address,
                    "port": port,
                    "client": client,
                    "progress": peer.progress * 100 if hasattr(peer, 'progress') else 0,
                    "download_rate": peer.download_rate if hasattr(peer, 'download_rate') else 0,
                    "upload_rate": peer.upload_rate if hasattr(peer, 'upload_rate') else 0,
                    "flags": str(peer.flags) if hasattr(peer, 'flags') else ""
                }
                
                peers.append(peer_data)
                
            except Exception as e:
                logger.debug(f"Error processing peer info: {e}")
                continue
        
        logger.debug(f"Extracted {len(peers)} peers from handle (total: {status.num_peers})")
        return peers
        
    except Exception as e:
        logger.error(f"Failed to get peer info from handle: {e}")
        return []


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
        # Use HTTPS for secure communication
        url = f"https://ipwhois.app/json/{peer_ip}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract relevant fields
            geo_data = {
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
            "country": "Unknown",
            "country_code": "??",
            "city": "Unknown",
            "region": "Unknown",
            "isp": "Unknown"
        }


def clear_peer_stats_cache(acestream_id: Optional[str] = None):
    """
    Clear the peer stats cache.
    
    Args:
        acestream_id: If provided, only clear cache for this ID. Otherwise clear all.
    """
    if acestream_id:
        _peer_stats_cache.pop(acestream_id, None)
        logger.debug(f"Cleared peer stats cache for {acestream_id}")
    else:
        _peer_stats_cache.clear()
        logger.debug("Cleared all peer stats cache")


def clear_ip_geo_cache():
    """Clear the IP geolocation cache."""
    _ip_geo_cache.clear()
    logger.debug("Cleared IP geolocation cache")
