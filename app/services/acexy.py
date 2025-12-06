"""
Acexy proxy integration service - DEPRECATED.

This module is kept for backwards compatibility but no longer performs 
bidirectional communication with Acexy. Stream state is now managed entirely
through stat URL checking by the collector service.

The acexy proxy is now stateless and only sends stream started events.
The orchestrator handles stream state checking independently via stat URLs.
"""

import logging

logger = logging.getLogger(__name__)


class AcexySyncService:
    """
    Deprecated service that previously synced with Acexy.
    
    Now a no-op placeholder for backwards compatibility.
    Stream state is managed via stat URL checking in the collector service.
    """
    
    def __init__(self):
        self._enabled = False
        
    async def start(self):
        """No-op start method for backwards compatibility."""
        logger.info("Acexy sync service is deprecated - stream state managed via stat URL checking")
        pass
    
    async def stop(self):
        """No-op stop method for backwards compatibility."""
        pass
    
    def get_status(self):
        """Get the status indicating service is deprecated."""
        return {
            "enabled": False,
            "deprecated": True,
            "message": "Acexy sync is deprecated. Stream state managed via stat URL checking.",
            "url": None,
            "healthy": None,
            "last_health_check": None,
            "sync_interval_seconds": None
        }


# Global instance
acexy_sync_service = AcexySyncService()
