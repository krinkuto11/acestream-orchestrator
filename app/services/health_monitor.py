import asyncio
import logging
import time
from .state import state
from .event_logger import event_logger

logger = logging.getLogger(__name__)

class HealthMonitor:
    """
    Basic health monitoring service that updates engine health status.
    
    Note: This works alongside the HealthManager service which handles
    proactive engine replacement and availability management.
    """
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
            check_start = time.time()
            try:
                # Update health for all engines
                state.update_engines_health()
                logger.debug("Updated health status for all engines")
                
                duration = time.time() - check_start
                
                # Log health check results
                engines = state.list_engines()
                healthy_count = sum(1 for e in engines if e.health_status == "healthy")
                unhealthy_count = sum(1 for e in engines if e.health_status == "unhealthy")
                
                logger.debug(f"Health check completed in {duration:.2f}s: {healthy_count} healthy, {unhealthy_count} unhealthy out of {len(engines)} engines")
                
                # Detect stress situation (high proportion of unhealthy engines)
                if len(engines) > 0 and unhealthy_count / len(engines) > 0.3:
                    logger.warning(f"High proportion of unhealthy engines: {unhealthy_count}/{len(engines)} (>30%)")
                    # Log event for high unhealthy engines
                    event_logger.log_event(
                        event_type="health",
                        category="warning",
                        message=f"High proportion of unhealthy engines: {unhealthy_count}/{len(engines)} (>30%)",
                        details={
                            "unhealthy_count": unhealthy_count,
                            "total_engines": len(engines),
                            "percentage": round(unhealthy_count / len(engines) * 100, 2)
                        }
                    )
                
            except Exception as e:
                duration = time.time() - check_start
                logger.error(f"Error during health check: {e} (duration: {duration:.2f}s)")
            
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break

# Global health monitor instance
health_monitor = HealthMonitor()