import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from .state import state
from ..models.schemas import StreamStatSnapshot, StreamEndedEvent
from ..core.config import cfg
from .metrics import orch_stale_streams_detected, on_stream_stat_update

logger = logging.getLogger(__name__)


class InactiveStreamTracker:
    """Tracks when streams become inactive based on multiple conditions."""
    
    def __init__(self):
        # Track when each condition first became true for each stream
        # Format: {stream_id: {"livepos_inactive_since": datetime, "prebuf_since": datetime, "zero_speed_since": datetime, "low_speed_since": datetime}}
        self._inactive_conditions: Dict[str, Dict[str, Optional[datetime]]] = {}
        # Track last known values to detect changes
        # Format: {stream_id: {"last_pos": value, "last_status": value, "last_speed_down": value, "last_speed_up": value}}
        self._last_values: Dict[str, Dict[str, Any]] = {}
        # Thresholds from config
        self.LIVEPOS_THRESHOLD_S = cfg.INACTIVE_LIVEPOS_THRESHOLD_S
        self.PREBUF_THRESHOLD_S = cfg.INACTIVE_PREBUF_THRESHOLD_S
        self.ZERO_SPEED_THRESHOLD_S = cfg.INACTIVE_ZERO_SPEED_THRESHOLD_S
        self.LOW_SPEED_THRESHOLD_KB = cfg.INACTIVE_LOW_SPEED_THRESHOLD_KB
        self.LOW_SPEED_THRESHOLD_S = cfg.INACTIVE_LOW_SPEED_THRESHOLD_S
    
    def update_stream(self, stream_id: str, livepos_pos: Optional[int], status: Optional[str], 
                     speed_down: Optional[int], speed_up: Optional[int]) -> bool:
        """
        Update stream state and check if it should be stopped.
        
        Returns True if any condition has been met for longer than its threshold.
        """
        now = datetime.now(timezone.utc)
        
        # Initialize tracking for new streams
        if stream_id not in self._inactive_conditions:
            self._inactive_conditions[stream_id] = {
                "livepos_inactive_since": None,
                "prebuf_since": None,
                "zero_speed_since": None,
                "low_speed_since": None
            }
            self._last_values[stream_id] = {}
        
        conditions = self._inactive_conditions[stream_id]
        last_vals = self._last_values[stream_id]
        
        # Check condition 1: livepos/pos unchanged for >LIVEPOS_THRESHOLD_S seconds
        if livepos_pos is not None:
            last_pos = last_vals.get("last_pos")
            if last_pos is not None and last_pos == livepos_pos:
                # Position hasn't changed
                if conditions["livepos_inactive_since"] is None:
                    conditions["livepos_inactive_since"] = now
                    logger.debug(f"Stream {stream_id}: livepos.pos unchanged at {livepos_pos}, started tracking")
            else:
                # Position changed, reset
                if conditions["livepos_inactive_since"] is not None:
                    logger.debug(f"Stream {stream_id}: livepos.pos changed from {last_pos} to {livepos_pos}, reset tracking")
                conditions["livepos_inactive_since"] = None
            last_vals["last_pos"] = livepos_pos
        else:
            # No livepos data, can't track this condition
            conditions["livepos_inactive_since"] = None
        
        # Check condition 2: status="prebuf" for >PREBUF_THRESHOLD_S seconds
        if status is not None:
            if status == "prebuf":
                if conditions["prebuf_since"] is None:
                    conditions["prebuf_since"] = now
                    logger.debug(f"Stream {stream_id}: status=prebuf, started tracking")
            else:
                if conditions["prebuf_since"] is not None:
                    logger.debug(f"Stream {stream_id}: status changed from prebuf to {status}, reset tracking")
                conditions["prebuf_since"] = None
            last_vals["last_status"] = status
        else:
            conditions["prebuf_since"] = None
        
        # Check condition 3: download/upload speed both 0 for >ZERO_SPEED_THRESHOLD_S seconds
        # Use explicit comparison to handle None vs 0
        speed_down_is_zero = (speed_down is not None and speed_down == 0)
        speed_up_is_zero = (speed_up is not None and speed_up == 0)
        
        if speed_down_is_zero and speed_up_is_zero:
            if conditions["zero_speed_since"] is None:
                conditions["zero_speed_since"] = now
                logger.debug(f"Stream {stream_id}: both speeds are 0, started tracking")
        else:
            if conditions["zero_speed_since"] is not None:
                logger.debug(f"Stream {stream_id}: speeds changed (down={speed_down}, up={speed_up}), reset tracking")
            conditions["zero_speed_since"] = None
        
        # Check condition 4: download speed below threshold for >LOW_SPEED_THRESHOLD_S seconds
        # Both speed_down and LOW_SPEED_THRESHOLD_KB are in KB/s units
        if speed_down is not None:
            if speed_down < self.LOW_SPEED_THRESHOLD_KB:
                if conditions["low_speed_since"] is None:
                    conditions["low_speed_since"] = now
                    logger.debug(f"Stream {stream_id}: download speed {speed_down} KB/s below threshold {self.LOW_SPEED_THRESHOLD_KB} KB/s, started tracking")
            else:
                if conditions["low_speed_since"] is not None:
                    logger.debug(f"Stream {stream_id}: download speed {speed_down} KB/s above threshold, reset tracking")
                conditions["low_speed_since"] = None
        else:
            conditions["low_speed_since"] = None
        
        last_vals["last_speed_down"] = speed_down
        last_vals["last_speed_up"] = speed_up
        
        # Check if any condition has been true for longer than its threshold
        if conditions["livepos_inactive_since"] is not None:
            elapsed = (now - conditions["livepos_inactive_since"]).total_seconds()
            if elapsed >= self.LIVEPOS_THRESHOLD_S:
                logger.info(f"Stream {stream_id}: inactive condition 'livepos_inactive_since' met for {elapsed:.1f}s (threshold: {self.LIVEPOS_THRESHOLD_S}s)")
                return True
        
        if conditions["prebuf_since"] is not None:
            elapsed = (now - conditions["prebuf_since"]).total_seconds()
            if elapsed >= self.PREBUF_THRESHOLD_S:
                logger.info(f"Stream {stream_id}: inactive condition 'prebuf_since' met for {elapsed:.1f}s (threshold: {self.PREBUF_THRESHOLD_S}s)")
                return True
        
        if conditions["zero_speed_since"] is not None:
            elapsed = (now - conditions["zero_speed_since"]).total_seconds()
            if elapsed >= self.ZERO_SPEED_THRESHOLD_S:
                logger.info(f"Stream {stream_id}: inactive condition 'zero_speed_since' met for {elapsed:.1f}s (threshold: {self.ZERO_SPEED_THRESHOLD_S}s)")
                return True
        
        if conditions["low_speed_since"] is not None:
            elapsed = (now - conditions["low_speed_since"]).total_seconds()
            if elapsed >= self.LOW_SPEED_THRESHOLD_S:
                logger.info(f"Stream {stream_id}: inactive condition 'low_speed_since' met for {elapsed:.1f}s (threshold: {self.LOW_SPEED_THRESHOLD_S}s)")
                return True
        
        return False
    
    def remove_stream(self, stream_id: str):
        """Remove tracking data for a stream that has ended."""
        self._inactive_conditions.pop(stream_id, None)
        self._last_values.pop(stream_id, None)
    
    def get_inactive_info(self, stream_id: str) -> Optional[Dict]:
        """Get current inactive tracking info for a stream (for debugging)."""
        if stream_id not in self._inactive_conditions:
            return None
        
        now = datetime.now(timezone.utc)
        conditions = self._inactive_conditions[stream_id]
        info = {}
        
        for condition_name, since_time in conditions.items():
            if since_time is not None:
                elapsed = (now - since_time).total_seconds()
                info[condition_name] = {
                    "since": since_time.isoformat(),
                    "elapsed_seconds": elapsed
                }
        
        return info if info else None


