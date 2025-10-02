import asyncio
import httpx
from datetime import datetime, timezone
from .state import state
from ..models.schemas import StreamStatSnapshot
from ..core.config import cfg
from .metrics import orch_collect_errors

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
            payload = data.get("response") or {}
            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=payload.get("peers"),
                speed_down=payload.get("speed_down"),
                speed_up=payload.get("speed_up"),
                downloaded=payload.get("downloaded"),
                uploaded=payload.get("uploaded"),
                status=payload.get("status"),
            )
            state.append_stat(stream_id, snap)
        except Exception:
            orch_collect_errors.inc()
            return

collector = Collector()
