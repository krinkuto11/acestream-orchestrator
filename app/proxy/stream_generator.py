"""
Stream generation and client-side handling for AceStream streams.
Simplified adaptation from ts_proxy - handles per-client delivery and buffering.

IMPORTANT: This module is used in a threading environment (uvicorn default mode),
NOT gevent. All sleep operations must use time.sleep(), not gevent.sleep().
"""

import time
import logging
import math

from .config_helper import ConfigHelper
from .constants import PROXY_MODE_API, normalize_proxy_mode
from .utils import get_logger, create_ts_packet
from .redis_keys import RedisKeys
from .constants import StreamMetadataField, NULL_PID_HIGH, NULL_PID_LOW

logger = get_logger()

# Keep client-side download burst small so runway telemetry reflects
# proxy-held buffer rather than player-local RAM hoarding.
MAX_CLIENT_LOCAL_HOARD_SECONDS = 0.0
MAX_CATCHUP_SPEED_MULTIPLIER = 2.0


class StreamGenerator:
    """Handles generating streams for clients"""
    
    def __init__(self, content_id, client_id, client_ip, client_user_agent, stream_initializing=False, seekback=None):
        self.content_id = content_id
        self.client_id = client_id
        self.client_ip = client_ip
        self.client_user_agent = client_user_agent
        self.stream_initializing = stream_initializing
        self.seekback = seekback
        
        # Performance tracking
        self.stream_start_time = time.time()
        self.bytes_sent = 0
        self.chunks_sent = 0
        self.local_index = 0
        self.consecutive_empty = 0
        
        # Rate tracking
        self.last_stats_time = time.time()
        self.last_stats_bytes = 0
        self.last_stats_chunks = 0
        self.current_rate = 0.0
        
        # TTL refresh
        self.last_ttl_refresh = time.time()
        self.ttl_refresh_interval = 3

        # Debounced client buffer-position tracking
        self.last_position_update_time = 0.0
        self.position_update_interval = 2.5

        # Runtime chunk-rate estimate (chunks/sec) used for lag-to-seconds conversion.
        self.chunk_rate_ema = None
        self.last_chunk_rate_update_time = time.time()
        self.last_chunk_sent_time = time.time()
        self.last_starvation_update_time = 0.0

        # Virtual Runway tracking
        self.pacing_start_time = None
        self.pacing_burst_chunks = 0  # Disabled: maintain 100% of buffer in proxy for telemetry precision
    
    def generate(self):
        """Generator function that produces stream content for the client"""
        # Local import avoids creating import cycles at module import time.
        from ..services.metrics import observe_proxy_egress_bytes

        self.stream_start_time = time.time()
        self.bytes_sent = 0
        self.chunks_sent = 0
        self.last_chunk_sent_time = time.time()
        self.last_starvation_update_time = 0.0
        
        try:
            logger.info(f"[{self.client_id}] Stream generator started, stream_ready={not self.stream_initializing}")
            
            # If stream is initializing, wait for it
            if self.stream_initializing:
                stream_ready = self._wait_for_initialization()
                if not stream_ready:
                    return
            
            logger.info(f"[{self.client_id}] Stream ready, starting normal streaming")
            
            # Reset start time for real streaming
            self.stream_start_time = time.time()
            
            # Setup streaming
            if not self._setup_streaming():
                return

            self.pacing_start_time = time.time() # START PACING CLOCK HERE
            
            # Main streaming loop
            # Get no data timeout settings
            no_data_max_checks = ConfigHelper.no_data_timeout_checks()
            no_data_check_interval = ConfigHelper.no_data_check_interval()
            
            while True:
                # Get chunks from buffer
                fetched_end_index = None
                if hasattr(self.buffer, "get_chunks_with_cursor"):
                    chunks, fetched_end_index = self.buffer.get_chunks_with_cursor(self.local_index)
                else:
                    chunks = self.buffer.get_chunks(self.local_index)
                
                if chunks:
                    for chunk in chunks:
                        yield chunk
                        chunk_len = len(chunk)
                        self.bytes_sent += chunk_len
                        observe_proxy_egress_bytes("TS", chunk_len)
                        self.chunks_sent += 1
                        self.last_chunk_sent_time = time.time()
                        yield from self._maybe_apply_client_pacing()
                    
                    # Update local index
                    self._advance_local_index(len(chunks), fetched_end_index=fetched_end_index)
                    self.consecutive_empty = 0
                    self._update_chunk_rate_estimate(len(chunks))
                    self._maybe_update_client_position(source="ts_cursor_ema")
                    
                    # Update stats periodically
                    if time.time() - self.last_stats_time >= 5:
                        self._update_stats()
                    
                else:
                    # No data available
                    self.consecutive_empty += 1

                    # Keep runway telemetry fresh during starvation so failover
                    # logic does not rely on stale runway samples. Debounce
                    # forced updates to avoid spamming Redis under short poll
                    # intervals while still reporting liveness/runway decay.
                    now = time.time()
                    starvation_update_interval = max(2.5, float(self.position_update_interval or 2.5))
                    if (now - float(self.last_starvation_update_time or 0.0)) >= starvation_update_interval:
                        self._maybe_update_client_position(force=True, source="starvation_tick")
                        self.last_starvation_update_time = now
                    
                    # Check if stream has ended (no data for too long)
                    if self.consecutive_empty > no_data_max_checks:
                        timeout_seconds = no_data_max_checks * no_data_check_interval
                        logger.info(f"[{self.client_id}] Stream ended (no data for {timeout_seconds:.1f}s)")
                        break
                    
                    # Wait a bit before retrying
                    # IMPORTANT: Use time.sleep() NOT gevent.sleep() - we're in threading mode
                    time.sleep(no_data_check_interval)
                
                # Refresh client TTL periodically
                if time.time() - self.last_ttl_refresh >= self.ttl_refresh_interval:
                    self._refresh_ttl()
            
            logger.info(f"[{self.client_id}] Stream completed: {self.bytes_sent} bytes, {self.chunks_sent} chunks")
            
        except GeneratorExit:
            logger.info(f"[{self.client_id}] Client disconnected")
        except Exception as e:
            logger.error(f"[{self.client_id}] Stream error: {e}", exc_info=True)
        finally:
            self._cleanup()
    
    def _wait_for_initialization(self):
        """Wait for stream to initialize"""
        from .server import ProxyServer

        timeout = ConfigHelper.channel_init_grace_period()
        start_time = time.time()
        check_interval = 0.2
        proxy_server = ProxyServer.get_instance()
        
        logger.info(f"[{self.client_id}] Waiting for stream initialization (timeout: {timeout}s)")
        
        while time.time() - start_time < timeout:
            manager = proxy_server.stream_managers.get(self.content_id)
            if manager is None:
                logger.error(f"[{self.client_id}] Stream manager missing during initialization")
                return False

            manager_mode = normalize_proxy_mode(getattr(manager, "control_mode", None))
            # Non-API modes do not run preflight gating, so do not block startup here.
            if manager_mode != PROXY_MODE_API:
                return True

            # Stream manager must complete session request before clients start normal streaming.
            if bool(getattr(manager, "connected", False)) and bool(getattr(manager, "playback_url", None)):
                return True

            # IMPORTANT: Use time.sleep() NOT gevent.sleep() - we're in threading mode
            time.sleep(check_interval)
        
        logger.error(f"[{self.client_id}] Stream initialization timeout")
        return False
    
    def _wait_for_initial_data(self, min_index=None):
        """Wait for initial data to arrive in the buffer before starting streaming.
        
        This is critical because the HTTP streamer needs time to connect to the
        playback URL and fetch the first chunks. Without this wait, clients will
        see an empty buffer and disconnect prematurely.
        """
        prebuffer_seconds = max(0.0, float(ConfigHelper.proxy_prebuffer_seconds()))
        timeout = max(float(ConfigHelper.initial_data_wait_timeout()), prebuffer_seconds + 15.0)
        check_interval = ConfigHelper.initial_data_check_interval()
        start_time = time.time()
        baseline_index = max(0, int(min_index or 0))

        # Hot reconnect fast-path: if the stream already has enough buffered chunks
        # for the prebuffer target, skip the startup holdback loop.
        initial_fresh_chunks = max(0, int(self.buffer.index) - baseline_index)
        if prebuffer_seconds > 0.0:
            chunk_rate = max(0.1, float(self.chunk_rate_ema or 1.0))
            required_prebuffer_chunks = max(1, int(math.ceil(prebuffer_seconds * chunk_rate)))
            if initial_fresh_chunks >= required_prebuffer_chunks:
                if getattr(self.buffer, "is_upstream_fresh", None) and self.buffer.is_upstream_fresh(15.0):
                    self.chunk_rate_ema = max(chunk_rate, float(self.chunk_rate_ema or 0.0))
                    logger.info(
                        f"[{self.client_id}] Hot stream detected: Bypassing prebuffer wait "
                        f"(buffer index: {self.buffer.index}, baseline_index: {baseline_index}, "
                        f"fresh_chunks: {initial_fresh_chunks}, required_chunks: {required_prebuffer_chunks}, "
                        f"chunk_rate_ema: {self.chunk_rate_ema:.2f} chunks/s)"
                    )
                    return True

                logger.warning(
                    f"[{self.client_id}] Warm cache detected but upstream is stale; forcing cache purge "
                    f"(buffer index: {self.buffer.index}, baseline_index: {baseline_index}, "
                    f"fresh_chunks: {initial_fresh_chunks}, required_chunks: {required_prebuffer_chunks})"
                )
                if getattr(self.buffer, "purge_stale_cache", None):
                    self.buffer.purge_stale_cache(reason="hot_reconnect_stale_upstream")
                baseline_index = max(0, int(self.buffer.index))
        
        logger.info(
            f"[{self.client_id}] Waiting for initial data in buffer "
            f"(timeout: {timeout:.1f}s, prebuffer_seconds: {prebuffer_seconds:.1f}, "
            f"baseline_index: {baseline_index})..."
        )
        
        while time.time() - start_time < timeout:
            elapsed = time.time() - start_time
            fresh_chunks = max(0, int(self.buffer.index) - baseline_index)

            # Prebuffer semantics for TS are time-based: keep a startup holdback
            # for N seconds, then start from the original baseline index.
            if prebuffer_seconds > 0.0:
                if fresh_chunks > 0 and elapsed >= prebuffer_seconds:
                    self.chunk_rate_ema = max(0.1, float(fresh_chunks) / max(elapsed, 0.001))
                    logger.info(
                        f"[{self.client_id}] Initial prebuffer ready after {elapsed:.2f}s "
                        f"(buffer index: {self.buffer.index}, fresh_chunks: {fresh_chunks}, "
                        f"chunk_rate_ema: {self.chunk_rate_ema:.2f} chunks/s)"
                    )
                    return True
            elif fresh_chunks > 0:
                elapsed = time.time() - start_time
                logger.info(f"[{self.client_id}] Initial data available after {elapsed:.2f}s (buffer index: {self.buffer.index})")
                return True
            
            # Wait before checking again
            # IMPORTANT: Use time.sleep() NOT gevent.sleep() - we're in threading mode
            time.sleep(check_interval)
        
        # Timeout - no data arrived
        logger.error(
            f"[{self.client_id}] Timeout waiting for initial data "
            f"(buffer index: {self.buffer.index}, baseline_index: {baseline_index}, "
            f"prebuffer_seconds: {prebuffer_seconds:.1f}, waited: {timeout:.1f}s)"
        )
        return False

    def _update_chunk_rate_estimate(self, chunks_received: int):
        """Maintain an EMA of producer/consumer chunk rate for lag conversion."""
        # FAVOR SOURCE RATE: if the buffer can provide a stable ingress rate from
        # the engine, use it to avoid egress speed contaminating the bitrate estimate.
        if hasattr(self, "buffer") and self.buffer:
            source_rate = getattr(self.buffer, "get_source_rate", lambda: 0.0)()
            if source_rate > 0.1:
                if self.chunk_rate_ema is None:
                    self.chunk_rate_ema = source_rate
                else:
                    # Faster adaptation for source rate changes
                    alpha = 0.2
                    self.chunk_rate_ema = (alpha * source_rate) + ((1.0 - alpha) * float(self.chunk_rate_ema))
                return

        now = time.time()
        elapsed = now - self.last_chunk_rate_update_time
        self.last_chunk_rate_update_time = now

        if chunks_received <= 0 or elapsed <= 0:
            return

        instant_rate = float(chunks_received) / float(elapsed)
        if self.chunk_rate_ema is None:
            self.chunk_rate_ema = instant_rate
            return

        alpha = 0.2
        self.chunk_rate_ema = (alpha * instant_rate) + ((1.0 - alpha) * float(self.chunk_rate_ema))

    def _maybe_apply_client_pacing(self):
        """Strictly pace the client to the engine's download speed (Leaky Bucket)
        
        This method is a generator that yields MPEG-TS Null packets as keep-alives 
        while waiting, preventing TCP/HTTP timeouts on aggressive load balancers.
        """
        # Local import to avoid cycle if needed
        from ..services.metrics import observe_proxy_egress_bytes

        if not self.pacing_start_time:
            return

        # Use the engine's ingress rate, fallback to our egress EMA if missing
        source_rate = getattr(self.buffer, "get_source_rate", lambda: 0.0)()
        if source_rate <= 0.1:
            source_rate = float(self.chunk_rate_ema or 1.0)
            
        elapsed = time.time() - self.pacing_start_time
        expected_chunks = elapsed * source_rate
        
        # Strict pacing: no client-side burst allowed. Maintains 100% of buffer in proxy
        # to ensure that failover runway telemetry remains highly accurate.
        if self.chunks_sent > expected_chunks + self.pacing_burst_chunks:
            # Calculate time to sleep to fall back in line with the expected rate
            wait_time = (self.chunks_sent - self.pacing_burst_chunks) / source_rate - elapsed
            
            while wait_time > 0.01:
                # Yield a Null packet to keep the connection active
                # Using standard Null PID (0x1FFF = 8191)
                yield create_ts_packet(pid_high=NULL_PID_HIGH, pid_low=NULL_PID_LOW)
                
                self.bytes_sent += 188
                observe_proxy_egress_bytes("TS", 188)
                
                # Sleep in small pulses to maintain responsiveness and timing
                pulse = min(wait_time, 0.5)
                time.sleep(pulse)
                
                # Update remaining wait time
                elapsed = time.time() - self.pacing_start_time
                wait_time = (self.chunks_sent - self.pacing_burst_chunks) / source_rate - elapsed

    def _advance_local_index(self, chunks_received: int, fetched_end_index=None):
        """Advance client position by fetched range when available.

        Redis chunk TTL expirations can create sparse ranges (missing chunk IDs).
        Advancing only by returned chunk count can pin local_index near the
        initial baseline and make lag appear to increase forever.
        """
        fetched_end = fetched_end_index
        if fetched_end is None:
            fetched_end = getattr(self.buffer, "last_fetch_end_index", None)
        if isinstance(fetched_end, int) and fetched_end >= self.local_index:
            self.local_index = int(fetched_end)
            return
        self.local_index += int(max(0, chunks_received))

    def _maybe_update_client_position(self, force: bool = False, source: str = "ts_cursor_ema"):
        """Publish client runway estimate using simple chunk counting."""
        if not hasattr(self, 'client_manager') or self.client_manager is None:
            return

        now = time.time()
        if not force and (now - self.last_position_update_time) < self.position_update_interval:
            return

        try:
            # Simple math: Since we pace the client, the buffer remains in Redis.
            chunks_behind = max(0, int(self.buffer.index) - int(self.local_index))
            chunk_rate = float(self.chunk_rate_ema or 1.0)
            seconds_behind = max(0.0, float(chunks_behind) / chunk_rate)

            normalized_source = str(source or "ts_cursor_ema")
            confidence = 0.75 if self.chunk_rate_ema else 0.55

            if normalized_source in {"ts_starvation_decay", "starvation_tick"}:
                if self.chunks_sent > 0:
                    elapsed_since_chunk = max(0.0, now - float(self.last_chunk_sent_time or now))
                    seconds_behind = max(0.0, seconds_behind - elapsed_since_chunk)
                    confidence = 0.45
                else:
                    confidence = 0.55

            self.client_manager.update_client_position(
                self.client_id,
                seconds_behind,
                source=normalized_source,
                confidence=confidence,
                observed_at=now,
            )
            self.last_position_update_time = now
        except Exception as e:
            logger.debug(f"[{self.client_id}] Failed to update client position: {e}")
    
    def _setup_streaming(self):
        """Setup streaming parameters"""
        from .server import ProxyServer
        
        proxy_server = ProxyServer.get_instance()
        
        # Get stream buffer
        self.buffer = proxy_server.stream_buffers.get(self.content_id)
        if not self.buffer:
            logger.error(f"[{self.client_id}] No buffer found for content_id={self.content_id}")
            return False
        
        # Get client manager
        self.client_manager = proxy_server.client_managers.get(self.content_id)
        if not self.client_manager:
            logger.error(f"[{self.client_id}] No client manager found")
            return False
        
        # Capture the current index before registering the client so we can
        # track the client's absolute position in the buffer.
        start_index = self.buffer.index

        # Reuse the existing baseline for deterministic hot reconnects.
        # This preserves prior runway context instead of forcing a cold start.
        reconnect_start_index = None
        try:
            redis_client = getattr(self.client_manager, "redis_client", None)
            if redis_client:
                client_key = RedisKeys.client_metadata(self.content_id, self.client_id)
                previous_initial_index_raw = redis_client.hget(client_key, "initial_index")
                if previous_initial_index_raw is not None:
                    previous_initial_index = int(
                        previous_initial_index_raw.decode("utf-8")
                        if isinstance(previous_initial_index_raw, bytes)
                        else previous_initial_index_raw
                    )
                    if 0 <= previous_initial_index <= int(start_index):
                        reconnect_start_index = previous_initial_index
        except Exception:
            reconnect_start_index = None

        if reconnect_start_index is not None:
            start_index = reconnect_start_index
            logger.info(
                f"[{self.client_id}] Reusing previous baseline index {start_index} for hot reconnect "
                f"(buffer index: {self.buffer.index})"
            )
        
        # Add client with starting position
        self.client_manager.add_client(self.client_id, self.client_ip, self.client_user_agent, initial_index=start_index)

        # Register an initial TS client row immediately so lag updates do not wait
        # for the first periodic stats flush.
        from ..services.client_tracker import client_tracking_service

        client_tracking_service.register_client(
            client_id=str(self.client_id),
            stream_id=str(self.content_id),
            ip_address=str(self.client_ip or "unknown"),
            user_agent=str(self.client_user_agent or "unknown"),
            protocol="TS",
            connected_at=time.time(),
            worker_id="ts_proxy",
        )

        # For first joins, only accept chunks produced after registration.
        # For deterministic reconnects, keep the recovered baseline so already
        # buffered data can satisfy startup prebuffer immediately.
        if reconnect_start_index is None:
            start_index = self.buffer.index
        
        # Wait for initial data in buffer before starting streaming
        # This gives the HTTP streamer time to fetch data from the playback URL
        if not self._wait_for_initial_data(min_index=start_index):
            # Error already logged in _wait_for_initial_data
            return False
        
        # Keep playback behind live edge when unified prebuffer is configured.
        # ROBUST FIX: If seekback is exactly 0, the user wants 'normal play' (liveness).
        # In this case, we bypass the prebuffer offset and start from the current edge.
        prebuffer_seconds = max(0.0, float(ConfigHelper.proxy_prebuffer_seconds()))
        requested_seekback = 0
        try:
            if self.seekback is not None:
                requested_seekback = int(float(self.seekback))
        except (ValueError, TypeError):
            requested_seekback = 0

        if prebuffer_seconds > 0.0 and requested_seekback > 0:
            self.local_index = max(0, int(start_index))
        else:
            if requested_seekback == 0:
                logger.info(f"[{self.client_id}] Seekback is 0 (normal play): starting from current live edge index {self.buffer.index}")
            self.local_index = self.buffer.index

        # Starting playback after prebuffer should reset starvation drain anchor.
        self.last_chunk_sent_time = time.time()

        # Publish an initial runway sample right after startup completes.
        self._maybe_update_client_position(force=True, source="ts_startup")
        
        logger.info(f"[{self.client_id}] Starting from buffer index {self.local_index}")
        return True
    
    def _update_stats(self):
        """Update streaming statistics and flush aggregated tracker deltas."""
        from ..services.client_tracker import client_tracking_service

        now = time.time()
        elapsed = now - self.last_stats_time
        
        if elapsed > 0:
            bytes_since_last = self.bytes_sent - self.last_stats_bytes
            chunks_since_last = self.chunks_sent - self.last_stats_chunks

            if bytes_since_last > 0 or chunks_since_last > 0:
                client_tracking_service.record_activity(
                    client_id=str(self.client_id),
                    stream_id=str(self.content_id),
                    bytes_delta=float(bytes_since_last),
                    protocol="TS",
                    ip_address=str(self.client_ip or "unknown"),
                    user_agent=str(self.client_user_agent or "unknown"),
                    request_kind="stream",
                    chunks_delta=int(chunks_since_last),
                    now=now,
                    worker_id="ts_proxy",
                )

            self.current_rate = bytes_since_last / elapsed / 1024  # KB/s
            
            logger.debug(f"[{self.client_id}] Rate: {self.current_rate:.1f} KB/s, Total: {self.bytes_sent / 1024 / 1024:.1f} MB")
            
            self.last_stats_time = now
            self.last_stats_bytes = self.bytes_sent
            self.last_stats_chunks = self.chunks_sent
            
            # Update bytes_sent in Redis
            if hasattr(self, 'client_manager') and self.client_manager:
                self.client_manager.update_client_bytes_sent(self.client_id, self.bytes_sent)
    
    def _refresh_ttl(self):
        """Refresh client TTL in Redis"""
        if hasattr(self, 'client_manager') and self.client_manager:
            self.client_manager.refresh_client_ttl()
            self.last_ttl_refresh = time.time()
    
    def _cleanup(self):
        """Cleanup after streaming ends"""
        # Remove client
        if hasattr(self, 'client_manager') and self.client_manager:
            self.client_manager.remove_client(self.client_id)
        
        logger.info(f"[{self.client_id}] Cleanup complete")


def create_stream_generator(content_id, client_id, client_ip, client_user_agent, stream_initializing=False, seekback=None):
    """Factory function to create StreamGenerator"""
    return StreamGenerator(content_id, client_id, client_ip, client_user_agent, stream_initializing, seekback)
