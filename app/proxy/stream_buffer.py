"""
Buffer management for AceStream streams.
Adapted from ts_proxy - removed Django dependencies, kept Redis/standard threading logic.
"""

import threading
import logging
import time
from collections import deque
from typing import Optional, Deque
import random

from .redis_keys import RedisKeys
from .config_helper import ConfigHelper, Config
from .constants import TS_PACKET_SIZE
from .utils import get_logger

logger = get_logger()


class StreamBuffer:
    """Manages stream data buffering with optimized Redis storage and TS packet alignment"""
    
    def __init__(self, content_id=None, redis_client=None):
        self.content_id = content_id
        self.redis_client = redis_client
        self.lock = threading.Lock()
        self.index = 0
        self.TS_PACKET_SIZE = TS_PACKET_SIZE
        
        # STANDARDIZED KEYS: Use RedisKeys class instead of hardcoded patterns
        self.buffer_index_key = RedisKeys.buffer_index(content_id) if content_id else ""
        self.buffer_prefix = RedisKeys.buffer_chunk_prefix(content_id) if content_id else ""
        
        self.chunk_ttl = ConfigHelper.redis_chunk_ttl()
        
        # Initialize from Redis if available
        if self.redis_client and content_id:
            try:
                current_index = self.redis_client.get(self.buffer_index_key)
                if current_index:
                    self.index = int(current_index)
                    logger.info(f"Initialized buffer from Redis with index {self.index}")
            except Exception as e:
                logger.error(f"Error initializing buffer from Redis: {e}")
        
        self._write_buffer = bytearray()
        self.target_chunk_size = Config.BUFFER_CHUNK_SIZE  # ~1MB default
        
        # Track timers for proper cleanup
        self.stopping = False
        self.fill_timers = []
        self.last_fetch_end_index = 0
        self.last_upstream_write_time = 0.0
        
        # REPLACED: gevent.event.Event with threading.Condition
        # Condition supports Wait/Notify semantics ideal for producer/consumer buffering
        self.chunk_available = threading.Condition()

        # Source rate tracking (chunks/second)
        self._source_rate_ema = None
        self._last_source_update_time = time.time()
    
    def add_chunk(self, chunk):
        """Add data with optimized Redis storage and TS packet alignment"""
        if not chunk:
            return False

        now = time.time()
        
        try:
            # LOCK PROTECTED: Prevent race conditions when appending to static buffers concurrently
            with self.lock:
                self.last_upstream_write_time = now
                # Accumulate partial packets between chunks
                if not hasattr(self, '_partial_packet'):
                    self._partial_packet = bytearray()
                
                # Combine with any previous partial packet
                combined_data = bytearray(self._partial_packet) + bytearray(chunk)
                
                # Calculate complete packets
                complete_packets_size = (len(combined_data) // self.TS_PACKET_SIZE) * self.TS_PACKET_SIZE
                
                if complete_packets_size == 0:
                    # Not enough data for a complete packet
                    self._partial_packet = combined_data
                    return True
                
                # Split into complete packets and remainder
                complete_packets = combined_data[:complete_packets_size]
                self._partial_packet = combined_data[complete_packets_size:]
                
                # Add completed packets to write buffer
                self._write_buffer.extend(complete_packets)
                
                # Only write to Redis when we have enough data for an optimized chunk
                writes_done = 0
                while len(self._write_buffer) >= self.target_chunk_size:
                    # Extract a full chunk
                    chunk_data = self._write_buffer[:self.target_chunk_size]
                    self._write_buffer = self._write_buffer[self.target_chunk_size:]
                    
                    # Write optimized chunk to Redis
                    if self.redis_client:
                        chunk_index = self.redis_client.incr(self.buffer_index_key)
                        chunk_key = RedisKeys.buffer_chunk(self.content_id, chunk_index)
                        self.redis_client.setex(chunk_key, self.chunk_ttl, bytes(chunk_data))
                        
                        # Update local tracking
                        self.index = chunk_index
                        writes_done += 1
            
            if writes_done > 0:
                self._update_source_rate(writes_done)
                logger.debug(f"Added {writes_done} chunks ({self.target_chunk_size} bytes each) to Redis for stream {self.content_id} at index {self.index}")
            
            # NOTIFICATION: Signal any sleeping waiters on Condition that buffer has advanced
            with self.chunk_available:
                self.chunk_available.notify_all()
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding chunk to buffer: {e}")
            return False

    def _update_source_rate(self, writes_done: int):
        """Update EMA of the source chunk arrival rate"""
        now = time.time()
        elapsed = now - self._last_source_update_time
        
        # Avoid division by zero or negative time
        if elapsed < 0.001:
            return

        self._last_source_update_time = now
        instant_rate = float(writes_done) / elapsed
        
        alpha = 0.1  # Slow EMA to filter out network/engine jitter
        if self._source_rate_ema is None:
            self._source_rate_ema = instant_rate
        else:
            self._source_rate_ema = (alpha * instant_rate) + ((1.0 - alpha) * self._source_rate_ema)

    def get_source_rate(self) -> float:
         """Return the current estimated source chunk rate (chunks/second)"""
         return float(self._source_rate_ema or 0.0)
    
    
    def _get_chunks_internal(self, start_index=None):
        """Get chunks and return per-call fetched end cursor.

        Returns:
            tuple[list[bytes], Optional[int]]: chunks and fetched end index for
            this specific call. The cursor must stay call-scoped to avoid
            cross-client races when many generators read concurrently.
        """
        try:
            request_id = f"req_{random.randint(1000, 9999)}"
            logger.debug(f"[{request_id}] get_chunks called with start_index={start_index}")
            
            if not self.redis_client:
                logger.error("Redis not available, cannot retrieve chunks")
                return [], None
            
            # If no start_index provided, use most recent chunks
            if start_index is None:
                start_index = max(0, self.index - 10)  # Start closer to current position
                logger.debug(f"[{request_id}] No start_index provided, using {start_index}")
            
            # Get current index from Redis
            current_index = int(self.redis_client.get(self.buffer_index_key) or 0)
            
            # Calculate range of chunks to retrieve
            start_id = start_index + 1
            chunks_behind = current_index - start_id
            
            # Adaptive chunk retrieval based on how far behind
            if chunks_behind > 100:
                fetch_count = 15
                logger.debug(f"[{request_id}] Client very behind ({chunks_behind} chunks), fetching {fetch_count}")
            elif chunks_behind > 50:
                fetch_count = 10
                logger.debug(f"[{request_id}] Client moderately behind ({chunks_behind} chunks), fetching {fetch_count}")
            elif chunks_behind > 20:
                fetch_count = 5
                logger.debug(f"[{request_id}] Client slightly behind ({chunks_behind} chunks), fetching {fetch_count}")
            else:
                fetch_count = 3
                logger.debug(f"[{request_id}] Client up-to-date (only {chunks_behind} chunks behind), fetching {fetch_count}")
            
            end_id = min(current_index + 1, start_id + fetch_count)
            
            if start_id >= end_id:
                logger.debug(f"[{request_id}] No new chunks to fetch (start_id={start_id}, end_id={end_id})")
                return [], None
            
            # Log the range we're retrieving
            logger.debug(f"[{request_id}] Retrieving chunks {start_id} to {end_id-1} (total: {end_id-start_id})")
            
            # Directly fetch from Redis using pipeline for efficiency
            pipe = self.redis_client.pipeline()
            for idx in range(start_id, end_id):
                chunk_key = RedisKeys.buffer_chunk(self.content_id, idx)
                pipe.get(chunk_key)
            
            results = pipe.execute()
            
            # Process results
            chunks = [result for result in results if result is not None]
            
            # Count non-None results
            found_chunks = len(chunks)
            missing_chunks = len(results) - found_chunks
            
            if missing_chunks > 0:
                logger.debug(f"[{request_id}] Missing {missing_chunks}/{len(results)} chunks in Redis")
            
            # Track the latest fetched range end so callers can advance
            # client position even when some chunk IDs are missing.
            fetched_end_index = max(0, end_id - 1)
            # Keep legacy shared cursor for backward compatibility.
            self.last_fetch_end_index = fetched_end_index
            
            # Final log message
            chunk_sizes = [len(c) for c in chunks]
            total_bytes = sum(chunk_sizes) if chunks else 0
            logger.debug(f"[{request_id}] Returning {len(chunks)} chunks ({total_bytes} bytes)")
            
            return chunks, fetched_end_index
            
        except Exception as e:
            logger.error(f"Error getting chunks from buffer: {e}", exc_info=True)
            return [], None

    def is_upstream_fresh(self, max_silence_seconds: float = 15.0) -> bool:
        """Return True when upstream has written data recently."""
        last_write = float(getattr(self, "last_upstream_write_time", 0.0) or 0.0)
        if last_write <= 0.0:
            return False
        silence_s = max(0.0, time.time() - last_write)
        return silence_s <= max(0.1, float(max_silence_seconds))

    def purge_stale_cache(self, reason: str = "stale_upstream") -> int:
        """Delete buffered Redis chunks and reset in-memory indices.

        Used by reconnect guard when a warm cache is present but upstream has
        been silent long enough that serving cached data would mask dead input.
        """
        deleted_keys = 0

        try:
            if self.redis_client and self.content_id:
                pattern = f"{self.buffer_prefix}*"
                if hasattr(self.redis_client, "scan_iter"):
                    batch = []
                    for key in self.redis_client.scan_iter(match=pattern, count=200):
                        batch.append(key)
                        if len(batch) >= 200:
                            deleted_keys += int(self.redis_client.delete(*batch) or 0)
                            batch = []
                    if batch:
                        deleted_keys += int(self.redis_client.delete(*batch) or 0)
                else:
                    current_index = int(self.redis_client.get(self.buffer_index_key) or 0)
                    for idx in range(1, current_index + 1):
                        chunk_key = RedisKeys.buffer_chunk(self.content_id, idx)
                        deleted_keys += int(self.redis_client.delete(chunk_key) or 0)

                self.redis_client.delete(self.buffer_index_key)

            with self.lock:
                self.index = 0
                self.last_fetch_end_index = 0
                self.last_upstream_write_time = 0.0
                self._write_buffer = bytearray()
                if hasattr(self, '_partial_packet'):
                    self._partial_packet = bytearray()

            logger.warning(
                "Purged stale stream cache for content_id=%s (reason=%s, deleted_keys=%s)",
                self.content_id,
                reason,
                deleted_keys,
            )
        except Exception as e:
            logger.error("Failed to purge stale stream cache for content_id=%s: %s", self.content_id, e)

        return deleted_keys

    def get_chunks_with_cursor(self, start_index=None):
        """Get chunks and a call-scoped fetched end cursor."""
        return self._get_chunks_internal(start_index)

    def get_chunks(self, start_index=None):
        """Backward-compatible chunk fetch API returning only data."""
        chunks, _ = self._get_chunks_internal(start_index)
        return chunks
    
    def get_chunks_exact(self, start_index, count):
        """Get exactly the requested number of chunks from given index"""
        try:
            if not self.redis_client:
                logger.error("Redis not available, cannot retrieve chunks")
                return []
            
            # Calculate range to retrieve
            start_id = start_index + 1
            end_id = start_id + count
            
            # Get current buffer position
            current_index = int(self.redis_client.get(self.buffer_index_key) or 0)
            
            # If requesting beyond current buffer, return what we have
            if start_id > current_index:
                return []
            
            # Cap end at current buffer position
            end_id = min(end_id, current_index + 1)
            
            # Directly fetch from Redis using pipeline
            pipe = self.redis_client.pipeline()
            for idx in range(start_id, end_id):
                chunk_key = RedisKeys.buffer_chunk(self.content_id, idx)
                pipe.get(chunk_key)
            
            results = pipe.execute()
            
            # Filter out None results
            chunks = [result for result in results if result is not None]
            
            # Update local index if needed
            if chunks and start_id + len(chunks) - 1 > self.index:
                self.index = start_id + len(chunks) - 1
            
            return chunks
            
        except Exception as e:
            logger.error(f"Error getting exact chunks: {e}", exc_info=True)
            return []
    
    def stop(self):
        """Stop the buffer and cancel all timers"""
        # Set stopping flag first to prevent new timer creation
        self.stopping = True
        
        # Cancel all pending timers
        timers_cancelled = 0
        for timer in list(self.fill_timers):
            try:
                # REPLACED: timer.dead / timer.kill with standard Timer cancellation
                if timer and timer.is_alive():
                    timer.cancel()
                    timers_cancelled += 1
            except Exception as e:
                logger.error(f"Error canceling timer: {e}")
        
        if timers_cancelled:
            logger.info(f"Cancelled {timers_cancelled} buffer timers for stream {self.content_id}")
        
        # Clear timer list
        self.fill_timers.clear()
        
        try:
            # Flush any remaining data in the write buffer
            if hasattr(self, '_write_buffer') and len(self._write_buffer) > 0:
                # Ensure remaining data is aligned to TS packets
                complete_size = (len(self._write_buffer) // 188) * 188
                
                if complete_size > 0:
                    final_chunk = self._write_buffer[:complete_size]
                    
                    # Write final chunk to Redis
                    with self.lock:
                        if self.redis_client:
                            try:
                                chunk_index = self.redis_client.incr(self.buffer_index_key)
                                chunk_key = f"{self.buffer_prefix}{chunk_index}"
                                self.redis_client.setex(chunk_key, self.chunk_ttl, bytes(final_chunk))
                                self.index = chunk_index
                                logger.info(f"Flushed final chunk of {len(final_chunk)} bytes to Redis")
                            except Exception as e:
                                logger.error(f"Error flushing final chunk: {e}")
                
                # Clear buffers
                self._write_buffer = bytearray()
                with self.lock:
                    if hasattr(self, '_partial_packet'):
                        self._partial_packet = bytearray()
                    
        except Exception as e:
            logger.error(f"Error during buffer stop: {e}")
    
    def get_optimized_client_data(self, client_index):
        """Get optimal amount of data for client streaming based on position and target size"""
        # Define limits
        MIN_CHUNKS = 3
        MAX_CHUNKS = 20
        TARGET_SIZE = 1024 * 1024  # Target ~1MB per response
        MAX_SIZE = 2 * 1024 * 1024  # Hard cap at 2MB
        
        # Calculate how far behind we are
        chunks_behind = self.index - client_index
        
        # Determine optimal chunk count
        if chunks_behind <= MIN_CHUNKS:
            chunk_count = max(1, chunks_behind)
        elif chunks_behind <= MAX_CHUNKS:
            chunk_count = chunks_behind
        else:
            chunk_count = MAX_CHUNKS
        
        # Retrieve chunks
        chunks = self.get_chunks_exact(client_index, chunk_count)
        
        # Check if we got significantly fewer chunks than expected
        if chunk_count > 3 and len(chunks) == 0 and chunks_behind > 10:
            logger.debug(f"Chunks missing for client at index {client_index}, buffer at {self.index} ({chunks_behind} behind)")
            return [], client_index
        
        # Check total size
        total_size = sum(len(c) for c in chunks)
        
        # If we're under target and have more chunks available, get more
        if total_size < TARGET_SIZE and chunks_behind > chunk_count:
            additional = min(MAX_CHUNKS - chunk_count, chunks_behind - chunk_count)
            more_chunks = self.get_chunks_exact(client_index + chunk_count, additional)
            
            additional_size = sum(len(c) for c in more_chunks)
            if total_size + additional_size <= MAX_SIZE:
                chunks.extend(more_chunks)
                chunk_count += len(more_chunks)
        
        return chunks, client_index + chunk_count
    
    def schedule_timer(self, delay, callback, *args, **kwargs):
        """Schedule a timer and track it for proper cleanup"""
        if self.stopping:
            return None
        
        # REPLACED: gevent.spawn_later with threading.Timer (daemonized)
        timer = threading.Timer(delay, callback, args=args, kwargs=kwargs)
        timer.daemon = True # Prevent blocking application teardown on exit
        self.fill_timers.append(timer)
        timer.start()
        return timer
