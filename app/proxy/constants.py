"""
Constants for AceStream Proxy.
Adapted from ts_proxy constants - keeping all the battle-tested infrastructure.
"""

from typing import Optional


# Canonical proxy control modes
PROXY_MODE_HTTP = "http"
PROXY_MODE_API = "api"

# Backward-compatible aliases still accepted in env/settings payloads.
_PROXY_MODE_ALIASES = {
    "legacy_http": PROXY_MODE_HTTP,
    "legacy-api": PROXY_MODE_API,
    "legacy_http_mode": PROXY_MODE_HTTP,
    "legacy_api": PROXY_MODE_API,
    "http": PROXY_MODE_HTTP,
    "api": PROXY_MODE_API,
    "legacyhttp": PROXY_MODE_HTTP,
    "legacyapi": PROXY_MODE_API,
}


def normalize_proxy_mode(value: Optional[str], default: Optional[str] = PROXY_MODE_HTTP) -> Optional[str]:
    """Normalize proxy control mode to canonical lowercase values.

    Accepted legacy values like "LEGACY_HTTP" or "legacy_api" are mapped to
    "http" and "api" respectively.
    """
    text = str(value or "").strip().lower()
    if not text:
        return default

    # Normalize separators so LEGACY_HTTP, legacy-http, and legacy http all map.
    compact = text.replace(" ", "_").replace("-", "_")
    mapped = _PROXY_MODE_ALIASES.get(compact)
    if mapped:
        return mapped

    return default


def proxy_mode_label(value: Optional[str]) -> str:
    """Human-readable control mode label for UI/event payloads."""
    mode = normalize_proxy_mode(value)
    if mode == PROXY_MODE_API:
        return "API"
    return "HTTP"

# Redis related constants
REDIS_KEY_PREFIX = "ace_proxy"
REDIS_TTL_DEFAULT = 3600  # 1 hour
REDIS_TTL_SHORT = 60      # 1 minute
REDIS_TTL_MEDIUM = 300    # 5 minutes

# Stream states (adapted from ChannelState)
class StreamState:
    INITIALIZING = "initializing"
    CONNECTING = "connecting"
    WAITING_FOR_CLIENTS = "waiting_for_clients"
    ACTIVE = "active"
    ERROR = "error"
    STOPPING = "stopping"
    STOPPED = "stopped"
    BUFFERING = "buffering"

# Event types
class EventType:
    STREAM_SWITCH = "stream_switch"          # Not used for AceStream but kept for compatibility
    STREAM_SWITCHED = "stream_switched"       # Not used for AceStream but kept for compatibility
    STREAM_STOP = "stream_stop"
    STREAM_STOPPED = "stream_stopped"
    CLIENT_CONNECTED = "client_connected"
    CLIENT_DISCONNECTED = "client_disconnected"
    CLIENT_STOP = "client_stop"

# Stream metadata field names stored in Redis
class StreamMetadataField:
    # Basic fields
    CONTENT_ID = "content_id"                # AceStream content ID (infohash)
    PLAYBACK_URL = "playback_url"            # AceStream playback URL
    STAT_URL = "stat_url"                     # AceStream stat URL  
    COMMAND_URL = "command_url"               # AceStream command URL
    PLAYBACK_SESSION_ID = "playback_session_id"  # AceStream session ID
    STATE = "state"
    OWNER = "owner"                           # Worker ID owning this stream
    
    # Engine info
    ENGINE_ID = "engine_id"                   # Container ID of engine
    ENGINE_HOST = "engine_host"
    ENGINE_PORT = "engine_port"
    ENGINE_FORWARDED = "engine_forwarded"
    
    # Status and error fields
    ERROR_MESSAGE = "error_message"
    ERROR_TIME = "error_time"
    STATE_CHANGED_AT = "state_changed_at"
    INIT_TIME = "init_time"
    CONNECTION_READY_TIME = "connection_ready_time"
    
    # Buffer and data tracking
    BUFFER_CHUNKS = "buffer_chunks"
    TOTAL_BYTES = "total_bytes"
    DYNAMIC_THRESHOLD_SECONDS = "dynamic_threshold_seconds"
    CURRENT_CLIENT_BUFFER_SECONDS = "current_client_buffer_seconds"
    MAX_TOLERANCE_SECONDS = "max_tolerance_seconds"
    STREAM_INACTIVITY_SECONDS = "stream_inactivity_seconds"
    DYNAMIC_THRESHOLD_UPDATED_AT = "dynamic_threshold_updated_at"
    SOURCE_BUFFER_DURATION_SECONDS = "source_buffer_duration_seconds"
    
    # AceStream specific
    IS_LIVE = "is_live"
    IS_ENCRYPTED = "is_encrypted"
    
# Client metadata fields
class ClientMetadataField:
    CONNECTED_AT = "connected_at"
    LAST_ACTIVE = "last_active"
    BYTES_SENT = "bytes_sent"
    # Legacy alias retained for backward compatibility with existing clients.
    BUFFER_SECONDS_BEHIND = "buffer_seconds_behind"
    AVG_RATE_KBPS = "avg_rate_KBps"
    CURRENT_RATE_KBPS = "current_rate_KBps"
    IP_ADDRESS = "ip_address"
    USER_AGENT = "user_agent"
    WORKER_ID = "worker_id"
    CHUNKS_SENT = "chunks_sent"
    STATS_UPDATED_AT = "stats_updated_at"
    # Per-client runway used by failover logic (segment/TS cursor based).
    CLIENT_RUNWAY_SECONDS = "client_runway_seconds"
    # Stream-wide HLS manifest window (dashboard/diagnostics).
    STREAM_BUFFER_WINDOW_SECONDS = "stream_buffer_window_seconds"
    POSITION_SOURCE = "position_source"
    POSITION_CONFIDENCE = "position_confidence"
    POSITION_OBSERVED_AT = "position_observed_at"

# TS packet constants (keep these as AceStream uses MPEG-TS format)
TS_PACKET_SIZE = 188
TS_SYNC_BYTE = 0x47
NULL_PID_HIGH = 0x1F
NULL_PID_LOW = 0xFF

# HTTP streaming constants
VLC_USER_AGENT = "VLC/3.0.21 LibVLC/3.0.21"

# Stream generator constants
# NOTE: These are documentation-only defaults. Actual values are read from environment
# via ConfigHelper (see config_helper.py). Update .env to customize these values.
INITIAL_DATA_WAIT_TIMEOUT = 10  # Maximum seconds to wait for initial data in buffer
INITIAL_DATA_CHECK_INTERVAL = 0.2  # Seconds between buffer checks

# No data tolerance constants - how long to wait when no data is received during streaming
# This is separate from initial data wait - this is for detecting stream end
NO_DATA_TIMEOUT_CHECKS = 60  # Number of consecutive empty checks before declaring stream ended
NO_DATA_CHECK_INTERVAL = 1  # Seconds between checks when no data is available
