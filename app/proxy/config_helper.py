"""
Configuration helper for AceStream Proxy.
Adapted from ts_proxy - uses environment variables instead of Django settings.
"""

import os


class Config:
    """Configuration class using environment variables."""
    
    # Connection timeouts
    CONNECTION_TIMEOUT = int(os.getenv('PROXY_CONNECTION_TIMEOUT', '10'))
    CLIENT_WAIT_TIMEOUT = int(os.getenv('PROXY_CLIENT_WAIT_TIMEOUT', '30'))
    STREAM_TIMEOUT = int(os.getenv('PROXY_STREAM_TIMEOUT', '60'))
    CHUNK_TIMEOUT = int(os.getenv('PROXY_CHUNK_TIMEOUT', '5'))
    
    # Buffer settings
    INITIAL_BEHIND_CHUNKS = int(os.getenv('PROXY_INITIAL_BEHIND_CHUNKS', '4'))
    CHUNK_SIZE = int(os.getenv('PROXY_CHUNK_SIZE', '8192'))
    BUFFER_CHUNK_SIZE = int(os.getenv('PROXY_BUFFER_CHUNK_SIZE', str(188 * 5644)))  # ~1MB
    
    # Redis settings
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
    REDIS_DB = int(os.getenv('REDIS_DB', '0'))
    
    # TTL settings
    CLIENT_RECORD_TTL = int(os.getenv('PROXY_CLIENT_TTL', '60'))
    REDIS_CHUNK_TTL = int(os.getenv('PROXY_BUFFER_TTL', '60'))
    
    # Cleanup and maintenance
    CLEANUP_INTERVAL = int(os.getenv('PROXY_CLEANUP_INTERVAL', '60'))
    CLEANUP_CHECK_INTERVAL = int(os.getenv('PROXY_CLEANUP_CHECK_INTERVAL', '3'))
    CLIENT_HEARTBEAT_INTERVAL = int(os.getenv('PROXY_HEARTBEAT_INTERVAL', '10'))
    KEEPALIVE_INTERVAL = float(os.getenv('PROXY_KEEPALIVE_INTERVAL', '0.5'))
    
    # Shutdown and grace periods
    CHANNEL_SHUTDOWN_DELAY = int(os.getenv('PROXY_GRACE_PERIOD', '5'))
    CHANNEL_INIT_GRACE_PERIOD = int(os.getenv('PROXY_INIT_TIMEOUT', '30'))
    
    # Retry settings
    MAX_RETRIES = int(os.getenv('PROXY_MAX_RETRIES', '3'))
    RETRY_WAIT_INTERVAL = float(os.getenv('PROXY_RETRY_WAIT_INTERVAL', '0.5'))
    
    # Health check
    HEALTH_CHECK_INTERVAL = int(os.getenv('PROXY_HEALTH_CHECK_INTERVAL', '5'))
    GHOST_CLIENT_MULTIPLIER = float(os.getenv('PROXY_GHOST_CLIENT_MULTIPLIER', '5.0'))
    
    # Stream data tolerance - how long to wait when no data is received
    # Total timeout = NO_DATA_TIMEOUT_CHECKS * NO_DATA_CHECK_INTERVAL seconds
    NO_DATA_TIMEOUT_CHECKS = int(os.getenv('PROXY_NO_DATA_TIMEOUT_CHECKS', '60'))
    NO_DATA_CHECK_INTERVAL = float(os.getenv('PROXY_NO_DATA_CHECK_INTERVAL', '1'))
    
    # Initial data wait settings
    INITIAL_DATA_WAIT_TIMEOUT = int(os.getenv('PROXY_INITIAL_DATA_WAIT_TIMEOUT', '10'))
    INITIAL_DATA_CHECK_INTERVAL = float(os.getenv('PROXY_INITIAL_DATA_CHECK_INTERVAL', '0.2'))
    
    # Stream mode (TS or HLS)
    STREAM_MODE = os.getenv('PROXY_STREAM_MODE', 'TS')  # Default to MPEG-TS for backwards compatibility
    
    # HLS-specific settings
    HLS_MAX_SEGMENTS = int(os.getenv('HLS_MAX_SEGMENTS', '20'))  # Maximum segments to buffer
    HLS_INITIAL_SEGMENTS = int(os.getenv('HLS_INITIAL_SEGMENTS', '3'))  # Minimum segments before playback
    HLS_WINDOW_SIZE = int(os.getenv('HLS_WINDOW_SIZE', '6'))  # Number of segments in manifest window
    HLS_BUFFER_READY_TIMEOUT = int(os.getenv('HLS_BUFFER_READY_TIMEOUT', '30'))  # Seconds to wait for initial buffer
    HLS_FIRST_SEGMENT_TIMEOUT = int(os.getenv('HLS_FIRST_SEGMENT_TIMEOUT', '30'))  # Seconds to wait for first segment
    HLS_INITIAL_BUFFER_SECONDS = int(os.getenv('HLS_INITIAL_BUFFER_SECONDS', '10'))  # Target duration for initial buffer
    HLS_MAX_INITIAL_SEGMENTS = int(os.getenv('HLS_MAX_INITIAL_SEGMENTS', '10'))  # Maximum segments to fetch initially
    HLS_SEGMENT_FETCH_INTERVAL = float(os.getenv('HLS_SEGMENT_FETCH_INTERVAL', '0.5'))  # Multiplier for target duration (0.5 = check twice per segment)
    
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
    def get(name, default=None):
        """Get a configuration value with a default fallback."""
        return getattr(Config, name, default)
    
    # Commonly used configuration values
    @staticmethod
    def connection_timeout():
        """Get connection timeout in seconds."""
        return Config.CONNECTION_TIMEOUT
    
    @staticmethod
    def client_wait_timeout():
        """Get client wait timeout in seconds."""
        return Config.CLIENT_WAIT_TIMEOUT
    
    @staticmethod
    def stream_timeout():
        """Get stream timeout in seconds."""
        return Config.STREAM_TIMEOUT
    
    @staticmethod
    def channel_shutdown_delay():
        """Get channel shutdown delay in seconds."""
        return Config.get_channel_shutdown_delay()
    
    @staticmethod
    def initial_behind_chunks():
        """Get number of chunks to start behind."""
        return Config.INITIAL_BEHIND_CHUNKS
    
    @staticmethod
    def keepalive_interval():
        """Get keepalive interval in seconds."""
        return Config.KEEPALIVE_INTERVAL
    
    @staticmethod
    def cleanup_check_interval():
        """Get cleanup check interval in seconds."""
        return Config.CLEANUP_CHECK_INTERVAL
    
    @staticmethod
    def redis_chunk_ttl():
        """Get Redis chunk TTL in seconds."""
        return Config.get_redis_chunk_ttl()
    
    @staticmethod
    def chunk_size():
        """Get chunk size in bytes."""
        return Config.CHUNK_SIZE
    
    @staticmethod
    def max_retries():
        """Get maximum retry attempts."""
        return Config.MAX_RETRIES
    
    @staticmethod
    def retry_wait_interval():
        """Get wait interval between connection retries in seconds."""
        return Config.RETRY_WAIT_INTERVAL
    
    @staticmethod
    def channel_init_grace_period():
        """Get channel initialization grace period in seconds."""
        return Config.get_channel_init_grace_period()
    
    @staticmethod
    def chunk_timeout():
        """
        Get chunk timeout in seconds (used for both socket and HTTP read timeouts).
        This controls how long we wait for each chunk before timing out.
        """
        return Config.CHUNK_TIMEOUT
    
    @staticmethod
    def no_data_timeout_checks():
        """Get number of consecutive empty checks before declaring stream ended."""
        return Config.NO_DATA_TIMEOUT_CHECKS
    
    @staticmethod
    def no_data_check_interval():
        """Get interval in seconds between checks when no data is available."""
        return Config.NO_DATA_CHECK_INTERVAL
    
    @staticmethod
    def initial_data_wait_timeout():
        """Get maximum seconds to wait for initial data in buffer."""
        return Config.INITIAL_DATA_WAIT_TIMEOUT
    
    @staticmethod
    def initial_data_check_interval():
        """Get seconds between buffer checks during initial data wait."""
        return Config.INITIAL_DATA_CHECK_INTERVAL
    
    @staticmethod
    def stream_mode():
        """Get stream mode (TS or HLS)."""
        return Config.STREAM_MODE
    
    # HLS-specific configuration helpers
    @staticmethod
    def hls_max_segments():
        """Get maximum number of HLS segments to buffer."""
        return Config.HLS_MAX_SEGMENTS
    
    @staticmethod
    def hls_initial_segments():
        """Get minimum number of HLS segments before playback starts."""
        return Config.HLS_INITIAL_SEGMENTS
    
    @staticmethod
    def hls_window_size():
        """Get number of segments in HLS manifest window."""
        return Config.HLS_WINDOW_SIZE
    
    @staticmethod
    def hls_buffer_ready_timeout():
        """Get timeout in seconds for HLS initial buffer to be ready."""
        return Config.HLS_BUFFER_READY_TIMEOUT
    
    @staticmethod
    def hls_first_segment_timeout():
        """Get timeout in seconds for first HLS segment to be available."""
        return Config.HLS_FIRST_SEGMENT_TIMEOUT
    
    @staticmethod
    def hls_initial_buffer_seconds():
        """Get target duration in seconds for HLS initial buffer."""
        return Config.HLS_INITIAL_BUFFER_SECONDS
    
    @staticmethod
    def hls_max_initial_segments():
        """Get maximum number of segments to fetch during HLS initial buffering."""
        return Config.HLS_MAX_INITIAL_SEGMENTS
    
    @staticmethod
    def hls_segment_fetch_interval():
        """Get multiplier for manifest fetch interval (relative to target duration)."""
        return Config.HLS_SEGMENT_FETCH_INTERVAL
