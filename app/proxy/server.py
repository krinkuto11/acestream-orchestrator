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
        self.shutdown_timers = {}
        
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

    def _get_shutdown_timers(self):
        """Return shutdown timer registry, creating it for __new__-constructed test stubs."""
        timers = getattr(self, "shutdown_timers", None)
        if timers is None:
            timers = {}
            self.shutdown_timers = timers
        return timers
    
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
            
            # Inject Redis client into unified tracker for PubSub parity
            from ..services.client_tracker import client_tracking_service
            client_tracking_service.set_redis_client(self.redis_client)
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
                        logger.info(f"[TS:{content_id}] Last client disconnected, scheduling cleanup")
                        shutdown_timers = self._get_shutdown_timers()
                        existing_timer = shutdown_timers.get(content_id)
                        if existing_timer:
                            existing_timer.cancel()
                        # Use threading.Timer instead of gevent.spawn_later
                        timer = threading.Timer(Config.CHANNEL_SHUTDOWN_DELAY, self._stop_stream, args=[content_id])
                        shutdown_timers[content_id] = timer
                        timer.daemon = True
                        timer.start()
        
        except Exception as e:
            logger.error(f"Error handling event: {e}")
    
    def start_stream(
        self,
        content_id,
        engine_host,
        engine_port,
        engine_container_id=None,
        engine_api_port=None,
        existing_session=None,
        source_input=None,
        source_input_type="content_id",
        file_indexes="0",
        seekback=0,
        playback_url=None,
        playback_session_id=None,
        stat_url=None,
        command_url=None,
        is_live=None,
        ace_api_client=None,
        bitrate: int = 0,
    ):
        """Start a new stream session"""
        if content_id in self.stream_managers:
            stream_manager = self.stream_managers[content_id]
            if getattr(stream_manager, 'running', False):
                shutdown_timers = self._get_shutdown_timers()
                existing_timer = shutdown_timers.get(content_id)
                if existing_timer:
                    existing_timer.cancel()
                    shutdown_timers.pop(content_id, None)
                    logger.info(f"[TS:{content_id}] Canceled pending shutdown timer")
                
                # Check if we need to update the bitrate of the existing manager
                if bitrate > 0 and getattr(stream_manager, 'bitrate', 0) == 0:
                    stream_manager.bitrate = bitrate
                    logger.info(f"[TS:{content_id}] Updated bitrate for existing session: {bitrate} bps")

                logger.info(f"[TS:{content_id}] Stream already exists and is active")
                return True

            logger.info(f"[TS:{content_id}] Stream exists but thread is dead, cleaning up for restart")
            self._stop_stream(content_id)
        
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
                engine_api_port=engine_api_port,
                engine_container_id=engine_container_id,
                buffer=buffer,
                client_manager=client_manager,
                worker_id=self.worker_id,
                api_key=api_key,
                existing_session=existing_session,
                source_input=source_input,
                source_input_type=source_input_type,
                file_indexes=file_indexes,
                seekback=seekback,
                playback_url=playback_url,
                playback_session_id=playback_session_id,
                stat_url=stat_url,
                command_url=command_url,
                is_live=is_live,
                ace_api_client=ace_api_client,
                bitrate=bitrate,
            )
            self.stream_managers[content_id] = stream_manager
            
            # Set owner and init_time in Redis
            owner_key = RedisKeys.stream_owner(content_id)
            init_key = RedisKeys.stream_init_time(content_id)
            self.redis_client.set(owner_key, self.worker_id, ex=300)
            self.redis_client.setex(init_key, 3600, str(time.time()))
            
            # Start stream manager in background thread
            # Using threading.Thread instead of gevent.spawn because uvicorn doesn't use gevent worker
            thread = threading.Thread(target=stream_manager.run, daemon=True, name=f"stream-{content_id[:8]}")
            thread.start()
            
            logger.info(f"[TS:{content_id}] Started stream session")
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
        
        logger.info(f"[TS:{content_id}] Stopping proxy session (called from state synchronization)")
        self._stop_stream(content_id)

    def migrate_stream(self, content_id: str, new_engine) -> dict:
        """Hot-swap an active TS stream to a new engine without dropping client sockets."""
        stream_manager = self.stream_managers.get(content_id)
        if not stream_manager:
            return {
                "migrated": False,
                "reason": "ts_stream_not_found",
                "stream_type": "TS",
            }

        old_container_id = str(getattr(stream_manager, "engine_container_id", "") or "")
        target_container_id = str(getattr(new_engine, "container_id", "") or "")

        # EXTEND GRACE PERIOD: Reset activity timers during migration
        try:
            now_str = str(time.time())
            init_key = RedisKeys.stream_init_time(content_id)
            disconnect_key = RedisKeys.last_client_disconnect(content_id)
            pipe = self.redis_client.pipeline(transaction=False)
            pipe.setex(init_key, 3600, now_str)
            pipe.setex(disconnect_key, 60, now_str)
            pipe.execute()
            logger.debug(f"Reset activity timers during migration of content_id={content_id}")
        except Exception as e:
            logger.warning(f"Failed to reset activity timers during migration of {content_id}: {e}")

        if not target_container_id:
            return {
                "migrated": False,
                "reason": "invalid_target_engine",
                "stream_type": "TS",
                "old_container_id": old_container_id,
            }

        if old_container_id and old_container_id == target_container_id:
            return {
                "migrated": False,
                "reason": "already_on_target_engine",
                "stream_type": "TS",
                "old_container_id": old_container_id,
                "new_container_id": target_container_id,
            }

        swap_result = stream_manager.hot_swap_engine(
            new_host=str(getattr(new_engine, "host", "") or ""),
            new_port=int(getattr(new_engine, "port", 0) or 0),
            new_api_port=int(getattr(new_engine, "api_port", 62062) or 62062),
            new_container_id=target_container_id,
        )

        return {
            "migrated": bool(swap_result.get("swapped", False)),
            "reason": str(swap_result.get("reason") or ""),
            "stream_type": "TS",
            "old_container_id": old_container_id,
            "new_container_id": target_container_id,
            "session_updates": {
                "playback_session_id": swap_result.get("playback_session_id"),
                "stat_url": swap_result.get("stat_url"),
                "command_url": swap_result.get("command_url"),
                "is_live": swap_result.get("is_live"),
            },
        }

    def seek_stream_by_key(self, content_id: str, target_timestamp: int):
        """Seek an active proxy session using LIVESEEK in API mode."""
        stream_manager = self.stream_managers.get(content_id)
        if not stream_manager:
            raise RuntimeError(f"No active proxy session for stream key '{content_id}'")
        return stream_manager.seek_stream(target_timestamp)
    
    def _stop_stream(self, content_id):
        """Stop a stream session (internal method)"""
        logger.info(f"[TS:{content_id}] Stopping stream")

        shutdown_timers = self._get_shutdown_timers()
        existing_timer = shutdown_timers.pop(content_id, None)
        if existing_timer:
            existing_timer.cancel()
        
        # Stop stream manager atomically
        stream_manager = self.stream_managers.pop(content_id, None)
        if stream_manager:
            stream_manager.stop()
        
        # Stop client manager atomically
        client_manager = self.client_managers.pop(content_id, None)
        if client_manager:
            client_manager.stop()
        
        # Stop buffer atomically
        buffer = self.stream_buffers.pop(content_id, None)
        if buffer:
            buffer.stop()

        # Flush all Redis keys related to this stream so stale cache does not survive stream end.
        self._flush_stream_redis_cache(content_id)
        
        logger.info(f"[TS:{content_id}] Stream stopped and cleaned up")

    def _flush_stream_redis_cache(self, content_id: str) -> int:
        """Remove Redis keys associated with one proxy stream.

        This is called on stream teardown so stream-end paths deterministically
        clear buffered chunks and per-stream metadata/client bookkeeping.
        """
        if not self.redis_client:
            return 0

        deleted_count = 0

        direct_keys = [
            RedisKeys.stream_owner(content_id),
            RedisKeys.stream_metadata(content_id),
            RedisKeys.buffer_index(content_id),
            RedisKeys.stream_stopping(content_id),
            RedisKeys.clients(content_id),
            RedisKeys.last_client_disconnect(content_id),
            RedisKeys.connection_attempt(content_id),
            RedisKeys.last_data(content_id),
            f"ace_proxy:stream:{content_id}:activity",
            RedisKeys.stream_init_time(content_id),
        ]

        wildcard_patterns = [
            f"{RedisKeys.buffer_chunk_prefix(content_id)}*",
            f"ace_proxy:stream:{content_id}:clients:*",
            f"ace_proxy:stream:{content_id}:worker:*",
        ]

        try:
            if direct_keys:
                deleted_count += int(self.redis_client.delete(*direct_keys) or 0)
        except Exception as e:
            logger.warning(f"Failed deleting direct Redis keys for stream {content_id}: {e}")

        for pattern in wildcard_patterns:
            try:
                matched_keys = list(self.redis_client.scan_iter(match=pattern, count=500))
                if not matched_keys:
                    continue
                deleted_count += int(self.redis_client.delete(*matched_keys) or 0)
            except Exception as e:
                logger.warning(
                    "Failed deleting wildcard Redis keys for stream %s with pattern %s: %s",
                    content_id,
                    pattern,
                    e,
                )

        if deleted_count > 0:
            logger.info(f"[TS:{content_id}] Flushed {deleted_count} Redis keys")

        return deleted_count
    
    def _cleanup_idle_sessions(self):
        """Clean up idle sessions with no clients"""
        for content_id in list(self.stream_managers.keys()):
            try:
                # Refresh ownership of the stream to prevent TTL expiry from suppressing shutdown triggers
                owner_key = RedisKeys.stream_owner(content_id)
                if self.redis_client:
                    self.redis_client.set(owner_key, self.worker_id, ex=300)
                
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
                                logger.info(f"[TS:{content_id}] Cleaning up idle stream (idle for {idle_time:.1f}s)")
                                self._stop_stream(content_id)
                        else:
                            # No clients EVER - check init_time
                            init_key = RedisKeys.stream_init_time(content_id)
                            init_time_raw = self.redis_client.get(init_key)
                            if init_time_raw:
                                init_time = float(init_time_raw.decode('utf-8'))
                                idle_since_init = time.time() - init_time
                                if idle_since_init > Config.CHANNEL_SHUTDOWN_DELAY:
                                    logger.info(f"[TS:{content_id}] Cleaning up orphan stream (never connected, initialized {idle_since_init:.1f}s ago)")
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
                logger.info(f"[TS:{content_id}] No clients left, scheduling stop")
                shutdown_timers = self._get_shutdown_timers()
                existing_timer = shutdown_timers.get(content_id)
                if existing_timer:
                    existing_timer.cancel()
                # Use threading.Timer instead of gevent.spawn_later
                timer = threading.Timer(Config.CHANNEL_SHUTDOWN_DELAY, self._stop_stream, args=[content_id])
                shutdown_timers[content_id] = timer
                timer.daemon = True
                timer.start()
