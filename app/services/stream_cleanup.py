"""
Background service for cleaning up old ended streams.

This service periodically removes ended streams from memory and database
to prevent unbounded growth.
"""

import asyncio
import logging
from .state import state
from ..core.config import cfg

logger = logging.getLogger(__name__)


class StreamCleanup:
    def __init__(self):
        self._task = None
        self._stop = asyncio.Event()
        # Clean up streams older than 1 hour by default
        # This can be made configurable if needed
        self._max_age_seconds = 3600

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(f"Stream cleanup service started (will remove ended streams older than {self._max_age_seconds}s)")

    async def stop(self):
        self._stop.set()
        if self._task:
            await self._task

    async def _run(self):
        """Run cleanup every 5 minutes."""
        cleanup_interval = 300  # 5 minutes
        
        while not self._stop.is_set():
            try:
                # Wait for the interval or until stopped
                await asyncio.wait_for(self._stop.wait(), timeout=cleanup_interval)
            except asyncio.TimeoutError:
                # Timeout is expected - time to run cleanup
                try:
                    removed_count = state.cleanup_ended_streams(max_age_seconds=self._max_age_seconds)
                    if removed_count > 0:
                        logger.info(f"Stream cleanup: removed {removed_count} old ended streams")
                except Exception:
                    logger.exception("Error during stream cleanup")


stream_cleanup = StreamCleanup()
