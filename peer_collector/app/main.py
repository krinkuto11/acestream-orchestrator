"""
Peer Collector Microservice

A lightweight microservice that runs inside the Gluetun VPN container to collect
peer statistics from AceStream torrents. The orchestrator calls this service to
get peer data without needing to be inside the VPN itself.

Endpoints:
- GET /health - Health check
- GET /peers/{acestream_id} - Get peer statistics for an AceStream ID
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .peer_stats import (
    add_torrent_for_tracking,
    get_peers_from_handle,
    enrich_peer_with_geolocation,
    _peer_stats_cache,
    PEER_DISCOVERY_WAIT_SECONDS,
    lt
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "30"))
MAX_PEERS_TO_ENRICH = int(os.getenv("MAX_PEERS_TO_ENRICH", "50"))

# Redis is bundled and always available (same as orchestrator)
redis_client = None
try:
    import redis
    REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB = int(os.getenv("REDIS_DB", "0"))
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=False)
    redis_client.ping()
    logger.info(f"Redis caching enabled: {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")
except Exception as e:
    logger.warning(f"Redis not available, using in-memory cache only: {e}")
    redis_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Starting Peer Collector Microservice")
    if lt is None:
        logger.error("libtorrent not available! Peer collection will not work.")
    else:
        logger.info("libtorrent is available and ready")
    
    yield
    
    logger.info("Shutting down Peer Collector Microservice")


app = FastAPI(
    title="AceStream Peer Collector",
    description="Microservice for collecting peer statistics from AceStream torrents",
    version="1.0.0",
    lifespan=lifespan
)


class HealthResponse(BaseModel):
    status: str
    libtorrent_available: bool
    redis_available: bool


class PeerStatsResponse(BaseModel):
    acestream_id: str
    infohash: Optional[str] = None
    peers: list
    peer_count: int
    total_peers: int
    cached_at: float
    error: Optional[str] = None


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    
    Returns the status of the service and whether libtorrent is available.
    """
    return HealthResponse(
        status="healthy",
        libtorrent_available=lt is not None,
        redis_available=redis_client is not None
    )


@app.get("/peers/{acestream_id}", response_model=PeerStatsResponse)
async def get_peer_stats(acestream_id: str):
    """
    Get peer statistics for an AceStream ID.
    
    This endpoint:
    1. Uses the acestream_id as the infohash
    2. Adds the torrent to libtorrent session
    3. Waits for peers to connect
    4. Extracts peer information
    5. Enriches peers with geolocation data
    
    Results are cached to prevent excessive API calls.
    
    Args:
        acestream_id: The AceStream content ID (used as infohash)
        
    Returns:
        Peer statistics including enriched peer data
    """
    if lt is None:
        return PeerStatsResponse(
            acestream_id=acestream_id,
            peers=[],
            peer_count=0,
            total_peers=0,
            cached_at=asyncio.get_event_loop().time(),
            error="libtorrent not available"
        )
    
    # Check in-memory cache first
    from datetime import datetime, timezone
    cache_key = acestream_id
    
    if cache_key in _peer_stats_cache:
        cached_data = _peer_stats_cache[cache_key]
        current_time = datetime.now(timezone.utc).timestamp()
        if (current_time - cached_data.get("cached_at", 0)) < CACHE_TTL_SECONDS:
            logger.debug(f"Using cached peer stats for {acestream_id}")
            return PeerStatsResponse(**cached_data)
    
    # Check Redis cache if enabled
    if redis_client:
        try:
            import json
            cached_bytes = redis_client.get(f"peer_stats:{cache_key}")
            if cached_bytes:
                cached_data = json.loads(cached_bytes.decode('utf-8'))
                logger.debug(f"Using Redis cached peer stats for {acestream_id}")
                return PeerStatsResponse(**cached_data)
        except Exception as e:
            logger.warning(f"Failed to read from Redis cache: {e}")
    
    try:
        # Use acestream_id directly as infohash
        # AceStream IDs are SHA1 hashes that can be used as BitTorrent infohashes
        infohash = acestream_id
        
        # Add torrent to libtorrent session (run in thread pool)
        def add_torrent_sync():
            return add_torrent_for_tracking(infohash)
        
        loop = asyncio.get_event_loop()
        handle = await loop.run_in_executor(None, add_torrent_sync)
        
        if handle is None:
            result = PeerStatsResponse(
                acestream_id=acestream_id,
                peers=[],
                peer_count=0,
                total_peers=0,
                cached_at=datetime.now(timezone.utc).timestamp(),
                error="Could not add torrent to libtorrent session"
            )
            _peer_stats_cache[cache_key] = result.model_dump()
            return result
        
        # Wait for peers to connect (configurable delay)
        # This allows DHT and trackers to find peers
        await asyncio.sleep(PEER_DISCOVERY_WAIT_SECONDS)
        
        # Get peers from handle (run in thread)
        def get_peers_sync():
            return get_peers_from_handle(handle, max_peers=100)
        
        peers = await loop.run_in_executor(None, get_peers_sync)
        
        # Enrich each peer with geolocation data
        enriched_peers = []
        
        for peer in peers[:MAX_PEERS_TO_ENRICH]:
            peer_ip = peer.get("ip")
            if not peer_ip:
                continue
            
            # Fetch geolocation (this will use cache if available)
            geo_data = await enrich_peer_with_geolocation(peer_ip)
            
            if geo_data:
                # Merge peer data with geolocation
                enriched_peer = {**peer, **geo_data}
                enriched_peers.append(enriched_peer)
            
            # Small delay to avoid overwhelming the API
            await asyncio.sleep(0.1)
        
        # Build result
        result_data = {
            "acestream_id": acestream_id,
            "infohash": infohash,
            "peers": enriched_peers,
            "peer_count": len(enriched_peers),
            "total_peers": len(peers),
            "cached_at": datetime.now(timezone.utc).timestamp()
        }
        
        # Cache the result in memory
        _peer_stats_cache[cache_key] = result_data
        
        # Cache in Redis if enabled
        if redis_client:
            try:
                import json
                redis_client.setex(
                    f"peer_stats:{cache_key}",
                    CACHE_TTL_SECONDS,
                    json.dumps(result_data)
                )
                logger.debug(f"Cached peer stats in Redis for {acestream_id}")
            except Exception as e:
                logger.warning(f"Failed to cache in Redis: {e}")
        
        logger.info(f"Fetched and cached peer stats for {acestream_id}: {len(enriched_peers)} enriched peers out of {len(peers)} total")
        
        return PeerStatsResponse(**result_data)
        
    except Exception as e:
        logger.error(f"Failed to get peer stats for {acestream_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch peer statistics: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
