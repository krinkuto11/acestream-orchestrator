"""
Real-time data service for WebSocket updates
"""
import asyncio
import json
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from ..services.state import state
from ..services.gluetun import get_vpn_status
from ..websockets.websocket_manager import manager

logger = logging.getLogger(__name__)

class RealtimeService:
    def __init__(self):
        self.last_data: Dict[str, Any] = {}
        self.running = False
        self.update_interval = 0.5  # 500ms for better responsiveness
        self.last_hash = None  # For better change detection
        
        # VPN status cache to reduce blocking calls
        self._vpn_cache: Dict[str, Any] = {"enabled": False}
        self._vpn_cache_time: Optional[datetime] = None
        self._vpn_cache_ttl_seconds: int = 5  # 5 second cache for VPN status
    
    async def start(self):
        """Start the real-time data collection and broadcasting service"""
        self.running = True
        logger.info(f"Starting real-time service with {self.update_interval}s interval")
        
        while self.running:
            try:
                # Only collect and process data if there are active WebSocket connections
                if not manager.active_connections:
                    await asyncio.sleep(self.update_interval)
                    continue
                
                current_data = await self.collect_all_data()
                
                # Create a hash of the important data for efficient change detection
                data_str = json.dumps({
                    "engines_count": len(current_data.get("engines", [])),
                    "streams_count": len(current_data.get("streams", [])),
                    "engines": current_data.get("engines", []),
                    "streams": current_data.get("streams", []),
                    "stream_stats": current_data.get("stream_stats", {}),
                    "vpn": current_data.get("vpn", {})
                }, sort_keys=True)
                current_hash = hashlib.md5(data_str.encode()).hexdigest()
                
                # Only broadcast if data has actually changed
                if current_hash != self.last_hash:
                    await manager.broadcast({
                        "type": "update",
                        "data": current_data,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    self.last_data = current_data
                    self.last_hash = current_hash
                    logger.debug(f"Broadcasted update to {len(manager.active_connections)} clients")
                    
            except Exception as e:
                logger.error(f"Error in real-time service: {e}")
            
            await asyncio.sleep(self.update_interval)
    
    def stop(self):
        """Stop the real-time service"""
        self.running = False
        logger.info("Stopping real-time service")
    
    async def collect_all_data(self) -> Dict[str, Any]:
        """Collect data from all relevant sources with optimized performance."""
        try:
            # Get a snapshot of all state data with minimal lock time
            snapshot = state.get_realtime_snapshot()
            engines = snapshot["engines"]
            streams = [s for s in snapshot["streams"] if s.status == "started"]  # Filter for active streams
            all_stream_stats = snapshot["stream_stats"]
            
            # Prepare stream stats data from the snapshot
            stream_stats = {}
            for stream in streams:
                stats_list = all_stream_stats.get(stream.id, [])
                if stats_list:
                    # Get the latest stat
                    stream_stats[stream.id] = stats_list[-1].model_dump()
                else:
                    stream_stats[stream.id] = None
            
            # Get VPN status asynchronously with caching
            vpn = await self._get_vpn_status_cached()
            
            return {
                "engines": [engine.model_dump() for engine in engines],
                "streams": [stream.model_dump() for stream in streams],
                "stream_stats": stream_stats,
                "vpn": vpn
            }
            
        except Exception as e:
            logger.error(f"Error collecting data: {e}")
            return {"engines": [], "streams": [], "stream_stats": {}, "vpn": {"enabled": False}}
    
    async def _get_vpn_status_cached(self) -> Dict[str, Any]:
        """Get VPN status with caching to reduce blocking calls."""
        try:
            # Check cache validity
            if self._vpn_cache_time is not None:
                cache_age = (datetime.utcnow() - self._vpn_cache_time).total_seconds()
                if cache_age < self._vpn_cache_ttl_seconds:
                    return self._vpn_cache
            
            # Run VPN status check in a thread to avoid blocking the event loop
            def get_vpn_status_sync():
                try:
                    return get_vpn_status()
                except Exception as e:
                    logger.warning(f"Failed to get VPN status: {e}")
                    return {"enabled": False}
            
            import asyncio
            import concurrent.futures
            
            # Run in thread pool with short timeout
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                try:
                    vpn = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(executor, get_vpn_status_sync),
                        timeout=1.0  # 1 second timeout
                    )
                    # Cache the result
                    self._vpn_cache = vpn
                    self._vpn_cache_time = datetime.utcnow()
                    return vpn
                except asyncio.TimeoutError:
                    logger.warning("VPN status check timed out, using cached data")
                    return self._vpn_cache
                    
        except Exception as e:
            logger.warning(f"Error getting VPN status: {e}")
            return self._vpn_cache

# Global instance
realtime_service = RealtimeService()