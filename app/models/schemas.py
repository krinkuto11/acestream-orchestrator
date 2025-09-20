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
    first_seen: datetime
    last_seen: datetime
    streams: List[str] = []

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

class StreamStatSnapshot(BaseModel):
    ts: datetime
    peers: Optional[int] = None
    speed_down: Optional[int] = None
    speed_up: Optional[int] = None
    downloaded: Optional[int] = None
    uploaded: Optional[int] = None
    status: Optional[str] = None
