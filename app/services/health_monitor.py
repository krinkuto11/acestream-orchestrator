import asyncio
import logging
from .state import state

logger = logging.getLogger(__name__)

class HealthMonitor:
    def __init__(self, check_interval: int = 30):
        self.check_interval = check_interval
        self._task = None
        self._running = False

    async def start(self):
        """Start the health monitoring task."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Health monitor started with {self.check_interval}s interval")

    async def stop(self):
        """Stop the health monitoring task."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitor stopped")

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                # Update health for all engines
                state.update_engines_health()
                logger.debug("Updated health status for all engines")
            except Exception as e:
                logger.error(f"Error during health check: {e}")
            
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break

# Global health monitor instance
health_monitor = HealthMonitor()