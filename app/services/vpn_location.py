"""
VPN Location Service - DEPRECATED

⚠️ DEPRECATED: This service is no longer used.

VPN location information is now obtained directly from:
- Provider: VPN_SERVICE_PROVIDER docker environment variable (see gluetun.get_vpn_provider)
- Location: Gluetun's /v1/publicip/ip endpoint (see gluetun.get_vpn_public_ip_info)

This file is kept for backward compatibility but is no longer actively used.
The servers.json matching logic has been replaced with direct API calls to Gluetun.
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

# Fallback IP geolocation service (free, no API key required)
# Used when IP is not found in Gluetun servers index
IP_GEOLOCATION_API_URL = "http://ip-api.com/json/{ip}?fields=status,country,city,isp"
GEOLOCATION_CACHE_FILE = Path("/tmp/ip_geolocation_cache.json")
GEOLOCATION_CACHE_DURATION_HOURS = 24 * 7  # Cache for 7 days (IPs don't change often)


class VPNLocationService:
    """Service to match VPN IPs to server locations."""
    
    def __init__(self):
        self._servers_cache: Optional[Dict] = None
        self._cache_timestamp: Optional[datetime] = None
        self._ip_index: Dict[str, Dict] = {}  # IP -> server info mapping
        self._lock = asyncio.Lock()
        self._geolocation_cache: Dict[str, Dict] = {}  # IP -> geolocation data cache
        self._geolocation_cache_timestamp: Optional[datetime] = None
    
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
        
        First tries to lookup in Gluetun servers index, then falls back to
        IP geolocation API if not found.
        
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
        
        # First, try to lookup in Gluetun servers index
        server_info = self._ip_index.get(public_ip)
        if server_info:
            logger.debug(f"Found location for IP {public_ip} in Gluetun index: {server_info}")
            return server_info
        
        # Not found in Gluetun index, try IP geolocation API as fallback
        logger.debug(f"IP {public_ip} not found in Gluetun index, trying IP geolocation API")
        geolocation_info = await self._get_location_from_geolocation_api(public_ip)
        if geolocation_info:
            logger.debug(f"Found location for IP {public_ip} via geolocation API: {geolocation_info}")
            return geolocation_info
        
        logger.debug(f"No location found for IP {public_ip} from any source")
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
        
        # Track statistics for verbose logging
        provider_stats = {}
        total_servers = 0
        total_ips = 0
        
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
            
            provider_server_count = len(servers)
            provider_ip_count = 0
            total_servers += provider_server_count
            
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
                        provider_ip_count += 1
                        total_ips += 1
            
            # Track stats per provider
            if provider_ip_count > 0:
                provider_stats[provider_name] = {
                    'servers': provider_server_count,
                    'ips': provider_ip_count
                }
        
        logger.info(f"Built IP index with {len(self._ip_index)} entries from {total_servers} servers across {len(provider_stats)} providers")
        
        # Log verbose provider statistics
        if provider_stats:
            logger.info("VPN location index statistics by provider:")
            for provider, stats in sorted(provider_stats.items(), key=lambda x: x[1]['ips'], reverse=True):
                logger.info(f"  - {provider}: {stats['servers']} servers, {stats['ips']} IPs")
        
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
    
    async def _get_location_from_geolocation_api(self, public_ip: str) -> Optional[Dict[str, str]]:
        """
        Get location information from IP geolocation API (fallback method).
        
        Uses ip-api.com free service which doesn't require an API key.
        Results are cached to reduce API calls.
        
        Args:
            public_ip: The public IP address to lookup
            
        Returns:
            Dict with 'provider', 'country', 'city' keys, or None if lookup fails
        """
        # Load geolocation cache if not loaded
        await self._ensure_geolocation_cache()
        
        # Check if IP is in cache and cache is fresh
        if public_ip in self._geolocation_cache:
            cache_age = datetime.now(timezone.utc) - self._geolocation_cache_timestamp
            if cache_age < timedelta(hours=GEOLOCATION_CACHE_DURATION_HOURS):
                logger.debug(f"Using cached geolocation data for IP {public_ip}")
                return self._geolocation_cache[public_ip]
        
        # Fetch from API
        try:
            api_url = IP_GEOLOCATION_API_URL.format(ip=public_ip)
            logger.debug(f"Fetching geolocation data from API for IP {public_ip}")
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(api_url)
                response.raise_for_status()
                data = response.json()
            
            # Check if lookup was successful
            if data.get("status") != "success":
                logger.warning(f"IP geolocation API returned non-success status for {public_ip}: {data.get('status')}")
                return None
            
            # Extract location info
            location_info = {
                'provider': data.get('isp', 'Unknown'),
                'country': data.get('country', 'Unknown'),
                'city': data.get('city', 'Unknown')
            }
            
            # Cache the result
            self._geolocation_cache[public_ip] = location_info
            await self._save_geolocation_cache()
            
            logger.info(f"Retrieved location for IP {public_ip} from geolocation API: {location_info}")
            return location_info
            
        except Exception as e:
            logger.error(f"Error fetching location from geolocation API for IP {public_ip}: {e}")
            return None
    
    async def _ensure_geolocation_cache(self):
        """Ensure geolocation cache is loaded from disk if available."""
        if self._geolocation_cache_timestamp is not None:
            return  # Already loaded
        
        try:
            if not GEOLOCATION_CACHE_FILE.exists():
                self._geolocation_cache = {}
                self._geolocation_cache_timestamp = datetime.now(timezone.utc)
                return
            
            # Load from disk
            with open(GEOLOCATION_CACHE_FILE, 'r') as f:
                data = json.load(f)
            
            self._geolocation_cache = data.get('cache', {})
            timestamp_str = data.get('timestamp')
            if timestamp_str:
                self._geolocation_cache_timestamp = datetime.fromisoformat(timestamp_str)
            else:
                self._geolocation_cache_timestamp = datetime.now(timezone.utc)
            
            logger.debug(f"Loaded {len(self._geolocation_cache)} geolocation cache entries from disk")
            
        except Exception as e:
            logger.error(f"Error loading geolocation cache from disk: {e}")
            self._geolocation_cache = {}
            self._geolocation_cache_timestamp = datetime.now(timezone.utc)
    
    async def _save_geolocation_cache(self):
        """Save geolocation cache to disk."""
        try:
            cache_data = {
                'cache': self._geolocation_cache,
                'timestamp': self._geolocation_cache_timestamp.isoformat()
            }
            
            # Ensure parent directory exists
            GEOLOCATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to temp file first, then rename (atomic operation)
            temp_file = GEOLOCATION_CACHE_FILE.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(cache_data, f)
            
            temp_file.replace(GEOLOCATION_CACHE_FILE)
            logger.debug(f"Saved geolocation cache to disk: {GEOLOCATION_CACHE_FILE}")
            
        except Exception as e:
            logger.error(f"Error saving geolocation cache to disk: {e}")

    async def force_refresh(self):
        """Force refresh of server data (for manual updates)."""
        async with self._lock:
            self._servers_cache = None
            self._cache_timestamp = None
            self._ip_index.clear()
            await self._fetch_and_cache_servers()
    
    async def initialize_at_startup(self):
        """
        Initialize VPN location service at startup.
        
        This eagerly fetches and indexes the Gluetun server list to be ready
        for VPN location lookups. Should be called after VPN containers become healthy.
        """
        logger.info("Initializing VPN location service...")
        await self._ensure_server_data()
        
        if self.is_ready():
            logger.info(f"VPN location service initialized successfully with {len(self._ip_index)} server IPs")
        else:
            logger.warning("VPN location service initialization completed but IP index is empty")


# Global instance
vpn_location_service = VPNLocationService()
