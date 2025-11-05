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
            r = await client.get(url)
            if r.status_code >= 300:
                return
            data = r.json()
            
            # Check if the stream has stopped/is stale
            # When a stream has stopped, the engine returns: {"response": null, "error": "unknown playback session id"}
            if data.get("response") is None and data.get("error"):
                error_msg = data.get("error", "").lower()
                if "unknown playback session id" in error_msg:
                    logger.info(f"Detected stale stream {stream_id}: {data.get('error')}")
                    # Get the stream to find its container_id
                    stream = state.get_stream(stream_id)
                    if stream and stream.status == "started":
                        # Automatically end the stream
                        logger.info(f"Automatically ending stale stream {stream_id}")
                        state.on_stream_ended(StreamEndedEvent(
                            container_id=stream.container_id,
                            stream_id=stream_id,
                            reason="stale_stream_detected"
                        ))
                        orch_stale_streams_detected.inc()
                    return
            
            payload = data.get("response") or {}
            # Handle both snake_case and camelCase field names from AceStream API
            # Some engine versions return speedDown/speedUp, others return speed_down/speed_up
            # Use explicit None check to preserve 0 values (0 is valid speed)
            speed_down_snake = payload.get("speed_down")
            speed_down = speed_down_snake if speed_down_snake is not None else payload.get("speedDown")
            speed_up_snake = payload.get("speed_up")
            speed_up = speed_up_snake if speed_up_snake is not None else payload.get("speedUp")
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
            
            # Update cumulative byte metrics
            on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
        except Exception:
            return

collector = Collector()
