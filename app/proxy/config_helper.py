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
    NO_DATA_TIMEOUT_CHECKS = int(os.getenv('PROXY_NO_DATA_TIMEOUT_CHECKS', '30'))
    NO_DATA_CHECK_INTERVAL = float(os.getenv('PROXY_NO_DATA_CHECK_INTERVAL', '0.1'))
    
    # Initial data wait settings
    INITIAL_DATA_WAIT_TIMEOUT = int(os.getenv('PROXY_INITIAL_DATA_WAIT_TIMEOUT', '10'))
    INITIAL_DATA_CHECK_INTERVAL = float(os.getenv('PROXY_INITIAL_DATA_CHECK_INTERVAL', '0.2'))
    
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
