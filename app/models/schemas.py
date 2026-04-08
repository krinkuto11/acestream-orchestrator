from __future__ import annotations
from pydantic import BaseModel, HttpUrl, ConfigDict, RootModel, Field
from typing import Dict, Optional, Literal, List, Any
from datetime import datetime

ProxyControlMode = Literal["http", "api"]

class EngineAddress(BaseModel):
    host: str
    port: int

class StreamKey(BaseModel):
    key_type: Literal["content_id", "infohash", "torrent_url", "direct_url", "raw_data", "url", "magnet"]
    key: str
    file_indexes: str = "0"
    seekback: int = 0
    live_delay: int = 0
    control_mode: Optional[ProxyControlMode] = None

class SessionInfo(BaseModel):
    playback_session_id: str
    stat_url: Optional[str] = None
    command_url: Optional[str] = None
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

class StreamDataPlaneFailedEvent(BaseModel):
    stream_id: str
    container_id: Optional[str] = None
    reason: Optional[str] = None

class EngineState(BaseModel):
    container_id: str
    container_name: Optional[str] = None
    host: str
    port: int
    api_port: Optional[int] = None
    labels: Dict[str, str] = {}
    forwarded: bool = False
    first_seen: datetime
    last_seen: datetime
    streams: List[str] = []
    health_status: Optional[Literal["healthy", "unhealthy", "unknown"]] = "unknown"
    last_health_check: Optional[datetime] = None
    last_stream_usage: Optional[datetime] = None
    vpn_container: Optional[str] = None  # VPN container name this engine is assigned to
    # Engine version information
    engine_variant: Optional[str] = None
    platform: Optional[str] = None
    version: Optional[str] = None
    forwarded_port: Optional[int] = None  # For forwarded engines only
    # Optional runtime aggregates (active streams + monitor sessions)
    total_peers: Optional[int] = None
    total_speed_down: Optional[int] = None
    total_speed_up: Optional[int] = None
    stream_count: Optional[int] = None
    monitor_stream_count: Optional[int] = None
    monitor_speed_down: Optional[int] = None
    monitor_speed_up: Optional[int] = None

class LivePosData(BaseModel):
    """Live position data for live streams."""
    pos: Optional[str] = None
    live_first: Optional[str] = None
    live_last: Optional[str] = None
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    buffer_pieces: Optional[str] = None

class StreamState(BaseModel):
    id: str
    key_type: Literal["content_id", "infohash", "torrent_url", "direct_url", "raw_data", "url", "magnet"]
    key: str
    file_indexes: str = "0"
    seekback: int = 0
    live_delay: int = 0
    control_mode: Optional[ProxyControlMode] = None
    container_id: str
    container_name: Optional[str] = None
    playback_session_id: str
    stat_url: str
    command_url: str
    is_live: bool
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: Literal["started", "ended", "pending_failover"] = "started"
    paused: bool = False
    # Latest stats from the most recent snapshot
    peers: Optional[int] = None
    speed_down: Optional[int] = None
    speed_up: Optional[int] = None
    downloaded: Optional[int] = None
    uploaded: Optional[int] = None
    # Live position data (for live streams)
    livepos: Optional[LivePosData] = None
    # Proxy-level buffer calculation
    proxy_buffer_pieces: Optional[int] = None

class StreamStatSnapshot(BaseModel):
    ts: datetime
    peers: Optional[int] = None
    speed_down: Optional[int] = None
    speed_up: Optional[int] = None
    downloaded: Optional[int] = None
    uploaded: Optional[int] = None
    status: Optional[str] = None
    livepos: Optional[LivePosData] = None
    proxy_buffer_pieces: Optional[int] = None


class EngineListResponse(BaseModel):
    items: List[EngineState]


class StreamListResponse(BaseModel):
    items: List[StreamState]


class HealthStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: Optional[str] = None
    message: Optional[str] = None


class MetricsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class GenericObjectResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class GenericListResponse(RootModel[List[Any]]):
    pass


class VPNSettingsResponse(BaseModel):
    """Dynamic VPN settings payload exposed by /settings/vpn."""

    enabled: bool = False
    dynamic_vpn_management: bool = True
    preferred_engines_per_vpn: int = 10
    protocol: str = "wireguard"
    provider: str = "protonvpn"
    regions: List[str] = []
    credentials: List[Dict[str, Any]] = []
    api_port: int = 8001
    health_check_interval_s: int = 5
    port_cache_ttl_s: int = 60
    restart_engines_on_reconnect: bool = True
    unhealthy_restart_timeout_s: int = 60


class VPNSettingsUpdate(BaseModel):
    """Update model matching Smart VPN wizard save payload."""

    enabled: bool
    dynamic_vpn_management: Optional[bool] = None
    preferred_engines_per_vpn: int
    protocol: str
    provider: str
    regions: List[str] | str
    credentials: List[Dict[str, Any]]
    api_port: Optional[int] = None
    health_check_interval_s: Optional[int] = None
    port_cache_ttl_s: Optional[int] = None
    restart_engines_on_reconnect: Optional[bool] = None
    unhealthy_restart_timeout_s: Optional[int] = None
    trigger_migration: Optional[bool] = False

