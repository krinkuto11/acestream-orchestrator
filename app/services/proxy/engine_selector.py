"""Intelligent engine selector with load balancing"""

import logging
import asyncio
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from ..state import state
from ...core.config import cfg

logger = logging.getLogger(__name__)


@dataclass
class EngineInfo:
    """Information about an available engine"""
    container_id: str
    host: str
    port: int
    is_forwarded: bool
    active_streams: int
    health_status: str
    
    def get_score(self) -> float:
        """Calculate selection score (higher is better).
        
        Scoring priorities:
        1. Forwarded engines get +1000 bonus
        2. Fewer active streams is better
        3. Healthy engines only
        """
        if self.health_status != "healthy":
            return -1000  # Unhealthy engines get very low score
        
        score = 0.0
        
        # Prioritize forwarded engines
        if self.is_forwarded:
            score += 1000
        
        # Prefer engines with fewer streams (load balancing)
        # Subtract 10 points per active stream
        score -= self.active_streams * 10
        
        return score


class EngineSelector:
    """Selects the best available engine for streaming.
    
    Selection algorithm:
    1. Prioritizes forwarded engines (better P2P connectivity)
    2. Balances load across all engines
    3. Filters out unhealthy engines
    4. Caches engine list to reduce orchestrator load
    """
    
    def __init__(self, cache_ttl: int = 2):
        self.cache_ttl = cache_ttl
        self._engine_cache: Optional[List[EngineInfo]] = None
        self._cache_time: float = 0
        self._cache_lock = asyncio.Lock()
    
    async def select_best_engine(self) -> Optional[Dict[str, Any]]:
        """Select the best available engine.
        
        Returns:
            Dictionary with engine info: {container_id, host, port, is_forwarded}
            or None if no suitable engine is available
        """
        engines = await self._get_engines()
        
        if not engines:
            logger.warning("No engines available for selection")
            return None
        
        # Filter healthy engines
        healthy_engines = [e for e in engines if e.health_status == "healthy"]
        
        if not healthy_engines:
            logger.warning("No healthy engines available")
            return None
        
        # Sort by score (highest first)
        healthy_engines.sort(key=lambda e: e.get_score(), reverse=True)
        
        # Select best engine
        best = healthy_engines[0]
        
        logger.info(
            f"Selected engine {best.container_id[:12]} "
            f"(forwarded={best.is_forwarded}, streams={best.active_streams}, "
            f"score={best.get_score():.1f})"
        )
        
        return {
            "container_id": best.container_id,
            "host": best.host,
            "port": best.port,
            "is_forwarded": best.is_forwarded,
        }
    
    async def _get_engines(self) -> List[EngineInfo]:
        """Get list of available engines with caching."""
        async with self._cache_lock:
            # Check if cache is still valid
            if self._engine_cache and (time.time() - self._cache_time) < self.cache_ttl:
                return self._engine_cache
            
            # Refresh cache
            engines = []
            
            # Get engines from state
            for container_id, engine_state in state.engines.items():
                # Count active streams for this engine
                active_streams = sum(
                    1 for stream in state.streams.values()
                    if stream.container_id == container_id and stream.status == "started"
                )
                
                # Check if engine is forwarded
                is_forwarded = engine_state.labels.get("acestream.forwarded") == "true"
                
                # Get health status
                health_status = engine_state.health_status or "unknown"
                
                # Get engine host and port
                host = engine_state.host  # Use the host from engine state
                port = engine_state.port  # This is the host HTTP port
                
                if not port:
                    logger.warning(f"Engine {container_id[:12]} has no port")
                    continue
                
                engines.append(EngineInfo(
                    container_id=container_id,
                    host=host,
                    port=port,
                    is_forwarded=is_forwarded,
                    active_streams=active_streams,
                    health_status=health_status,
                ))
            
            self._engine_cache = engines
            self._cache_time = time.time()
            
            logger.debug(f"Refreshed engine cache with {len(engines)} engines")
            return engines
    
    def invalidate_cache(self):
        """Invalidate the engine cache to force refresh on next selection."""
        self._engine_cache = None
        self._cache_time = 0
