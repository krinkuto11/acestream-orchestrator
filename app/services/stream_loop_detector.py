import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional
from .state import state
from ..core.config import cfg
from ..models.schemas import StreamEndedEvent

logger = logging.getLogger(__name__)


class StreamLoopDetector:
    """
    Stream loop detector service.
    
    This service periodically checks the live_last field of active streams.
    If live_last is behind current time by more than the configured threshold,
    it indicates the stream is looping (no new data being fed into the network),
    and the stream will be automatically stopped.
    
    The threshold is configurable via STREAM_LOOP_DETECTION_THRESHOLD_S.
    Detection can be enabled/disabled via STREAM_LOOP_DETECTION_ENABLED.
    """
    def __init__(self):
        self._task = None
        self._stop = asyncio.Event()
        self._first_check_completed = {}  # Track which streams have had their first valid check
        self._check_interval_s = 10  # Check every 10 seconds

    async def start(self):
        if not cfg.STREAM_LOOP_DETECTION_ENABLED:
            logger.info("Stream loop detection is disabled")
            return
        
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(f"Stream loop detector started (threshold: {cfg.STREAM_LOOP_DETECTION_THRESHOLD_S}s)")

    async def stop(self):
        self._stop.set()
        if self._task:
            await self._task

    async def _run(self):
        async with httpx.AsyncClient(timeout=3.0) as client:
            while not self._stop.is_set():
                try:
                    await self._check_all_streams(client)
                except Exception:
                    logger.exception("Error in stream loop detector")
                
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._check_interval_s)
                except asyncio.TimeoutError:
                    pass

    async def _check_all_streams(self, client: httpx.AsyncClient):
        """Check all active streams for loop detection."""
        streams = state.list_streams(status="started")
        
        for stream in streams:
            try:
                await self._check_stream(client, stream)
            except Exception:
                logger.exception(f"Error checking stream {stream.id} for loop")

    async def _check_stream(self, client: httpx.AsyncClient, stream):
        """Check a single stream for loop detection."""
        # Only check live streams
        if not stream.is_live:
            return
        
        # Only check if stream has a stat URL
        if not stream.stat_url:
            return
        
        try:
            # Fetch current stats from the engine
            response = await client.get(stream.stat_url)
            if response.status_code >= 300:
                logger.debug(f"Non-success response from stat URL for stream {stream.id}: {response.status_code}")
                return
            
            data = response.json()
            payload = data.get("response")
            if not payload:
                return
            
            livepos = payload.get("livepos")
            if not livepos:
                return
            
            # Get live_last (with fallbacks for compatibility)
            live_last_str = livepos.get("live_last") or livepos.get("last_ts") or livepos.get("last")
            if not live_last_str:
                return
            
            try:
                live_last = int(live_last_str)
            except (ValueError, TypeError):
                logger.warning(f"Invalid live_last value for stream {stream.id}: {live_last_str}")
                return
            
            # Validate timestamp is reasonable (between 2020 and 2050)
            MIN_VALID_TIMESTAMP = 1577836800  # 2020-01-01
            MAX_VALID_TIMESTAMP = 2524608000  # 2050-01-01
            if live_last < MIN_VALID_TIMESTAMP or live_last > MAX_VALID_TIMESTAMP:
                logger.debug(f"Ignoring invalid timestamp for stream {stream.id}: {live_last}")
                return
            
            # Mark that we've successfully read the stream at least once
            if stream.id not in self._first_check_completed:
                self._first_check_completed[stream.id] = True
                logger.debug(f"First valid livepos check completed for stream {stream.id}")
            
            # Calculate how far behind we are
            current_time = int(datetime.now(timezone.utc).timestamp())
            time_behind = current_time - live_last
            
            logger.debug(f"Stream {stream.id[:16]}... live_last: {live_last}, current: {current_time}, behind: {time_behind}s")
            
            # Check if we're beyond the threshold
            if time_behind > cfg.STREAM_LOOP_DETECTION_THRESHOLD_S:
                logger.warning(
                    f"Stream {stream.id} is {time_behind}s behind live (threshold: {cfg.STREAM_LOOP_DETECTION_THRESHOLD_S}s). "
                    f"Stopping stream due to loop detection."
                )
                
                # Stop the stream via command URL
                await self._stop_stream(client, stream, time_behind)
                
        except Exception:
            logger.exception(f"Error checking livepos for stream {stream.id}")

    async def _stop_stream(self, client: httpx.AsyncClient, stream, time_behind: int):
        """Stop a stream that has been detected as looping."""
        try:
            # Call command URL with method=stop
            if stream.command_url:
                stop_url = f"{stream.command_url}?method=stop"
                logger.info(f"Stopping looping stream {stream.id} via command URL: {stop_url}")
                
                try:
                    response = await client.get(stop_url)
                    if response.status_code >= 300:
                        logger.warning(f"Stop command returned non-success status {response.status_code} for stream {stream.id}")
                except Exception as e:
                    logger.warning(f"Failed to send stop command for stream {stream.id}: {e}")
            
            # Mark the stream as ended in our state
            state.on_stream_ended(StreamEndedEvent(
                container_id=stream.container_id,
                stream_id=stream.id,
                reason=f"loop_detection_threshold_exceeded_{time_behind}s"
            ))
            
            # Remove from first_check_completed tracking
            if stream.id in self._first_check_completed:
                del self._first_check_completed[stream.id]
            
            # Log event
            from .event_logger import event_logger
            event_logger.log_event(
                event_type="stream",
                category="ended",
                message=f"Stream stopped due to loop detection ({time_behind}s behind live)",
                details={
                    "stream_id": stream.id,
                    "time_behind_seconds": time_behind,
                    "threshold_seconds": cfg.STREAM_LOOP_DETECTION_THRESHOLD_S
                },
                stream_id=stream.id,
                container_id=stream.container_id
            )
            
        except Exception:
            logger.exception(f"Error stopping looping stream {stream.id}")


stream_loop_detector = StreamLoopDetector()
