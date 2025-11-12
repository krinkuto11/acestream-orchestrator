"""
VPN Location Service

Matches VPN public IPs to Gluetun server list to determine provider, country, and city.
Caches server list with daily updates for optimal performance.
"""

import json
import logging
import httpx
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from pathlib import Path
import asyncio

logger = logging.getLogger(__name__)

# Cache configuration
GLUETUN_SERVERS_URL = "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json"
CACHE_FILE = Path("/tmp/gluetun_servers_cache.json")
CACHE_DURATION_HOURS = 24


class VPNLocationService:
    """Service to match VPN IPs to server locations."""
    
    def __init__(self):
        self._servers_cache: Optional[Dict] = None
        self._cache_timestamp: Optional[datetime] = None
        self._ip_index: Dict[str, Dict] = {}  # IP -> server info mapping
        self._lock = asyncio.Lock()
    
    def is_ready(self) -> bool:
        """
        Check if the service is ready to perform lookups.
        
        Returns:
            True if IP index is populated and ready for lookups
        """
        return len(self._ip_index) > 0
    
    async def get_location_by_ip(self, public_ip: str) -> Optional[Dict[str, str]]:
        """
        Get VPN location information by public IP.
        
        Args:
            public_ip: The public IP address to lookup
            
        Returns:
            Dict with 'provider', 'country', 'city' keys, or None if not found
        """
        if not public_ip:
            return None
        
        # Ensure we have fresh server data
        await self._ensure_server_data()
        
        # Check if service is ready (IPs are indexed)
        if not self.is_ready():
            logger.debug("VPN location service not ready yet (IPs not indexed)")
            return None
        
        # Lookup in index
        server_info = self._ip_index.get(public_ip)
        if server_info:
            logger.debug(f"Found location for IP {public_ip}: {server_info}")
            return server_info
        
        logger.debug(f"No location found for IP {public_ip}")
        return None
    
    async def _ensure_server_data(self):
        """Ensure we have fresh server data, fetching if needed."""
        async with self._lock:
            # Check if cache is still valid
            if self._cache_timestamp and self._servers_cache:
                age = datetime.now(timezone.utc) - self._cache_timestamp
                if age < timedelta(hours=CACHE_DURATION_HOURS):
                    logger.debug("Using cached server data")
                    return
            
            # Try to load from disk cache first
            if await self._load_from_disk_cache():
                logger.info("Loaded server data from disk cache")
                return
            
            # Fetch fresh data from URL
            await self._fetch_and_cache_servers()
    
    async def _load_from_disk_cache(self) -> bool:
        """Load server data from disk cache if it exists and is fresh."""
        try:
            if not CACHE_FILE.exists():
                return False
            
            # Check file age
            file_mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime, tz=timezone.utc)
            age = datetime.now(timezone.utc) - file_mtime
            
            if age >= timedelta(hours=CACHE_DURATION_HOURS):
                logger.info(f"Disk cache is stale (age: {age}), will fetch fresh data")
                return False
            
            # Load from disk
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
            
            self._servers_cache = data['servers']
            self._cache_timestamp = datetime.fromisoformat(data['timestamp'])
            self._build_ip_index()
            
            logger.info(f"Loaded {len(self._ip_index)} server IPs from disk cache")
            return True
            
        except Exception as e:
            logger.error(f"Error loading disk cache: {e}")
            return False
    
    async def _fetch_and_cache_servers(self):
        """Fetch server list from Gluetun and build IP index."""
        try:
            logger.info(f"Fetching Gluetun server list from {GLUETUN_SERVERS_URL}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(GLUETUN_SERVERS_URL)
                response.raise_for_status()
                servers_data = response.json()
            
            self._servers_cache = servers_data
            self._cache_timestamp = datetime.now(timezone.utc)
            
            # Build IP index
            self._build_ip_index()
            
            # Save to disk cache
            await self._save_to_disk_cache()
            
            logger.info(f"Successfully fetched and indexed {len(self._ip_index)} server IPs")
            
        except Exception as e:
            logger.error(f"Error fetching Gluetun server list: {e}")
            # If we have old cache, keep using it
            if self._servers_cache:
                logger.warning("Using stale cache due to fetch error")
    
    def _build_ip_index(self):
        """Build optimized IP-to-server mapping index."""
        self._ip_index.clear()
        
        if not self._servers_cache:
            return
        
        # Iterate through all providers
        for provider_name, provider_data in self._servers_cache.items():
            # Skip metadata fields
            if provider_name in ['version', '<root>']:
                continue
            
            if not isinstance(provider_data, dict):
                continue
            
            servers = provider_data.get('servers', [])
            if not isinstance(servers, list):
                continue
            
            # Index each server's IPs
            for server in servers:
                if not isinstance(server, dict):
                    continue
                
                ips = server.get('ips', [])
                country = server.get('country', '')
                city = server.get('city', '')
                
                # Index each IP
                for ip in ips:
                    if isinstance(ip, str) and ip:
                        self._ip_index[ip] = {
                            'provider': provider_name,
                            'country': country,
                            'city': city or 'N/A'
                        }
        
        logger.debug(f"Built IP index with {len(self._ip_index)} entries")
    
    async def _save_to_disk_cache(self):
        """Save server data to disk cache."""
        try:
            cache_data = {
                'servers': self._servers_cache,
                'timestamp': self._cache_timestamp.isoformat()
            }
            
            # Ensure parent directory exists
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to temp file first, then rename (atomic operation)
            temp_file = CACHE_FILE.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(cache_data, f)
            
            temp_file.replace(CACHE_FILE)
            logger.debug(f"Saved server data to disk cache: {CACHE_FILE}")
            
        except Exception as e:
            logger.error(f"Error saving disk cache: {e}")
    
    async def force_refresh(self):
        """Force refresh of server data (for manual updates)."""
        async with self._lock:
            self._servers_cache = None
            self._cache_timestamp = None
            self._ip_index.clear()
            await self._fetch_and_cache_servers()


# Global instance
vpn_location_service = VPNLocationService()
