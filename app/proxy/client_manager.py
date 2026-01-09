"""
Client connection management for AceStream streams.
Adapted from ts_proxy - removed Django dependencies, kept heartbeat and Redis logic.
"""

import threading
import logging
import time
import json
import gevent
from typing import Set, Optional
from redis.exceptions import ConnectionError, TimeoutError

from .constants import EventType, StreamState, ClientMetadataField
from .config_helper import ConfigHelper, Config
from .redis_keys import RedisKeys
from .utils import get_logger

logger = get_logger()


class ClientManager:
    """Manages client connections with no duplicates"""
    
    def __init__(self, content_id=None, redis_client=None, heartbeat_interval=1, worker_id=None):
        self.content_id = content_id
        self.redis_client = redis_client
        self.clients = set()
        self.lock = threading.Lock()
        self.last_active_time = time.time()
        self.worker_id = worker_id  # Store worker ID as instance variable
        self._heartbeat_running = True  # Flag to control heartbeat thread
        
        # STANDARDIZED KEYS: Move client set under stream namespace
        self.client_set_key = RedisKeys.clients(content_id)
        self.client_ttl = Config.CLIENT_RECORD_TTL
        self.heartbeat_interval = Config.CLIENT_HEARTBEAT_INTERVAL
        self.last_heartbeat_time = {}
        
        # Get ProxyServer instance for ownership checks
        from .server import ProxyServer
        self.proxy_server = ProxyServer.get_instance()
        
        # Start heartbeat thread for local clients
        self._start_heartbeat_thread()
        self._registered_clients = set()  # Track already registered client IDs
    
    def _start_heartbeat_thread(self):
        """Start thread to regularly refresh client presence in Redis for local clients"""
        def heartbeat_task():
            logger.debug(f"Started heartbeat thread for stream {self.content_id} (interval: {self.heartbeat_interval}s)")
            
            while self._heartbeat_running:
                try:
                    # Wait for the interval, but check stop flag frequently for quick shutdown
                    for _ in range(int(self.heartbeat_interval)):
                        if not self._heartbeat_running:
                            break
                        time.sleep(1)
                    
                    # Final check before doing work
                    if not self._heartbeat_running:
                        break
                    
                    # Send heartbeat for all local clients
                    with self.lock:
                        # Skip this cycle if we have no local clients
                        if not self.clients:
                            continue
                        
                        # IMPROVED GHOST DETECTION: Check for stale clients before sending heartbeats
                        current_time = time.time()
                        clients_to_remove = set()
                        
                        # First identify clients that should be removed
                        for client_id in self.clients:
                            client_key = RedisKeys.client_metadata(self.content_id, client_id)
                            
                            # Check if client exists in Redis at all
                            exists = self.redis_client.exists(client_key)
                            if not exists:
                                logger.debug(f"Client {client_id} no longer exists in Redis, removing locally")
                                clients_to_remove.add(client_id)
                                continue
                            
                            # Check for stale activity using last_active field
                            last_active = self.redis_client.hget(client_key, "last_active")
                            if last_active:
                                last_active_time = float(last_active.decode('utf-8'))
                                ghost_timeout = self.heartbeat_interval * Config.GHOST_CLIENT_MULTIPLIER
                                
                                if current_time - last_active_time > ghost_timeout:
                                    logger.debug(f"Client {client_id} inactive for {current_time - last_active_time:.1f}s, removing as ghost")
                                    clients_to_remove.add(client_id)
                        
                        # Remove ghost clients in a separate step
                        for client_id in clients_to_remove:
                            self.remove_client(client_id)
                        
                        if clients_to_remove:
                            logger.info(f"Removed {len(clients_to_remove)} ghost clients from stream {self.content_id}")
                        
                        # Now send heartbeats only for remaining clients
                        pipe = self.redis_client.pipeline()
                        current_time = time.time()
                        
                        for client_id in self.clients:
                            # Skip clients we just marked for removal
                            if client_id in clients_to_remove:
                                continue
                            
                            # Skip if we just sent a heartbeat recently
                            if client_id in self.last_heartbeat_time:
                                time_since_heartbeat = current_time - self.last_heartbeat_time[client_id]
                                if time_since_heartbeat < self.heartbeat_interval * 0.5:
                                    continue
                            
                            # Only update clients that remain
                            client_key = RedisKeys.client_metadata(self.content_id, client_id)
                            pipe.hset(client_key, "last_active", str(current_time))
                            pipe.expire(client_key, self.client_ttl)
                            
                            # Keep client in the set with TTL
                            pipe.sadd(self.client_set_key, client_id)
                            pipe.expire(self.client_set_key, self.client_ttl)
                            
                            # Track last heartbeat locally
                            self.last_heartbeat_time[client_id] = current_time
                        
                        # Execute all commands atomically
                        pipe.execute()
                        
                        # Only notify if we have real clients
                        if self.clients and not all(c in clients_to_remove for c in self.clients):
                            self._notify_owner_of_activity()
                
                except Exception as e:
                    logger.error(f"Error in client heartbeat thread: {e}")
            
            logger.debug(f"Heartbeat thread exiting for stream {self.content_id}")
        
        thread = threading.Thread(target=heartbeat_task, daemon=True)
        thread.name = f"client-heartbeat-{self.content_id}"
        thread.start()
        logger.debug(f"Started client heartbeat thread for stream {self.content_id} (interval: {self.heartbeat_interval}s)")
    
    def stop(self):
        """Stop the heartbeat thread and cleanup"""
        logger.debug(f"Stopping ClientManager for stream {self.content_id}")
        self._heartbeat_running = False
    
    def _execute_redis_command(self, command_func):
        """Execute Redis command with error handling"""
        if not self.redis_client:
            return None
        
        try:
            return command_func()
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"Redis connection error in ClientManager: {e}")
            return None
        except Exception as e:
            logger.error(f"Redis command error in ClientManager: {e}")
            return None
    
    def _notify_owner_of_activity(self):
        """Notify stream owner that clients are active on this worker"""
        if not self.redis_client or not self.clients:
            return
        
        try:
            worker_id = self.worker_id or "unknown"
            
            # Worker info under stream namespace
            worker_key = f"ace_proxy:stream:{self.content_id}:worker:{worker_id}"
            self._execute_redis_command(
                lambda: self.redis_client.setex(worker_key, self.client_ttl, str(len(self.clients)))
            )
            
            # Activity timestamp under stream namespace
            activity_key = f"ace_proxy:stream:{self.content_id}:activity"
            self._execute_redis_command(
                lambda: self.redis_client.setex(activity_key, self.client_ttl, str(time.time()))
            )
        except Exception as e:
            logger.error(f"Error notifying owner of client activity: {e}")
    
    def add_client(self, client_id, client_ip, user_agent=None):
        """Add a client with duplicate prevention"""
        if client_id in self._registered_clients:
            logger.debug(f"Client {client_id} already registered, skipping")
            return False
        
        self._registered_clients.add(client_id)
        
        # Use RedisKeys for client key
        client_key = RedisKeys.client_metadata(self.content_id, client_id)
        
        # Prepare client data
        current_time = str(time.time())
        client_data = {
            "user_agent": user_agent or "unknown",
            "ip_address": client_ip,
            "connected_at": current_time,
            "last_active": current_time,
            "worker_id": self.worker_id or "unknown"
        }
        
        try:
            with self.lock:
                # Store client in local set
                self.clients.add(client_id)
                
                # Store in Redis
                if self.redis_client:
                    self.redis_client.hset(client_key, mapping=client_data)
                    self.redis_client.expire(client_key, self.client_ttl)
                    
                    # Add to the client set
                    self.redis_client.sadd(self.client_set_key, client_id)
                    self.redis_client.expire(self.client_set_key, self.client_ttl)
                    
                    # Clear any initialization timer
                    init_key = f"ace_proxy:stream:{self.content_id}:init_time"
                    self.redis_client.delete(init_key)
                    
                    self._notify_owner_of_activity()
                    
                    # Publish client connected event
                    event_data = {
                        "event": EventType.CLIENT_CONNECTED,
                        "content_id": self.content_id,
                        "client_id": client_id,
                        "worker_id": self.worker_id or "unknown",
                        "timestamp": time.time()
                    }
                    
                    if user_agent:
                        event_data["user_agent"] = user_agent
                        logger.debug(f"Storing user agent '{user_agent}' for client {client_id}")
                    
                    self.redis_client.publish(
                        RedisKeys.events_channel(self.content_id),
                        json.dumps(event_data)
                    )
                
                # Get total clients across all workers
                total_clients = self.get_total_client_count()
                logger.info(f"New client connected: {client_id} (local: {len(self.clients)}, total: {total_clients})")
                
                self.last_heartbeat_time[client_id] = time.time()
                
                return len(self.clients)
                
        except Exception as e:
            logger.error(f"Error adding client {client_id}: {e}")
            return False
    
    def remove_client(self, client_id):
        """Remove a client from this stream and Redis"""
        client_ip = None
        
        with self.lock:
            if client_id in self.clients:
                self.clients.remove(client_id)
            
            if client_id in self.last_heartbeat_time:
                del self.last_heartbeat_time[client_id]
            
            self.last_active_time = time.time()
            
            if self.redis_client:
                # Get client IP before removing the data
                client_key = RedisKeys.client_metadata(self.content_id, client_id)
                client_data = self.redis_client.hgetall(client_key)
                if client_data and b'ip_address' in client_data:
                    client_ip = client_data[b'ip_address'].decode('utf-8')
                elif client_data and 'ip_address' in client_data:
                    client_ip = client_data['ip_address']
                
                # Remove from stream's client set
                self.redis_client.srem(self.client_set_key, client_id)
                
                # Delete individual client key
                self.redis_client.delete(client_key)
                
                # Check if this was the last client
                remaining = self.redis_client.scard(self.client_set_key) or 0
                if remaining == 0:
                    logger.warning(f"Last client removed: {client_id} - stream may shut down soon")
                    
                    # Trigger disconnect time tracking
                    disconnect_key = RedisKeys.last_client_disconnect(self.content_id)
                    self.redis_client.setex(disconnect_key, 60, str(time.time()))
                
                self._notify_owner_of_activity()
                
                # Check if we're the owner
                am_i_owner = self.proxy_server and self.proxy_server.am_i_owner(self.content_id)
                
                if am_i_owner:
                    # We're the owner - handle the disconnect directly
                    logger.debug(f"Owner handling CLIENT_DISCONNECTED for client {client_id} locally")
                    if remaining == 0:
                        logger.debug(f"No clients left - triggering immediate shutdown check")
                        gevent.spawn(self.proxy_server.handle_client_disconnect, self.content_id)
                else:
                    # We're not the owner - publish event
                    logger.debug(f"Non-owner publishing CLIENT_DISCONNECTED event for client {client_id}")
                    event_data = json.dumps({
                        "event": EventType.CLIENT_DISCONNECTED,
                        "content_id": self.content_id,
                        "client_id": client_id,
                        "worker_id": self.worker_id or "unknown",
                        "timestamp": time.time(),
                        "remaining_clients": remaining
                    })
                    self.redis_client.publish(RedisKeys.events_channel(self.content_id), event_data)
            
            total_clients = self.get_total_client_count()
            logger.info(f"Client disconnected: {client_id} (local: {len(self.clients)}, total: {total_clients})")
        
        return len(self.clients)
    
    def get_client_count(self):
        """Get local client count"""
        with self.lock:
            return len(self.clients)
    
    def get_total_client_count(self):
        """Get total client count across all workers"""
        if not self.redis_client:
            return len(self.clients)
        
        try:
            # Count members in the client set
            return self.redis_client.scard(self.client_set_key) or 0
        except Exception as e:
            logger.error(f"Error getting total client count: {e}")
            return len(self.clients)  # Fall back to local count
    
    def update_client_bytes_sent(self, client_id, bytes_sent):
        """Update bytes_sent metric for a specific client in Redis"""
        if not self.redis_client:
            return
        
        try:
            client_key = RedisKeys.client_metadata(self.content_id, client_id)
            self.redis_client.hset(client_key, "bytes_sent", str(bytes_sent))
            self.redis_client.hset(client_key, "stats_updated_at", str(time.time()))
        except Exception as e:
            logger.error(f"Error updating client bytes_sent: {e}")
    
    def refresh_client_ttl(self):
        """Refresh TTL for active clients to prevent expiration"""
        if not self.redis_client:
            return
        
        try:
            # Refresh TTL for all clients belonging to this worker
            for client_id in self.clients:
                client_key = RedisKeys.client_metadata(self.content_id, client_id)
                self.redis_client.expire(client_key, self.client_ttl)
            
            # Refresh TTL on the set itself
            self.redis_client.expire(self.client_set_key, self.client_ttl)
        except Exception as e:
            logger.error(f"Error refreshing client TTL: {e}")
