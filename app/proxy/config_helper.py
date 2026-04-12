"""
Configuration helper for AceStream Proxy.
Adapted from ts_proxy - uses environment variables instead of Django settings.
"""

import os

from .constants import PROXY_MODE_HTTP, PROXY_MODE_API, normalize_proxy_mode


class Config:
    """Configuration class using environment variables."""
    
    # Connection timeouts
    CONNECTION_TIMEOUT: int = 30
    UPSTREAM_CONNECT_TIMEOUT: int = 3
    UPSTREAM_READ_TIMEOUT: int = 90
    CLIENT_WAIT_TIMEOUT: int = 30
    STREAM_TIMEOUT: int = 60
    CHUNK_TIMEOUT: int = 5
    
    # Buffer settings
    INITIAL_BEHIND_CHUNKS: int = 4
    CHUNK_SIZE: int = 8192
    BUFFER_CHUNK_SIZE: int = int(188 * 5644)  # ~1MB
    
    # Redis settings
    REDIS_HOST: str = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT: int = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_DB: int = int(os.getenv('REDIS_DB', '0'))
    
    # TTL settings
    CLIENT_RECORD_TTL: int = 60
    REDIS_CHUNK_TTL: int = 60
    
    # Cleanup and maintenance
    CLEANUP_INTERVAL: int = 60
    CLEANUP_CHECK_INTERVAL: int = 3
    CLIENT_HEARTBEAT_INTERVAL: int = 10
    KEEPALIVE_INTERVAL: float = 0.5
    
    # Shutdown and grace periods
    CHANNEL_SHUTDOWN_DELAY: int = 5
    CHANNEL_INIT_GRACE_PERIOD: int = 30
    
    # Retry settings
    MAX_RETRIES: int = 3
    RETRY_WAIT_INTERVAL: float = 0.5
    
    # Health check
    HEALTH_CHECK_INTERVAL: int = 5
    PROXY_MAX_CATCHUP_MULTIPLIER: float = 2.0
    GHOST_CLIENT_MULTIPLIER: float = 5.0
    
    # Stream data tolerance - how long to wait when no data is received
    # Total timeout = NO_DATA_TIMEOUT_CHECKS * NO_DATA_CHECK_INTERVAL seconds
    NO_DATA_TIMEOUT_CHECKS: int = 60
    NO_DATA_CHECK_INTERVAL: float = 1.0
    
    # Initial data wait settings
    INITIAL_DATA_WAIT_TIMEOUT: int = 10
    INITIAL_DATA_CHECK_INTERVAL: float = 0.2

    # Unified prebuffer for TS/HLS startup holdback (seconds)
    PROXY_PREBUFFER_SECONDS: int = 0
    
    # Stream mode (TS or HLS)
    STREAM_MODE: str = 'TS'  # Default to MPEG-TS for backwards compatibility

    # Engine control mode
    # http: JSON-over-HTTP control flow
    # api: telnet-style AceStream API control flow (optional)
    CONTROL_MODE: str = PROXY_MODE_API

    # API-mode preflight tier used before START when proxy control mode is api.
    # light: resolve/canonicalize only
    # deep: resolve + start + status/livepos sampling + stop
    LEGACY_API_PREFLIGHT_TIER: str = 'light'
    
    # HLS-specific settings
    HLS_MAX_SEGMENTS: int = 20  # Maximum segments to buffer
    HLS_INITIAL_SEGMENTS: int = 3  # Minimum segments before playback
    HLS_WINDOW_SIZE: int = 6  # Number of segments in manifest window
    HLS_BUFFER_READY_TIMEOUT: int = 30  # Seconds to wait for initial buffer
    HLS_FIRST_SEGMENT_TIMEOUT: int = 30  # Seconds to wait for first segment
    HLS_INITIAL_BUFFER_SECONDS: int = 10  # Target duration for initial buffer
    HLS_MAX_INITIAL_SEGMENTS: int = 10  # Maximum segments to fetch initially
    HLS_SEGMENT_FETCH_INTERVAL: float = 0.5  # Multiplier for target duration (0.5 = check twice per segment)
    
    @staticmethod
    def get_channel_shutdown_delay():
        """Get channel shutdown delay in seconds."""
        return Config.CHANNEL_SHUTDOWN_DELAY
    
    @staticmethod
    def get_redis_chunk_ttl():
        """Get Redis chunk TTL in seconds."""
        return Config.REDIS_CHUNK_TTL
    
    @staticmethod
    def get_channel_init_grace_period():
        """Get channel initialization grace period in seconds."""
        return Config.CHANNEL_INIT_GRACE_PERIOD


