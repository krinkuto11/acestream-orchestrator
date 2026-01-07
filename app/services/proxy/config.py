"""Proxy configuration and constants"""

from typing import Final

# Stream session timeouts
EMPTY_STREAM_TIMEOUT: Final[int] = 30  # Seconds to wait for data before considering stream empty
STREAM_IDLE_TIMEOUT: Final[int] = 300  # Seconds of no clients before cleaning up stream (5 min)
CLIENT_HEARTBEAT_INTERVAL: Final[int] = 10  # Seconds between client heartbeats
CLIENT_TIMEOUT: Final[int] = 30  # Seconds before considering client disconnected

# Buffer sizes
STREAM_BUFFER_SIZE: Final[int] = 4 * 1024 * 1024  # 4MB buffer for smooth streaming
COPY_CHUNK_SIZE: Final[int] = 64 * 1024  # 64KB chunks for copying

# HTTP headers for AceStream communication
# AceStream engines require User-Agent to identify client as media player
USER_AGENT: Final[str] = "VLC/3.0.21 LibVLC/3.0.21"

# HTTP client configuration for AceStream compatibility
# Based on acexy reference: compression must be disabled for AceStream middleware to work properly
MAX_CONNECTIONS: Final[int] = 10  # Maximum connections per host
MAX_KEEPALIVE_CONNECTIONS: Final[int] = 10  # Maximum keepalive connections
KEEPALIVE_EXPIRY: Final[int] = 30  # Seconds before keepalive connection expires

# Engine selection
ENGINE_SELECTION_TIMEOUT: Final[int] = 5  # Seconds to wait for engine selection
ENGINE_CACHE_TTL: Final[int] = 2  # Seconds to cache engine list
MAX_STREAMS_PER_ENGINE: Final[int] = 10  # Maximum concurrent streams per engine

# Retry configuration
MAX_ENGINE_RETRIES: Final[int] = 3  # Max retries for engine failures
RETRY_DELAY: Final[int] = 1  # Seconds between retries

# Stream types
STREAM_TYPE_MPEGTS = "mpegts"
STREAM_TYPE_HLS = "hls"

# Stream buffer configuration
MEMORY_BUFFER_SIZE: Final[int] = 100  # Number of chunks to keep in memory fallback
CATCHUP_THRESHOLD_CHUNKS: Final[int] = 50  # Chunks behind before auto-catchup
TIMEOUT_MAX_EMPTY_CYCLES: Final[int] = 300  # Empty read cycles before timeout (~30s at 0.1s sleep)

# Session cleanup
SESSION_CLEANUP_INTERVAL: Final[int] = 60  # Seconds between cleanup runs
