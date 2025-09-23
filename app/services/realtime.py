"""
Real-time data service for WebSocket updates
"""
import asyncio
import json
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
        self.update_interval = 2  # 2 seconds
    
    async def start(self):
        """Start the real-time data collection and broadcasting service"""
        self.running = True
        logger.info("Starting real-time service")
        
        while self.running:
            try:
                current_data = await self.collect_all_data()
                
                # Only broadcast if data has changed and there are connected clients
                if current_data != self.last_data and manager.active_connections:
                    await manager.broadcast({
                        "type": "update",
                        "data": current_data,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    self.last_data = current_data
                    
            except Exception as e:
                logger.error(f"Error in real-time service: {e}")
            
            await asyncio.sleep(self.update_interval)
    
    def stop(self):
        """Stop the real-time service"""
        self.running = False
        logger.info("Stopping real-time service")
    
    async def collect_all_data(self) -> Dict[str, Any]:
        """Collect data from all relevant sources"""
        try:
            # Get engines
            engines = state.list_engines()
            
            # Get active streams
            streams = state.list_streams(status="started")
            
            # Get VPN status (with error handling)
            try:
                vpn = get_vpn_status()
            except Exception as e:
                logger.warning(f"Failed to get VPN status: {e}")
                vpn = {"enabled": False}
            
            # Get stream stats for active streams
            stream_stats = {}
            for stream in streams:
                try:
                    stats = state.get_stream_stats(stream.id)
                    if stats:
                        # Get the latest stat
                        stream_stats[stream.id] = stats[-1].model_dump() if stats else None
                except Exception as e:
                    logger.warning(f"Failed to get stats for stream {stream.id}: {e}")
                    stream_stats[stream.id] = None
            
            return {
                "engines": [engine.model_dump() for engine in engines],
                "streams": [stream.model_dump() for stream in streams],
                "stream_stats": stream_stats,
                "vpn": vpn
            }
            
        except Exception as e:
            logger.error(f"Error collecting data: {e}")
            return {"engines": [], "streams": [], "stream_stats": {}, "vpn": {"enabled": False}}

# Global instance
realtime_service = RealtimeService()