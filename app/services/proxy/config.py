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

# Session cleanup
SESSION_CLEANUP_INTERVAL: Final[int] = 60  # Seconds between cleanup runs
