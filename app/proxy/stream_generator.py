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

# Pacing configuration
PACING_BURST_CHUNKS = 3
FAT_KEEPALIVE_PACKETS = 50


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
        
        # Split byte tracking for precise pacing (VBR Controller)
        self.video_bytes_sent = 0
        self.network_bytes_sent = 0
        self.has_sent_first_frame = False
        
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
        self.pacing_burst_chunks = PACING_BURST_CHUNKS # Burst allowance for chunk-based pacing
        self.pacing_burst_seconds = 6.0               # Burst allowance for byte-based pacing
        
        # Byte-based pacing (Target Bitrate)
        self.stream_bitrate = 0  # In bytes per second
    
    def generate(self):
        """Generator function that produces stream content for the client"""
        # Local import avoids creating import cycles at module import time.
        from ..services.metrics import observe_proxy_egress_bytes

        self.stream_start_time = time.time()
        self.bytes_sent = 0
        self.chunks_sent = 0
        self.video_bytes_sent = 0
        self.network_bytes_sent = 0
        self.has_sent_first_frame = False
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
            # Get no data timeout settings for starvation handling
            no_data_max_checks = ConfigHelper.no_data_timeout_checks()
            no_data_check_interval = ConfigHelper.no_data_check_interval()
            first_chunk_yielded = False
            
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
                        
                        # Tracking for 'Probe-Then-Hold' and pacing math
                        if not first_chunk_yielded:
                            first_chunk_yielded = True
                            self.has_sent_first_frame = True
                        
                        self.bytes_sent += chunk_len
                        self.video_bytes_sent += chunk_len
                        self.network_bytes_sent += chunk_len
                        
                        observe_proxy_egress_bytes("TS", chunk_len)
                        self.chunks_sent += 1
                        self.last_chunk_sent_time = time.time()
                        
                        # Probe Release: Trigger pre-buffer hold immediately after first video chunk
                        if first_chunk_yielded and self.chunks_sent == 1:
                            prebuffer_seconds = max(0.0, float(ConfigHelper.proxy_prebuffer_seconds()))
                            if prebuffer_seconds > 0.0:
                                yield from self._apply_prebuffer_hold(prebuffer_seconds)

                        self._maybe_apply_client_pacing()
                    
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
                    # IMPORTANT: Use pulses of sleep with Null packets to keep connection alive
                    no_data_check_interval = ConfigHelper.no_data_check_interval()
                    sleep_remaining = no_data_check_interval
                    while sleep_remaining > 0:
                        if getattr(self, "has_sent_first_frame", False):
                            fat_keepalive = create_ts_packet(pid_high=NULL_PID_HIGH, pid_low=NULL_PID_LOW) * FAT_KEEPALIVE_PACKETS
                            yield fat_keepalive
                            self.network_bytes_sent += len(fat_keepalive)
                        
                        pulse = min(sleep_remaining, 0.5)
                        time.sleep(pulse)
                        sleep_remaining -= pulse
                
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

            # Stream manager must complete session request AND bitrate detection (API mode) 
            # before clients start normal streaming.
            is_connected = bool(getattr(manager, "connected", False))
            has_url = bool(getattr(manager, "playback_url", None))
            has_bitrate = bool(getattr(manager, "bitrate", 0) > 0)
            
            if is_connected and has_url:
                if manager_mode == PROXY_MODE_API and not has_bitrate:
                    # Still waiting for bitrate propagation in API mode
                    pass
                else:
                    return True

            # SILENT WAIT: Do not send keep-alives during the handshake phase
            # to avoid poisoning the player's probe with Null packets.
            time.sleep(check_interval)
        
        logger.error(f"[{self.client_id}] Stream initialization timeout")
        return False
    
    def _wait_for_probe_chunk(self, min_index=None):
        """Wait silently for at least one chunk to be available in the buffer."""
        timeout = max(float(ConfigHelper.initial_data_wait_timeout()), 10.0)
        check_interval = ConfigHelper.initial_data_check_interval()
        start_time = time.time()
        baseline_index = max(0, int(min_index or 0))

        logger.info(
            f"[{self.client_id}] Waiting for probe chunk in buffer "
            f"(timeout: {timeout:.1f}s, baseline_index: {baseline_index})..."
        )
        
        while time.time() - start_time < timeout:
            if self.buffer.index > baseline_index:
                return True
            time.sleep(check_interval)
        
        logger.error(f"[{self.client_id}] Timeout waiting for probe chunk")
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
        """Pace the client to the engine's target bitrate or EMA speed."""
        if not self.pacing_start_time:
            return

        # 1. Base Pacing variables
        current_bitrate = self.stream_bitrate
        pacing_burst_seconds = self.pacing_burst_seconds
        now = time.time()
        elapsed = now - self.pacing_start_time
        
        if elapsed <= 1.0: # Grace period
            return

        # PRIMARY: Byte-based pacing using engine's native target bitrate
        if current_bitrate > 0:
            # 2. DRIFT COMPENSATION: 
            if hasattr(self, "buffer") and hasattr(self, "local_index"):
                proxy_runway_chunks = max(0, self.buffer.index - self.local_index)
                if proxy_runway_chunks > 15: # Engine is running away from the client!
                    current_bitrate = int(current_bitrate * 1.15) 

            pacing_burst_bytes = int(current_bitrate * pacing_burst_seconds)
            expected_bytes = elapsed * current_bitrate
            
            # 3. Throttle using the video_bytes_sent only (ignore keep-alives)
            if self.video_bytes_sent > expected_bytes + pacing_burst_bytes:
                wait_time = (self.video_bytes_sent - pacing_burst_bytes) / float(current_bitrate) - elapsed
                
                while wait_time > 0:
                    pulse = min(wait_time, 0.5)
                    time.sleep(pulse)
                    
                    # Recalculate based on total elapsed time
                    now = time.time()
                    elapsed = now - self.pacing_start_time
                    wait_time = (self.video_bytes_sent - pacing_burst_bytes) / float(current_bitrate) - elapsed
                
                # We applied byte pacing, skip chunk fallback
                return

        # FALLBACK: Chunk-based pacing using buffer EMA if bitrate is unknown
        source_rate = getattr(self.buffer, "chunk_rate_ema", 1.0)
        if source_rate <= 0:
            return
            
        pacing_burst_chunks = self.pacing_burst_chunks
        expected_chunks = elapsed * source_rate
        if self.chunks_sent > expected_chunks + pacing_burst_chunks:
            wait_time = (self.chunks_sent - pacing_burst_chunks) / float(source_rate) - elapsed
            
            while wait_time > 0:
                pulse = min(wait_time, 0.5)
                time.sleep(pulse)
                
                now = time.time()
                elapsed = now - self.pacing_start_time
                wait_time = (self.chunks_sent - pacing_burst_chunks) / float(source_rate) - elapsed

    def _apply_prebuffer_hold(self, prebuffer_seconds):
        """Build the required safety runway after the probe chunk has been released."""
        target_chunk_size = getattr(self.buffer, "target_chunk_size", 1024 * 1024)
        
        # Late-binding bitrate check: try one last time to get it from manager or Redis
        if self.stream_bitrate <= 0:
            from .server import ProxyServer
            proxy_server = ProxyServer.get_instance()
            manager = proxy_server.stream_managers.get(self.content_id)
            if manager and getattr(manager, "bitrate", 0) > 0:
                self.stream_bitrate = int(manager.bitrate) # Already in Bytes/s
                logger.info(f"[{self.client_id}] Late-binding bitrate captured from manager: {self.stream_bitrate} B/s")
            else:
                try:
                    metadata_key = RedisKeys.stream_metadata(self.content_id)
                    bitrate_raw = self.client_manager.redis_client.hget(metadata_key, StreamMetadataField.BITRATE)
                    if bitrate_raw:
                        self.stream_bitrate = int(bitrate_raw) # Already in Bytes/s
                        logger.info(f"[{self.client_id}] Late-binding bitrate captured from Redis: {self.stream_bitrate} B/s")
                except Exception:
                    pass

        # Bitrate safety floor (2.5 Mbps = 312,500 B/s)
        MIN_SAFE_BITRATE_BPS = 312500 
        effective_bitrate = max(self.stream_bitrate, MIN_SAFE_BITRATE_BPS)
        if effective_bitrate > self.stream_bitrate and self.stream_bitrate > 0:
            logger.info(f"[{self.client_id}] Applying bitrate floor: {effective_bitrate} B/s (Reported: {self.stream_bitrate} B/s)")
        
        target_chunks = int(math.ceil((prebuffer_seconds * effective_bitrate) / float(target_chunk_size)))
        logger.info(
            f"[{self.client_id}] Prebuffer target: {prebuffer_seconds}s @ {effective_bitrate} B/s "
            f"({target_chunk_size} B/chunk) -> {target_chunks} chunks"
        )
        
        start_wait = time.time()
        last_logged_size = -1
        last_log_time = 0.0
        
        # PROOF OF LIFE: Record the engine index at the start.
        # We want to see at least one NEW chunk produced before we finish.
        initial_engine_index = self.buffer.index
        
        while True:
            current_buffer_size = max(0, int(self.buffer.index) - int(self.local_index))
            now = time.time()
            
            # Condition: Target runway met AND either engine has progressed OR we've waited a bit
            has_runway = current_buffer_size >= target_chunks
            has_progressed = self.buffer.index > initial_engine_index
            has_timed_out_min = (now - start_wait) >= 2.0
            
            if has_runway and (has_progressed or has_timed_out_min):
                logger.info(
                    f"[{self.client_id}] Prebuffer complete after {now - start_wait:.1f}s "
                    f"(Runway: {current_buffer_size} chunks, Engine Progressed: {has_progressed})"
                )
                break
            
            # Debounced logging: only log if size increased AND at least 2 seconds passed
            if current_buffer_size != last_logged_size and (now - last_log_time) >= 2.0:
                logger.info(f"[{self.client_id}] Hoarding... Runway: {current_buffer_size}/{target_chunks} chunks")
                last_logged_size = current_buffer_size
                last_log_time = now
            
            if now - start_wait > max(30.0, prebuffer_seconds * 2):
                logger.warning(f"[{self.client_id}] Prebuffer hold timed out at {current_buffer_size} chunks")
                break
                
            # Blast Fat Keep-Alives to prevent HTTP timeouts
            fat_keepalive = create_ts_packet(pid_high=NULL_PID_HIGH, pid_low=NULL_PID_LOW) * FAT_KEEPALIVE_PACKETS
            yield fat_keepalive
            self.network_bytes_sent += len(fat_keepalive)
            
            time.sleep(0.5)

    def _advance_local_index(self, chunks_received: int, fetched_end_index=None):
        """Advance client position by fetched range when available."""
        if fetched_end_index is not None and isinstance(fetched_end_index, int):
            if fetched_end_index >= self.local_index:
                self.local_index = int(fetched_end_index)
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
            # Calculate lag using the best available data
            chunks_behind = max(0, int(self.buffer.index) - int(self.local_index))
            target_chunk_size = getattr(self.buffer, "target_chunk_size", 1024 * 1024)
            
            if self.stream_bitrate > 0:
                # Precision math: bytes_behind / bytes_per_sec
                seconds_behind = max(0.0, (float(chunks_behind) * target_chunk_size) / float(self.stream_bitrate))
                confidence = 0.90
            else:
                # Fallback: chunk_count / observation_rate
                chunk_rate = float(self.chunk_rate_ema or 1.0)
                seconds_behind = max(0.0, float(chunks_behind) / chunk_rate)
                confidence = 0.75 if self.chunk_rate_ema else 0.55

            # ALWAYS APPLY CONSUMPTION DECAY:
            # Our 'raw' calculation only counts data sitting in Redis. We must 
            # subtract the time elapsed since we last delivered a chunk to the 
            # player's socket, as the player has likely watched that data by now.
            if self.chunks_sent > 0:
                elapsed_since_chunk = max(0.0, now - float(self.last_chunk_sent_time or now))
                seconds_behind = max(0.0, seconds_behind - elapsed_since_chunk)
            
            normalized_source = str(source or "ts_cursor_ema")

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

        # EXTRACT BITRATE FOR PACING: Prioritize direct manager data, fallback to Redis
        manager = proxy_server.stream_managers.get(self.content_id)
        if manager and getattr(manager, "bitrate", 0) > 0:
            self.stream_bitrate = int(manager.bitrate) # Already in Bytes/s
            logger.info(f"[{self.client_id}] Target bitrate extracted from manager: {self.stream_bitrate} bytes/s")
        else:
            try:
                metadata_key = RedisKeys.stream_metadata(self.content_id)
                bitrate_raw = self.client_manager.redis_client.hget(metadata_key, StreamMetadataField.BITRATE)
                if bitrate_raw:
                    self.stream_bitrate = int(bitrate_raw) # Already in Bytes/s
                    logger.info(f"[{self.client_id}] Target bitrate extracted from Redis: {self.stream_bitrate} bytes/s")
            except Exception as e:
                logger.debug(f"[{self.client_id}] Failed to extract bitrate for pacing: {e}")
        
        # Capture the current index before registering the client so we can
        # track the client's absolute position in the buffer.
        start_index = self.buffer.index

        # Reuse the existing baseline for deterministic hot reconnects.
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
            
            # Revoke burst allowance for hot reconnects to prevent 'burst surfing'
            self.pacing_burst_chunks = 0
            self.stream_bitrate = 0 # Revokes byte-based burst
            
            logger.info(f"[{self.client_id}] Hot reconnect detected. Revoking burst allowance to prevent buffer surfing.")
        
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
        
        # Wait for at least one chunk in buffer (Probe Release Phase)
        # This gives the HTTP streamer time to fetch data from the playback URL
        if not self._wait_for_probe_chunk(min_index=start_index):
            # Error already logged in _wait_for_probe_chunk
            return False
        
        # Keep playback behind live edge to ensure a safety runway for failovers.
        # INITIAL POSITION: Start at the earliest possible chunk to satisfy the probe immediately.
        requested_seekback = 0
        try:
            if self.seekback is not None:
                requested_seekback = int(float(self.seekback))
        except (ValueError, TypeError):
            requested_seekback = 0

        if requested_seekback > 0:
            # Explicit seekback requested (e.g. from HLS or URL param)
            self.local_index = max(0, int(start_index))
            logger.info(f"[{self.client_id}] Starting with explicit seekback: index {self.local_index}")
        else:
            # Normal play: start at the live edge to release a valid chunk immediately.
            # The 'hoarding' phase will happen after this first chunk is delivered.
            self.local_index = max(0, int(start_index))
            logger.info(
                f"[{self.client_id}] Probe-first startup: starting at index {self.local_index} "
                f"(live {self.buffer.index})"
            )

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
