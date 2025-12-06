import asyncio
import httpx
import logging
from datetime import datetime, timezone
from .state import state
from ..models.schemas import StreamStatSnapshot, StreamEndedEvent
from ..core.config import cfg
from .metrics import orch_stale_streams_detected, on_stream_stat_update

logger = logging.getLogger(__name__)

class Collector:
    """
    Stream statistics collector and stale stream detector.
    
    This service is the PRIMARY mechanism for detecting stale streams.
    It periodically polls stat URLs for all active streams and:
    1. Collects stream statistics (peers, speed, etc.)
    2. Detects stale streams when the engine returns "unknown playback session id"
    3. Automatically ends stale streams
    
    With the acexy proxy now being stateless (only sending start events),
    this collector is the ONLY way stale streams are detected and cleaned up.
    The COLLECT_INTERVAL_S should be kept low (default 2 seconds) for quick detection.
    """
    def __init__(self):
        self._task = None
        self._stop = asyncio.Event()

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
                tasks = [self._collect_one(client, s.id, s.stat_url) for s in streams]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=cfg.COLLECT_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass

    async def _collect_one(self, client: httpx.AsyncClient, stream_id: str, url: str):
        try:
            logger.debug(f"Collecting stats for stream_id={stream_id} url={url}")
            r = await client.get(url)
            logger.debug(f"HTTP {r.status_code} from {url} for stream {stream_id}")
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
                logger.debug(f"Failed to parse JSON from {url} for stream {stream_id}: {e}; body={body}")
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
                        logger.info(f"Automatically ending stale stream {stream_id}")
                        state.on_stream_ended(StreamEndedEvent(
                            container_id=stream.container_id,
                            stream_id=stream_id,
                            reason="stale_stream_detected"
                        ))
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
            logger.debug(f"Payload for {stream_id} from {url}: {raw_payload_str}")

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

            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=payload.get("peers"),
                speed_down=speed_down,
                speed_up=speed_up,
                downloaded=payload.get("downloaded"),
                uploaded=payload.get("uploaded"),
                status=payload.get("status"),
            )
            state.append_stat(stream_id, snap)
            logger.debug(f"Appended stat for {stream_id}: peers={snap.peers} speed_down={snap.speed_down} speed_up={snap.speed_up} downloaded={snap.downloaded} uploaded={snap.uploaded}")

            # Update cumulative byte metrics
            try:
                on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
            except Exception:
                logger.exception(f"Error updating cumulative metrics for stream {stream_id}")
        except Exception:
            logger.exception(f"Unhandled exception while collecting stats for {stream_id} from {url}")
            return

collector = Collector()
