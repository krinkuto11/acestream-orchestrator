"""
Redis key patterns for AceStream Proxy.
Adapted from ts_proxy - centralizing key patterns for maintainability.
"""

class RedisKeys:
    @staticmethod
    def stream_metadata(content_id):
        """Key for stream metadata hash"""
        return f"ace_proxy:stream:{content_id}:metadata"
    
    @staticmethod
    def buffer_index(content_id):
        """Key for tracking buffer index"""
        return f"ace_proxy:stream:{content_id}:buffer:index"
    
    @staticmethod
    def buffer_chunk(content_id, chunk_index):
        """Key for specific buffer chunk"""
        return f"ace_proxy:stream:{content_id}:buffer:chunk:{chunk_index}"
    
    @staticmethod
    def buffer_chunk_prefix(content_id):
        """Prefix for buffer chunks"""
        return f"ace_proxy:stream:{content_id}:buffer:chunk:"
    
    @staticmethod
    def stream_stopping(content_id):
        """Key indicating stream is stopping"""
        return f"ace_proxy:stream:{content_id}:stopping"
    
    @staticmethod
    def client_stop(content_id, client_id):
        """Key requesting client stop"""
        return f"ace_proxy:stream:{content_id}:client:{client_id}:stop"
    
    @staticmethod
    def events_channel(content_id):
        """PubSub channel for events"""
        return f"ace_proxy:events:{content_id}"
    
    @staticmethod
    def stream_owner(content_id):
        """Key for storing stream owner worker ID"""
        return f"ace_proxy:stream:{content_id}:owner"
    
    @staticmethod
    def clients(content_id):
        """Key for set of client IDs"""
        return f"ace_proxy:stream:{content_id}:clients"
    
    @staticmethod
    def last_client_disconnect(content_id):
        """Key for last client disconnect timestamp"""
        return f"ace_proxy:stream:{content_id}:last_client_disconnect_time"
    
    @staticmethod
    def connection_attempt(content_id):
        """Key for connection attempt timestamp"""
        return f"ace_proxy:stream:{content_id}:connection_attempt_time"
    
    @staticmethod
    def last_data(content_id):
        """Key for last data timestamp"""
        return f"ace_proxy:stream:{content_id}:last_data"
    
    @staticmethod
    def worker_heartbeat(worker_id):
        """Key for worker heartbeat"""
        return f"ace_proxy:worker:{worker_id}:heartbeat"
    
    @staticmethod
    def client_metadata(content_id, client_id):
        """Key for client metadata hash"""
        return f"ace_proxy:stream:{content_id}:clients:{client_id}"