class Collector:
    """
    Stream statistics collector and stale stream detector.
    
    This service is the PRIMARY mechanism for detecting stale streams.
    It periodically polls stat URLs for all active streams and:
    1. Collects stream statistics (peers, speed, etc.)
    2. Detects stale streams when the engine returns "unknown playback session id"
    3. Detects inactive streams based on multiple conditions:
       - livepos/pos unchanged for >INACTIVE_LIVEPOS_THRESHOLD_S seconds (default: 15s)
       - status="prebuf" for >INACTIVE_PREBUF_THRESHOLD_S seconds (default: 10s)
       - download/upload speed both 0 for >INACTIVE_ZERO_SPEED_THRESHOLD_S seconds (default: 10s)
       - download speed below INACTIVE_LOW_SPEED_THRESHOLD_KB KB/s for >INACTIVE_LOW_SPEED_THRESHOLD_S seconds (default: <400 KB/s for 20s)
    4. Automatically stops inactive streams via command URL
    
    With the acexy proxy now being stateless (only sending start events),
    this collector is the PRIMARY mechanism for detecting and cleaning up stale streams.
    The COLLECT_INTERVAL_S should be kept low (default 2 seconds) for quick detection.
    """
    def __init__(self):
        self._task = None
        self._stop = asyncio.Event()
        self._inactive_tracker = InactiveStreamTracker()

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            await self._task

    async def _run(self):
        async with httpx.AsyncClient(timeout=3.0) as client:
            while not self._stop.is_set():
                streams = state.list_streams(status="started")
                # Pass both stat_url and command_url for each stream
                tasks = [self._collect_one(client, s.id, s.stat_url, s.command_url) for s in streams]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=cfg.COLLECT_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass

    async def _collect_one(self, client: httpx.AsyncClient, stream_id: str, stat_url: str, command_url: str):
        try:
            logger.debug(f"Collecting stats for stream_id={stream_id} url={stat_url}")
            r = await client.get(stat_url)
            logger.debug(f"HTTP {r.status_code} from {stat_url} for stream {stream_id}")
            if r.status_code >= 300:
                # Log response body at debug so operators can inspect redirects/errors
                try:
                    text = r.text
                except Exception:
                    text = "<unreadable response body>"
                logger.warning(f"Non-success response collecting stats for {stream_id} ({r.status_code}): {text}")
                return
            try:
                data = r.json()
            except Exception as e:
                # Response wasn't valid JSON — log the body to help debugging
                body = r.text if hasattr(r, 'text') else '<no-body>'
                logger.debug(f"Failed to parse JSON from {stat_url} for stream {stream_id}: {e}; body={body}")
                return

            # Check if the stream has stopped/is stale
            # When a stream has stopped, the engine returns: {"response": null, "error": "unknown playback session id"}
            if data.get("response") is None and data.get("error"):
                error_msg = data.get("error", "").lower()
                logger.debug(f"Stat endpoint reported error for {stream_id}: {data.get('error')}")
                if "unknown playback session id" in error_msg:
                    # Get the stream to find its container_id
                    stream = state.get_stream(stream_id)
                    if stream and stream.status == "started":
                        # Only log and end the stream if it's still marked as started
                        logger.info(f"Detected stale stream {stream_id}: {data.get('error')}")
                        await self._stop_stream(client, stream_id, stream.container_id, command_url, "stale_stream_detected")
                        orch_stale_streams_detected.inc()
                    else:
                        # Stream is already ended or doesn't exist - this is expected
                        logger.debug(f"Stale stream {stream_id} is already ended or doesn't exist, skipping")
                    return

            payload = data.get("response") or {}
            # Log the raw payload at debug level (truncated to avoid huge logs)
            try:
                raw_payload_str = str(payload)
                if len(raw_payload_str) > 2000:
                    raw_payload_str = raw_payload_str[:2000] + '...<truncated>'
            except Exception:
                raw_payload_str = '<unrepresentable payload>'
            logger.debug(f"Payload for {stream_id} from {stat_url}: {raw_payload_str}")

            # Handle both snake_case and camelCase field names from AceStream API
            # Some engine versions return speedDown/speedUp, others return speed_down/speed_up
            # Use explicit None check to preserve 0 values (0 is valid speed)
            speed_down_snake = payload.get("speed_down")
            speed_down_camel = payload.get("speedDown")
            speed_down = speed_down_snake if speed_down_snake is not None else speed_down_camel
            speed_up_snake = payload.get("speed_up")
            speed_up_camel = payload.get("speedUp")
            speed_up = speed_up_snake if speed_up_snake is not None else speed_up_camel

            # Log which keys were used so it's clear which engine version responded
            logger.debug(
                f"Selected speed values for {stream_id}: speed_down={speed_down} (snake={speed_down_snake} camel={speed_down_camel}), "
                f"speed_up={speed_up} (snake={speed_up_snake} camel={speed_up_camel})"
            )

            # Extract status field
            status = payload.get("status")
            
            # Extract livepos.pos field for live streams
            livepos_pos = None
            livepos_data = payload.get("livepos")
            if livepos_data and isinstance(livepos_data, dict):
                livepos_pos = livepos_data.get("pos")

            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=payload.get("peers"),
                speed_down=speed_down,
                speed_up=speed_up,
                downloaded=payload.get("downloaded"),
                uploaded=payload.get("uploaded"),
                status=status,
            )
            state.append_stat(stream_id, snap)
            logger.debug(f"Appended stat for {stream_id}: peers={snap.peers} speed_down={snap.speed_down} speed_up={snap.speed_up} downloaded={snap.downloaded} uploaded={snap.uploaded} status={snap.status}")

            # Update cumulative byte metrics
            try:
                on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
            except Exception:
                logger.exception(f"Error updating cumulative metrics for stream {stream_id}")
            
            # Check for inactive stream conditions
            should_stop = self._inactive_tracker.update_stream(
                stream_id, livepos_pos, status, speed_down, speed_up
            )
            
            if should_stop:
                # Stream has been inactive for >30 seconds, stop it
                stream = state.get_stream(stream_id)
                if stream and stream.status == "started":
                    inactive_info = self._inactive_tracker.get_inactive_info(stream_id)
                    logger.info(f"Stopping inactive stream {stream_id}. Inactive conditions: {inactive_info}")
                    await self._stop_stream(client, stream_id, stream.container_id, command_url, "inactive_stream_detected")
                    orch_stale_streams_detected.inc()  # Reuse existing metric for now
                    
        except Exception:
            logger.exception(f"Unhandled exception while collecting stats for {stream_id} from {stat_url}")
            return
    
    async def _stop_stream(self, client: httpx.AsyncClient, stream_id: str, container_id: str, 
                          command_url: str, reason: str):
        """
        Stop a stream by calling its command URL with method=stop.
        Then mark the stream as ended in state.
        """
        try:
            # Call command URL with method=stop to stop the stream on the engine
            stop_url = f"{command_url}?method=stop"
            logger.info(f"Stopping stream {stream_id} via command URL: {stop_url}")
            
            try:
                r = await client.get(stop_url, timeout=5.0)
                if r.status_code < 300:
                    logger.info(f"Successfully sent stop command for stream {stream_id}")
                else:
                    logger.warning(f"Stop command returned non-success status {r.status_code} for stream {stream_id}")
            except Exception as e:
                # Don't fail if the stop command fails - still end the stream in our state
                logger.warning(f"Failed to send stop command for stream {stream_id}: {e}")
            
            # Always mark the stream as ended in our state
            logger.info(f"Ending stream {stream_id} with reason: {reason}")
            state.on_stream_ended(StreamEndedEvent(
                container_id=container_id,
                stream_id=stream_id,
                reason=reason
            ))
            
            # Clean up inactive tracking for this stream
            self._inactive_tracker.remove_stream(stream_id)
            
        except Exception as e:
            logger.error(f"Error stopping stream {stream_id}: {e}")


collector = Collector()
