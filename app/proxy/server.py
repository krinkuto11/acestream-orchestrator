"""
AceStream Proxy Server - manages proxy sessions with worker coordination.
Simplified adaptation from ts_proxy - focuses on core functionality.
"""

import threading
import logging
import socket
import os
import time
import json
import redis

from .stream_manager import StreamManager
from .stream_buffer import StreamBuffer
from .client_manager import ClientManager
from .redis_keys import RedisKeys
from .constants import StreamState, EventType
from .config_helper import Config, ConfigHelper
from .utils import get_logger

logger = get_logger()


class ProxyServer:
    """Manages AceStream proxy server instance with worker coordination"""
    _instance = None
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ProxyServer()
        return cls._instance
    
    def __init__(self):
        """Initialize proxy server with worker identification"""
        self.stream_managers = {}
        self.stream_buffers = {}
        self.client_managers = {}
        
        # Generate unique worker ID
        pid = os.getpid()
        hostname = socket.gethostname()
        self.worker_id = f"{hostname}:{pid}"
        
        # Connect to Redis
        self.redis_client = None
        self._setup_redis_connection()
        
        # Start cleanup thread
        self.cleanup_interval = Config.CLEANUP_INTERVAL
        self._start_cleanup_thread()
        
        # Start event listener
        self._start_event_listener()
        
        logger.info(f"ProxyServer initialized with worker_id={self.worker_id}")
    
    def _setup_redis_connection(self):
        """Setup Redis connection"""
        try:
            self.redis_client = redis.Redis(
                host=Config.REDIS_HOST,
                port=Config.REDIS_PORT,
                db=Config.REDIS_DB,
                decode_responses=False
            )
            self.redis_client.ping()
            logger.info("Connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.redis_client = None
    
    def _start_cleanup_thread(self):
        """Start cleanup thread to manage idle sessions"""
        def cleanup_task():
            while True:
                try:
                    time.sleep(self.cleanup_interval)
                    self._cleanup_idle_sessions()
                except Exception as e:
                    logger.error(f"Error in cleanup thread: {e}")
        
        thread = threading.Thread(target=cleanup_task, daemon=True)
        thread.name = "proxy-cleanup"
        thread.start()
        logger.info("Started cleanup thread")
    
    def _start_event_listener(self):
        """Start event listener for Redis PubSub"""
        def event_listener_task():
            if not self.redis_client:
                logger.warning("No Redis client, event listener not started")
                return
            
            # Create separate Redis connection for pubsub
            pubsub_client = redis.Redis(
                host=Config.REDIS_HOST,
                port=Config.REDIS_PORT,
                db=Config.REDIS_DB
            )
            pubsub = pubsub_client.pubsub()
            
            # Subscribe to all stream events (wildcard pattern)
            pubsub.psubscribe("ace_proxy:events:*")
            
            logger.info("Event listener started")
            
            for message in pubsub.listen():
                try:
                    if message['type'] == 'pmessage':
                        self._handle_event(message)
                except Exception as e:
                    logger.error(f"Error handling event: {e}")
        
        thread = threading.Thread(target=event_listener_task, daemon=True)
        thread.name = "event-listener"
        thread.start()
    
    def _handle_event(self, message):
        """Handle PubSub event"""
        try:
            data = json.loads(message['data'])
            event_type = data.get('event')
            content_id = data.get('content_id')
            
            if event_type == EventType.CLIENT_DISCONNECTED:
                # Check if we're the owner
                if self.am_i_owner(content_id):
                    remaining = data.get('remaining_clients', 0)
                    if remaining == 0:
                        logger.info(f"Last client disconnected for {content_id}, scheduling cleanup")
                        # Use threading.Timer instead of gevent.spawn_later
                        timer = threading.Timer(Config.CHANNEL_SHUTDOWN_DELAY, self._stop_stream, args=[content_id])
                        timer.daemon = True
                        timer.start()
        
        except Exception as e:
            logger.error(f"Error handling event: {e}")
    
    def start_stream(self, content_id, engine_host, engine_port, engine_container_id=None):
        """Start a new stream session"""
        if content_id in self.stream_managers:
            logger.info(f"Stream already exists for content_id={content_id}")
            return True
        
        try:
            # Get API key from environment
            api_key = os.getenv('API_KEY')
            
            # Create Redis buffer
            buffer = StreamBuffer(content_id=content_id, redis_client=self.redis_client)
            self.stream_buffers[content_id] = buffer
            
            # Create client manager
            client_manager = ClientManager(
                content_id=content_id,
                redis_client=self.redis_client,
                worker_id=self.worker_id
            )
            self.client_managers[content_id] = client_manager
            
            # Create stream manager
            stream_manager = StreamManager(
                content_id=content_id,
                engine_host=engine_host,
                engine_port=engine_port,
                engine_container_id=engine_container_id,
                buffer=buffer,
                client_manager=client_manager,
                worker_id=self.worker_id,
                api_key=api_key
            )
            self.stream_managers[content_id] = stream_manager
            
            # Set owner in Redis
            owner_key = RedisKeys.stream_owner(content_id)
            self.redis_client.set(owner_key, self.worker_id, ex=300)
            
            # Start stream manager in background thread
            # Using threading.Thread instead of gevent.spawn because uvicorn doesn't use gevent worker
            thread = threading.Thread(target=stream_manager.run, daemon=True, name=f"stream-{content_id[:8]}")
            thread.start()
            
            logger.info(f"Started stream for content_id={content_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start stream: {e}")
            return False
    
    def stop_stream_by_key(self, content_id: str):
        """
        Public method to stop a stream by its content ID (stream key).
        This is called when a stream ends in the orchestrator state to ensure
        proxy sessions are cleaned up synchronously.
        
        :param content_id: The AceStream content ID (infohash or content key)
        :type content_id: str
        """
        if content_id not in self.stream_managers:
            logger.debug(f"No active proxy session for content_id={content_id}, nothing to clean up")
            return
        
        logger.info(f"Stopping proxy session for content_id={content_id} (called from state synchronization)")
        self._stop_stream(content_id)
    
    def _stop_stream(self, content_id):
        """Stop a stream session (internal method)"""
        logger.info(f"Stopping stream for content_id={content_id}")
        
        # Stop stream manager
        stream_manager = self.stream_managers.get(content_id)
        if stream_manager:
            stream_manager.stop()
            del self.stream_managers[content_id]
        
        # Stop client manager
        client_manager = self.client_managers.get(content_id)
        if client_manager:
            client_manager.stop()
            del self.client_managers[content_id]
        
        # Stop buffer
        buffer = self.stream_buffers.get(content_id)
        if buffer:
            buffer.stop()
            del self.stream_buffers[content_id]
        
        # Remove owner from Redis
        owner_key = RedisKeys.stream_owner(content_id)
        self.redis_client.delete(owner_key)
        
        logger.info(f"Stream stopped for content_id={content_id}")
    
    def _cleanup_idle_sessions(self):
        """Clean up idle sessions with no clients"""
        for content_id in list(self.stream_managers.keys()):
            try:
                client_manager = self.client_managers.get(content_id)
                if client_manager:
                    client_count = client_manager.get_total_client_count()
                    
                    if client_count == 0:
                        # Check how long it's been since last client
                        disconnect_key = RedisKeys.last_client_disconnect(content_id)
                        last_disconnect = self.redis_client.get(disconnect_key)
                        
                        if last_disconnect:
                            last_disconnect_time = float(last_disconnect.decode('utf-8'))
                            idle_time = time.time() - last_disconnect_time
                            
                            if idle_time > Config.CHANNEL_SHUTDOWN_DELAY:
                                logger.info(f"Cleaning up idle stream {content_id} (idle for {idle_time:.1f}s)")
                                self._stop_stream(content_id)
            
            except Exception as e:
                logger.error(f"Error cleaning up session {content_id}: {e}")
    
    def am_i_owner(self, content_id):
        """Check if this worker owns the stream"""
        try:
            owner_key = RedisKeys.stream_owner(content_id)
            owner = self.redis_client.get(owner_key)
            if owner:
                return owner.decode('utf-8') == self.worker_id
        except Exception as e:
            logger.error(f"Error checking ownership: {e}")
        return False
    
    def handle_client_disconnect(self, content_id):
        """Handle client disconnect event"""
        # Check if we're the owner
        if not self.am_i_owner(content_id):
            return
        
        # Check client count
        client_manager = self.client_managers.get(content_id)
        if client_manager:
            client_count = client_manager.get_total_client_count()
            
            if client_count == 0:
                logger.info(f"No clients left for {content_id}, scheduling stop")
                # Use threading.Timer instead of gevent.spawn_later
                timer = threading.Timer(Config.CHANNEL_SHUTDOWN_DELAY, self._stop_stream, args=[content_id])
                timer.daemon = True
                timer.start()