class ConfigHelper:
    """
    Helper class for accessing configuration values with sensible defaults.
    This simplifies code and ensures consistent defaults across the application.
    """
    
    @staticmethod
    def _proxy_settings():
        try:
            from ..services.settings_persistence import SettingsPersistence

            return SettingsPersistence.load_proxy_config() or {}
        except Exception:
            return {}

    @staticmethod
    def _get_proxy_value(key: str, fallback):
        try:
            from ..services.settings_persistence import SettingsPersistence

            return SettingsPersistence.get_cached_setting("proxy_settings", key, fallback)
        except Exception:
            return fallback

    @staticmethod
    def get(name, default=None):
        """Get a configuration value with a default fallback."""
        settings = ConfigHelper._proxy_settings()
        if name in settings:
            return settings[name]
        return getattr(Config, name, default)
    
    # Commonly used configuration values
    @staticmethod
    def connection_timeout():
        """Get connection timeout in seconds."""
        return ConfigHelper._get_proxy_value("connection_timeout", Config.CONNECTION_TIMEOUT)

    @staticmethod
    def upstream_connect_timeout():
        """Get upstream connect timeout in seconds for HTTP/API engine calls."""
        return ConfigHelper._get_proxy_value("upstream_connect_timeout", Config.UPSTREAM_CONNECT_TIMEOUT)

    @staticmethod
    def upstream_read_timeout():
        """Get upstream read timeout in seconds for HTTP/API engine calls."""
        return ConfigHelper._get_proxy_value("upstream_read_timeout", Config.UPSTREAM_READ_TIMEOUT)
    
    @staticmethod
    def client_wait_timeout():
        """Get client wait timeout in seconds."""
        return ConfigHelper._get_proxy_value("client_wait_timeout", Config.CLIENT_WAIT_TIMEOUT)
    
    @staticmethod
    def stream_timeout():
        """Get stream timeout in seconds."""
        return ConfigHelper._get_proxy_value("stream_timeout", Config.STREAM_TIMEOUT)
    
    @staticmethod
    def channel_shutdown_delay():
        """Get channel shutdown delay in seconds."""
        return ConfigHelper._get_proxy_value("channel_shutdown_delay", Config.get_channel_shutdown_delay())
    
    @staticmethod
    def initial_behind_chunks():
        """Get number of chunks to start behind."""
        return ConfigHelper._get_proxy_value("initial_behind_chunks", Config.INITIAL_BEHIND_CHUNKS)
    
    @staticmethod
    def keepalive_interval():
        """Get keepalive interval in seconds."""
        return ConfigHelper._get_proxy_value("keepalive_interval", Config.KEEPALIVE_INTERVAL)
    
    @staticmethod
    def cleanup_check_interval():
        """Get cleanup check interval in seconds."""
        return ConfigHelper._get_proxy_value("cleanup_check_interval", Config.CLEANUP_CHECK_INTERVAL)
    
    @staticmethod
    def redis_chunk_ttl():
        """Get Redis chunk TTL in seconds."""
        return ConfigHelper._get_proxy_value("redis_chunk_ttl", Config.get_redis_chunk_ttl())
    
    @staticmethod
    def chunk_size():
        """Get chunk size in bytes."""
        return ConfigHelper._get_proxy_value("chunk_size", Config.CHUNK_SIZE)
    
    @staticmethod
    def max_retries():
        """Get maximum retry attempts."""
        return ConfigHelper._get_proxy_value("max_retries", Config.MAX_RETRIES)
    
    @staticmethod
    def retry_wait_interval():
        """Get wait interval between connection retries in seconds."""
        return ConfigHelper._get_proxy_value("retry_wait_interval", Config.RETRY_WAIT_INTERVAL)
    
    @staticmethod
    def channel_init_grace_period():
        """Get channel initialization grace period in seconds."""
        return ConfigHelper._get_proxy_value("channel_init_grace_period", Config.get_channel_init_grace_period())
    
    @staticmethod
    def chunk_timeout():
        """
        Get chunk timeout in seconds (used for both socket and HTTP read timeouts).
        This controls how long we wait for each chunk before timing out.
        """
        return ConfigHelper._get_proxy_value("chunk_timeout", Config.CHUNK_TIMEOUT)
    
    @staticmethod
    def no_data_timeout_checks():
        """Get number of consecutive empty checks before declaring stream ended."""
        return ConfigHelper._get_proxy_value("no_data_timeout_checks", Config.NO_DATA_TIMEOUT_CHECKS)
    
    @staticmethod
    def no_data_check_interval():
        """Get interval in seconds between checks when no data is available."""
        return ConfigHelper._get_proxy_value("no_data_check_interval", Config.NO_DATA_CHECK_INTERVAL)
    
    @staticmethod
    def initial_data_wait_timeout():
        """Get maximum seconds to wait for initial data in buffer."""
        return ConfigHelper._get_proxy_value("initial_data_wait_timeout", Config.INITIAL_DATA_WAIT_TIMEOUT)
    
    @staticmethod
    def initial_data_check_interval():
        """Get seconds between buffer checks during initial data wait."""
        return ConfigHelper._get_proxy_value("initial_data_check_interval", Config.INITIAL_DATA_CHECK_INTERVAL)
    
    @staticmethod
    def stream_mode():
        """Get stream mode (TS or HLS)."""
        return ConfigHelper._get_proxy_value("stream_mode", Config.STREAM_MODE)

    @staticmethod
    def control_mode():
        """Get engine control mode (http or api)."""
        normalized = normalize_proxy_mode(
            ConfigHelper._get_proxy_value("control_mode", Config.CONTROL_MODE),
            default=PROXY_MODE_HTTP,
        )
        Config.CONTROL_MODE = normalized
        return normalized

    @staticmethod
    def is_api_mode():
        """Return True when control mode uses AceStream API socket commands."""
        return ConfigHelper.control_mode() == PROXY_MODE_API

    @staticmethod
    def legacy_api_preflight_tier():
        """Get API-mode preflight tier (light or deep)."""
        tier = str(ConfigHelper._get_proxy_value("legacy_api_preflight_tier", Config.LEGACY_API_PREFLIGHT_TIER) or "light").strip().lower()
        return tier if tier in {"light", "deep"} else "light"
    
    # HLS-specific configuration helpers
    @staticmethod
    def hls_max_segments():
        """Get maximum number of HLS segments to buffer."""
        return ConfigHelper._get_proxy_value("hls_max_segments", Config.HLS_MAX_SEGMENTS)
    
    @staticmethod
    def hls_initial_segments():
        """Get minimum number of HLS segments before playback starts."""
        return ConfigHelper._get_proxy_value("hls_initial_segments", Config.HLS_INITIAL_SEGMENTS)
    
    @staticmethod
    def hls_window_size():
        """Get number of segments in HLS manifest window."""
        return ConfigHelper._get_proxy_value("hls_window_size", Config.HLS_WINDOW_SIZE)
    
    @staticmethod
    def hls_buffer_ready_timeout():
        """Get timeout in seconds for HLS initial buffer to be ready."""
        return ConfigHelper._get_proxy_value("hls_buffer_ready_timeout", Config.HLS_BUFFER_READY_TIMEOUT)
    
    @staticmethod
    def hls_first_segment_timeout():
        """Get timeout in seconds for first HLS segment to be available."""
        return ConfigHelper._get_proxy_value("hls_first_segment_timeout", Config.HLS_FIRST_SEGMENT_TIMEOUT)

    @staticmethod
    def proxy_prebuffer_seconds():
        """Get unified proxy prebuffer holdback duration in seconds (0 disables)."""
        try:
            value = int(ConfigHelper._get_proxy_value("proxy_prebuffer_seconds", Config.PROXY_PREBUFFER_SECONDS))
        except Exception:
            value = int(Config.PROXY_PREBUFFER_SECONDS)
        return max(0, value)
    
    @staticmethod
    def hls_initial_buffer_seconds():
        """Get target duration in seconds for HLS initial buffer.

        Unified behavior: when proxy_prebuffer_seconds is set (>0), HLS uses that
        same value to keep TS and HLS startup holdback aligned.
        """
        unified_prebuffer = ConfigHelper.proxy_prebuffer_seconds()
        if unified_prebuffer > 0:
            return unified_prebuffer
        return ConfigHelper._get_proxy_value("hls_initial_buffer_seconds", Config.HLS_INITIAL_BUFFER_SECONDS)
    
    @staticmethod
    def hls_max_initial_segments():
        """Get maximum number of segments to fetch during HLS initial buffering."""
        return ConfigHelper._get_proxy_value("hls_max_initial_segments", Config.HLS_MAX_INITIAL_SEGMENTS)
    
    @staticmethod
    def hls_segment_fetch_interval():
        """Get multiplier for manifest fetch interval (relative to target duration)."""
        return ConfigHelper._get_proxy_value("hls_segment_fetch_interval", Config.HLS_SEGMENT_FETCH_INTERVAL)

    @staticmethod
    def proxy_max_catchup_multiplier():
        """Get maximum speed multiplier for client catch-up pacing (1.0 = real-time, 2.0 = double-time)."""
        return float(ConfigHelper._get_proxy_value("proxy_max_catchup_multiplier", Config.PROXY_MAX_CATCHUP_MULTIPLIER))
