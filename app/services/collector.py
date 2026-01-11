import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from .state import state
from ..models.schemas import StreamStatSnapshot
from ..core.config import cfg
from .metrics import on_stream_stat_update

logger = logging.getLogger(__name__)


class Collector:
    """
    Stream statistics collector.
    
    This service periodically polls stat URLs for all active streams and
    collects stream statistics (peers, speed, etc.) for monitoring and metrics.
    
    The COLLECT_INTERVAL_S determines how frequently stats are collected (default 1 second).
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
                # Collect stats for each active stream
                tasks = [self._collect_one(client, s.id, s.stat_url) for s in streams]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=cfg.COLLECT_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass

    async def _collect_one(self, client: httpx.AsyncClient, stream_id: str, stat_url: str):
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

            # Check if the stream has an error response
            # When a stream has stopped, the engine may return: {"response": null, "error": "unknown playback session id"}
            # However, stream lifecycle is now managed by Acexy, so we just skip collecting stats in this case
            if data.get("response") is None and data.get("error"):
                error_msg = data.get("error", "").lower()
                logger.debug(f"Stat endpoint reported error for {stream_id}: {data.get('error')}")
                # Stream has ended on the engine side - skip stats collection
                # Acexy will handle the stream lifecycle, so we don't need to intervene
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

            # Extract livepos data (for live streams)
            # AceStream engines return livepos object with fields that may vary by version:
            # - pos: current playback position timestamp
            # - live_first/first_ts/first: start of live buffer (preference: live_first > first_ts > first)
            # - live_last/last_ts/last: end of live buffer (preference: live_last > last_ts > last)
            # - buffer_pieces: number of buffered pieces
            # Example from AceStream 3.x API: {"pos": "1767629806", "live_first": "1767628008", "live_last": "1767629808", ...}
            livepos_data = None
            livepos_raw = payload.get("livepos")
            if livepos_raw:
                from ..models.schemas import LivePosData
                livepos_data = LivePosData(
                    pos=livepos_raw.get("pos"),
                    # Prefer live_first, fallback to first_ts, then first (for older versions)
                    live_first=livepos_raw.get("live_first") or livepos_raw.get("first_ts") or livepos_raw.get("first"),
                    # Prefer live_last, fallback to last_ts, then last (for older versions)
                    live_last=livepos_raw.get("live_last") or livepos_raw.get("last_ts") or livepos_raw.get("last"),
                    # Store both first_ts and last_ts for compatibility
                    first_ts=livepos_raw.get("first_ts") or livepos_raw.get("first"),
                    last_ts=livepos_raw.get("last_ts") or livepos_raw.get("last"),
                    buffer_pieces=livepos_raw.get("buffer_pieces")
                )

            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=payload.get("peers"),
                speed_down=speed_down,
                speed_up=speed_up,
                downloaded=payload.get("downloaded"),
                uploaded=payload.get("uploaded"),
                status=status,
                livepos=livepos_data,
            )
            state.append_stat(stream_id, snap)
            logger.debug(f"Appended stat for {stream_id}: peers={snap.peers} speed_down={snap.speed_down} speed_up={snap.speed_up} downloaded={snap.downloaded} uploaded={snap.uploaded} status={snap.status} livepos={bool(livepos_data)}")

            # Update cumulative byte metrics
            try:
                on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
            except Exception:
                logger.exception(f"Error updating cumulative metrics for stream {stream_id}")
        
        except httpx.ConnectTimeout:
            # Connection timeout - engine may be slow or unavailable
            logger.debug(f"Connection timeout collecting stats for {stream_id} from {stat_url}")
            return
        except httpx.TimeoutException:
            # Other timeout exceptions (read timeout, pool timeout, etc.)
            logger.debug(f"Timeout collecting stats for {stream_id} from {stat_url}")
            return
        except httpx.HTTPError as e:
            # Other HTTP-related errors (connection errors, etc.)
            logger.debug(f"HTTP error collecting stats for {stream_id} from {stat_url}: {e}")
            return
        except Exception:
            logger.exception(f"Unhandled exception while collecting stats for {stream_id} from {stat_url}")
            return


collector = Collector()
