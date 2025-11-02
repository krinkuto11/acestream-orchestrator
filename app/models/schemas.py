from __future__ import annotations
from pydantic import BaseModel, HttpUrl
from typing import Dict, Optional, Literal, List
from datetime import datetime

class EngineAddress(BaseModel):
    host: str
    port: int

class StreamKey(BaseModel):
    key_type: Literal["content_id", "infohash", "url", "magnet"]
    key: str

class SessionInfo(BaseModel):
    playback_session_id: str
    stat_url: HttpUrl
    command_url: HttpUrl
    is_live: int

class StreamStartedEvent(BaseModel):
    container_id: Optional[str] = None
    engine: EngineAddress
    stream: StreamKey
    session: SessionInfo
    labels: Dict[str, str] = {}

class StreamEndedEvent(BaseModel):
    container_id: Optional[str] = None
    stream_id: Optional[str] = None
    reason: Optional[str] = None

class EngineState(BaseModel):
    container_id: str
    container_name: Optional[str] = None
    host: str
    port: int
    labels: Dict[str, str] = {}
    forwarded: bool = False
    first_seen: datetime
    last_seen: datetime
    streams: List[str] = []
    health_status: Optional[Literal["healthy", "unhealthy", "unknown"]] = "unknown"
    last_health_check: Optional[datetime] = None
    last_stream_usage: Optional[datetime] = None
    last_cache_cleanup: Optional[datetime] = None
    cache_size_bytes: Optional[int] = None

class StreamState(BaseModel):
    id: str
    key_type: Literal["content_id", "infohash", "url", "magnet"]
    key: str
    container_id: str
    playback_session_id: str
    stat_url: str
    command_url: str
    is_live: bool
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: Literal["started", "ended"] = "started"
    # Latest stats from the most recent snapshot
    peers: Optional[int] = None
    speed_down: Optional[int] = None
    speed_up: Optional[int] = None
    downloaded: Optional[int] = None
    uploaded: Optional[int] = None

class StreamStatSnapshot(BaseModel):
    ts: datetime
    peers: Optional[int] = None
    speed_down: Optional[int] = None
    speed_up: Optional[int] = None
    downloaded: Optional[int] = None
    uploaded: Optional[int] = None
    status: Optional[str] = None

class OrchestratorStatusResponse(BaseModel):
    """
    Comprehensive orchestrator status for proxy integration.
    Provides all information needed to make intelligent decisions about retries and fallbacks.
    
    Note: This is a documentation model. The actual endpoint returns a dict.
    """
    pass  # Placeholder for documentation

class ProvisioningBlockedReason(BaseModel):
    """Detailed reason why provisioning is blocked with recovery guidance."""
    code: Literal["circuit_breaker", "vpn_disconnected", "max_capacity", "general_error"]
    message: str
    recovery_eta_seconds: Optional[int] = None  # Estimated time until recovery
    can_retry: bool = False  # Whether retrying makes sense
    should_wait: bool = False  # Whether proxy should wait vs fail immediately
