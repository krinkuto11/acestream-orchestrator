
import asyncio
import logging
import time
from .engine_cache_manager import engine_cache_manager
from .state import state

logger = logging.getLogger(__name__)

async def start_cache_monitoring(interval_seconds: int = 30):
    """
    Periodically updates cache usage statistics in the application state.
    """
    logger.info(f"Starting Cache Monitoring Service (interval: {interval_seconds}s)")
    
    while True:
        try:
            # Check if cache management is enabled
            if engine_cache_manager.is_enabled():
                # Get current stats from Docker volumes
                stats = await engine_cache_manager.get_total_cache_size()
                
                # Update global state
                state.update_cache_stats(
                    total_bytes=stats.get("total_bytes", 0),
                    volume_count=stats.get("volume_count", 0)
                )
                
                logger.debug(f"Updated cache stats: {stats.get('total_bytes', 0)} bytes across {stats.get('volume_count', 0)} volumes")
            else:
                # If disabled, ensure state reflects that
                state.update_cache_stats(total_bytes=0, volume_count=0)
                
        except asyncio.CancelledError:
            logger.info("Cache Monitoring Service stopping...")
            break
        except Exception as e:
            logger.error(f"Error in cache monitoring loop: {e}")
            
        await asyncio.sleep(interval_seconds)
