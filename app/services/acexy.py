"""
Acexy proxy integration service.

This module provides:
- Client for fetching stream information from Acexy's /ace/streams endpoint
- Periodic sync job to reconcile orchestrator state with Acexy streams
- Cleanup logic for streams not present in Acexy (stale streams)
"""

import asyncio
import logging
import httpx
from datetime import datetime, timezone
from typing import Optional, List, Dict, Set
from dataclasses import dataclass

from ..core.config import cfg
from .state import state
from ..models.schemas import StreamEndedEvent
from .event_logger import event_logger
from .metrics import orch_stale_streams_detected

logger = logging.getLogger(__name__)


@dataclass
class AcexyStream:
    """Represents a stream from Acexy's /ace/streams endpoint."""
    id: str
    playback_url: str
    stat_url: str
    command_url: str
    clients: int
    created_at: str
    has_player: bool
    engine_host: str
    engine_port: int
    engine_container_id: Optional[str] = None


class AcexyClient:
    """Client for communicating with Acexy proxy API."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._healthy: Optional[bool] = None
        self._last_health_check: Optional[datetime] = None
        
    async def check_health(self) -> bool:
        """
        Check if Acexy is healthy by calling the /ace/streams endpoint.
        
        Returns:
            True if Acexy is healthy and responding, False otherwise.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/ace/streams")
                is_healthy = response.status_code == 200
                
                if is_healthy != self._healthy:
                    if is_healthy:
                        logger.info(f"Acexy at {self.base_url} is now healthy")
                    else:
                        logger.warning(f"Acexy at {self.base_url} is unhealthy (status: {response.status_code})")
                
                self._healthy = is_healthy
                self._last_health_check = datetime.now(timezone.utc)
                return is_healthy
        except Exception as e:
            if self._healthy is not False:
                logger.error(f"Failed to connect to Acexy at {self.base_url}: {e}")
            self._healthy = False
            self._last_health_check = datetime.now(timezone.utc)
            return False
    
    def is_healthy(self) -> Optional[bool]:
        """Get the last known health status."""
        return self._healthy
    
    async def get_streams(self) -> Optional[List[AcexyStream]]:
        """
        Fetch streams from Acexy's /ace/streams endpoint.
        
        Returns:
            List of AcexyStream objects, or None if the request failed.
        
        Example response from Acexy:
        {
            "streams": [
                {
                    "id": "{id: 38e9ae1ee0c96d7c6187c9c4cc60ffccb565bdf7}",
                    "playback_url": "http://gluetun:19000/ace/r/.../...",
                    "stat_url": "http://gluetun:19000/ace/stat/.../...",
                    "command_url": "http://gluetun:19000/ace/cmd/.../...",
                    "clients": 1,
                    "created_at": "2025-11-30T17:56:15.316407348Z",
                    "has_player": true,
                    "engine_host": "gluetun",
                    "engine_port": 19000,
                    "engine_container_id": "dc70d2ab2a46f285d4478e6025faedb85..."
                }
            ],
            "total_streams": 2
        }
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/ace/streams")
                
                if response.status_code != 200:
                    logger.warning(f"Acexy returned status {response.status_code} for /ace/streams")
                    return None
                
                data = response.json()
                streams_data = data.get("streams", [])
                
                streams = []
                for stream_data in streams_data:
                    try:
                        stream = AcexyStream(
                            id=stream_data.get("id", ""),
                            playback_url=stream_data.get("playback_url", ""),
                            stat_url=stream_data.get("stat_url", ""),
                            command_url=stream_data.get("command_url", ""),
                            clients=stream_data.get("clients", 0),
                            created_at=stream_data.get("created_at", ""),
                            has_player=stream_data.get("has_player", False),
                            engine_host=stream_data.get("engine_host", ""),
                            engine_port=stream_data.get("engine_port", 0),
                            engine_container_id=stream_data.get("engine_container_id")
                        )
                        streams.append(stream)
                    except Exception as e:
                        logger.warning(f"Failed to parse stream data: {e}")
                        continue
                
                logger.debug(f"Fetched {len(streams)} streams from Acexy")
                return streams
                
        except httpx.TimeoutException:
            logger.error(f"Timeout fetching streams from Acexy at {self.base_url}")
            return None
        except Exception as e:
            logger.error(f"Error fetching streams from Acexy: {e}")
            return None


class AcexySyncService:
    """
    Background service that syncs orchestrator state with Acexy streams.
    
    This service:
    1. Periodically fetches streams from Acexy's /ace/streams endpoint
    2. Compares with the orchestrator's internal state
    3. Ends streams that are present in orchestrator but not in Acexy (stale streams)
    """
    
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._client: Optional[AcexyClient] = None
        
    async def start(self):
        """Start the sync service."""
        if not cfg.ACEXY_ENABLED or not cfg.ACEXY_URL:
            logger.info("Acexy sync service disabled - ACEXY_ENABLED is false or ACEXY_URL not set")
            return
        
        if self._task and not self._task.done():
            return
        
        self._client = AcexyClient(cfg.ACEXY_URL)
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(f"Acexy sync service started - syncing with {cfg.ACEXY_URL} every {cfg.ACEXY_SYNC_INTERVAL_S}s")
        
        # Log startup event
        event_logger.log_event(
            event_type="system",
            category="acexy_sync_started",
            message=f"Acexy sync service started, syncing with {cfg.ACEXY_URL}",
            details={"url": cfg.ACEXY_URL, "interval_seconds": cfg.ACEXY_SYNC_INTERVAL_S}
        )
    
    async def stop(self):
        """Stop the sync service."""
        self._stop.set()
        if self._task:
            await self._task
            logger.info("Acexy sync service stopped")
    
    async def _run(self):
        """Main sync loop."""
        while not self._stop.is_set():
            try:
                await self._sync()
            except Exception as e:
                logger.exception(f"Error during Acexy sync: {e}")
            
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=cfg.ACEXY_SYNC_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    
    async def _sync(self):
        """
        Perform a sync between orchestrator state and Acexy streams.
        
        This method:
        1. Fetches current streams from Acexy
        2. Gets current streams from orchestrator state
        3. Identifies streams in orchestrator that are not in Acexy
        4. Ends those stale streams
        """
        if not self._client:
            return
        
        # First check if Acexy is healthy
        is_healthy = await self._client.check_health()
        if not is_healthy:
            logger.debug("Acexy is not healthy, skipping sync")
            return
        
        # Fetch streams from Acexy
        acexy_streams = await self._client.get_streams()
        if acexy_streams is None:
            logger.debug("Could not fetch streams from Acexy, skipping sync")
            return
        
        # Build a set of stream identifiers from Acexy
        # We use stat_url as the unique identifier since it contains the playback session ID
        acexy_stat_urls: Set[str] = {stream.stat_url for stream in acexy_streams}
        
        # Get orchestrator's active streams
        orchestrator_streams = state.list_streams(status="started")
        
        if not orchestrator_streams:
            logger.debug("No active streams in orchestrator, nothing to sync")
            return
        
        # Find streams that are in orchestrator but not in Acexy
        stale_streams = []
        for stream in orchestrator_streams:
            if stream.stat_url and stream.stat_url not in acexy_stat_urls:
                stale_streams.append(stream)
        
        if not stale_streams:
            logger.debug(f"All {len(orchestrator_streams)} orchestrator streams are present in Acexy")
            return
        
        # Log and end stale streams
        logger.info(f"Found {len(stale_streams)} stale streams not present in Acexy (out of {len(orchestrator_streams)} total)")
        
        for stream in stale_streams:
            try:
                logger.info(f"Ending stale stream {stream.id} - not present in Acexy")
                
                # End the stream
                state.on_stream_ended(StreamEndedEvent(
                    container_id=stream.container_id,
                    stream_id=stream.id,
                    reason="acexy_sync_stale"
                ))
                
                # Update metrics
                orch_stale_streams_detected.inc()
                
                # Log event
                event_logger.log_event(
                    event_type="stream",
                    category="ended",
                    message=f"Stream ended by Acexy sync: {stream.id[:16]}... (not present in Acexy)",
                    details={
                        "reason": "acexy_sync_stale",
                        "key_type": stream.key_type,
                        "key": stream.key,
                        "stat_url": stream.stat_url
                    },
                    container_id=stream.container_id,
                    stream_id=stream.id
                )
                
            except Exception as e:
                logger.error(f"Failed to end stale stream {stream.id}: {e}")
    
    def get_status(self) -> Dict:
        """Get the status of the Acexy sync service."""
        return {
            "enabled": cfg.ACEXY_ENABLED,
            "url": cfg.ACEXY_URL if cfg.ACEXY_ENABLED else None,
            "healthy": self._client.is_healthy() if self._client else None,
            "last_health_check": self._client._last_health_check.isoformat() if self._client and self._client._last_health_check else None,
            "sync_interval_seconds": cfg.ACEXY_SYNC_INTERVAL_S
        }


# Global instance
acexy_sync_service = AcexySyncService()