class OrchestratorStatusResponse(BaseModel):
    """
    Comprehensive orchestrator status for proxy integration.
    Provides all information needed to make intelligent decisions about retries and fallbacks.
    
    Note: This is a documentation model. The actual endpoint returns a dict.
    """
    model_config = ConfigDict(extra="allow")

class ProvisioningBlockedReason(BaseModel):
    """Detailed reason why provisioning is blocked with recovery guidance."""
    code: Literal["circuit_breaker", "vpn_disconnected", "max_capacity", "general_error"]
    message: str
    recovery_eta_seconds: Optional[int] = None  # Estimated time until recovery
    can_retry: bool = False  # Whether retrying makes sense
    should_wait: bool = False  # Whether proxy should wait vs fail immediately

class EventLog(BaseModel):
    """Event log entry for significant application events."""
    id: int
    timestamp: datetime
    event_type: Literal["engine", "stream", "vpn", "health", "system"]
    category: str  # created, deleted, started, ended, failed, recovered, etc.
    message: str
    details: Optional[Dict[str, Any]] = {}
    container_id: Optional[str] = None
    stream_id: Optional[str] = None


class EngineParameterSchema(BaseModel):
    name: str
    type: str = "flag"
    value: Any = True
    enabled: bool = True


class EngineConfigSchema(BaseModel):
    download_limit: int = 0
    upload_limit: int = 0
    live_cache_type: str = "memory"
    buffer_time: int = 10
    memory_limit: Optional[str] = None
    parameters: List[EngineParameterSchema] = Field(default_factory=list)
    torrent_folder_mount_enabled: bool = False
    torrent_folder_host_path: Optional[str] = None
    torrent_folder_container_path: Optional[str] = None
    disk_cache_mount_enabled: bool = False
    disk_cache_prune_enabled: bool = False
    disk_cache_prune_interval: int = 1440


class ManualEngineSchema(BaseModel):
    host: str
    port: int


class EngineSettingsSchema(BaseModel):
    min_replicas: int = 2
    max_replicas: int = 6
    auto_delete: bool = True
    manual_mode: bool = False
    manual_engines: List[ManualEngineSchema] = Field(default_factory=list)


class OrchestratorSettingsSchema(BaseModel):
    monitor_interval_s: int = 10
    engine_grace_period_s: int = 30
    autoscale_interval_s: int = 30
    startup_timeout_s: int = 25
    idle_ttl_s: int = 600
    collect_interval_s: int = 1
    stats_history_max: int = 720
    health_check_interval_s: int = 20
    health_failure_threshold: int = 3
    health_unhealthy_grace_period_s: int = 60
    health_replacement_cooldown_s: int = 60
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout_s: int = 300
    circuit_breaker_replacement_threshold: int = 3
    circuit_breaker_replacement_timeout_s: int = 180
    max_concurrent_provisions: int = 5
    min_provision_interval_s: float = 0.5
    port_range_host: str = "19000-19999"
    ace_http_range: str = "40000-44999"
    ace_https_range: str = "45000-49999"
    ace_live_edge_delay: int = 0
    debug_mode: bool = False


class ProxySettingsSchema(BaseModel):
    initial_data_wait_timeout: int = 10
    initial_data_check_interval: float = 0.2
    no_data_timeout_checks: int = 60
    no_data_check_interval: float = 1.0
    connection_timeout: int = 30
    stream_timeout: int = 60
    channel_shutdown_delay: int = 5
    proxy_prebuffer_seconds: int = 0
    max_streams_per_engine: int = 3
    stream_mode: str = "TS"
    control_mode: str = "api"
    legacy_api_preflight_tier: str = "light"
    ace_live_edge_delay: int = 0
    hls_max_segments: int = 20
    hls_initial_segments: int = 3
    hls_window_size: int = 6
    hls_buffer_ready_timeout: int = 30
    hls_first_segment_timeout: int = 30
    hls_initial_buffer_seconds: int = 10
    hls_max_initial_segments: int = 10
    hls_segment_fetch_interval: float = 0.5


class VPNCredentialSchema(BaseModel):
    id: str
    provider: Optional[str] = None
    protocol: Optional[str] = None
    private_key: Optional[str] = None
    wireguard_private_key: Optional[str] = None
    addresses: Optional[str] = None
    wireguard_addresses: Optional[str] = None
    endpoint: Optional[str] = None
    endpoints: Optional[str] = None
    wireguard_endpoints: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    openvpn_user: Optional[str] = None
    openvpn_password: Optional[str] = None
    source: Optional[str] = None
    port_forwarding: Optional[bool] = True


class VPNSettingsSchema(BaseModel):
    enabled: bool = False
    dynamic_vpn_management: bool = True
    preferred_engines_per_vpn: int = 10
    protocol: str = "wireguard"
    provider: str = "protonvpn"
    regions: List[str] = Field(default_factory=list)
    credentials: List[VPNCredentialSchema] = Field(default_factory=list)
    api_port: int = 8001
    health_check_interval_s: int = 5
    port_cache_ttl_s: int = 60
    restart_engines_on_reconnect: bool = True
    unhealthy_restart_timeout_s: int = 60


class ConsolidatedSettingsSchema(BaseModel):
    engine_config: EngineConfigSchema
    engine_settings: EngineSettingsSchema
    orchestrator_settings: OrchestratorSettingsSchema
    proxy_settings: ProxySettingsSchema
    vpn_settings: VPNSettingsSchema
