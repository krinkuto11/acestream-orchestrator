import asyncio
import os
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from .state import state
from ..models.schemas import StreamStatSnapshot, LivePosData
from ..core.config import cfg
from .metrics import on_stream_stat_update

logger = logging.getLogger(__name__)


class Collector:
    """
    Stream statistics collector.
    
    This service periodically polls stat URLs for all active streams and
    collects stream statistics (peers, speed, etc.) for monitoring and metrics.
    
    The COLLECT_INTERVAL_S determines how frequently stats are collected (default 1 second).
    """
    def __init__(self):
        self._task = None
        self._stop = asyncio.Event()
        # Legacy API status probes are blocking socket calls; limit offloaded
        # concurrency so the collector loop remains responsive under load.
        try:
            legacy_probe_workers = max(2, int(os.getenv("LEGACY_STATS_PROBE_WORKERS", "8")))
        except Exception:
            legacy_probe_workers = 8
        self._legacy_probe_semaphore = asyncio.Semaphore(legacy_probe_workers)

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            await self._task

    async def _run(self):
        async with httpx.AsyncClient(timeout=3.0) as client:
            while not self._stop.is_set():
                streams = state.list_streams(status="started")
                # Collect stats for each active stream
                tasks = [self._collect_one(client, s.id, s.stat_url) for s in streams]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=cfg.COLLECT_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass

    async def _collect_one(self, client: httpx.AsyncClient, stream_id: str, stat_url: str):
        if not stat_url:
            # Legacy API streams don't expose stat_url. Pull stats from the
            # active proxy StreamManager session.
            stream = state.get_stream(stream_id)
            if stream:
                await self._collect_legacy_stream(stream_id, stream)
            return

        try:
            logger.debug(f"Collecting stats for stream_id={stream_id} url={stat_url}")
            r = await client.get(stat_url)
            logger.debug(f"HTTP {r.status_code} from {stat_url} for stream {stream_id}")
            if r.status_code >= 300:
                # Log response body at debug so operators can inspect redirects/errors
                try:
                    text = r.text
                except Exception:
                    text = "<unreadable response body>"
                logger.warning(f"Non-success response collecting stats for {stream_id} ({r.status_code}): {text}")
                return
            try:
                data = r.json()
            except Exception as e:
                # Response wasn't valid JSON — log the body to help debugging
                body = r.text if hasattr(r, 'text') else '<no-body>'
                logger.debug(f"Failed to parse JSON from {stat_url} for stream {stream_id}: {e}; body={body}")
                return

            # Check if the stream has an error response
            # When a stream has stopped, the engine may return: {"response": null, "error": "unknown playback session id"}
            if data.get("response") is None and data.get("error"):
                error_msg = data.get("error", "").lower()
                logger.debug(f"Stat endpoint reported error for {stream_id}: {data.get('error')}")
                # Stream has ended on the engine side - skip stats collection
                return

            payload = data.get("response") or {}
            # Log the raw payload at debug level (truncated to avoid huge logs)
            try:
                raw_payload_str = str(payload)
                if len(raw_payload_str) > 2000:
                    raw_payload_str = raw_payload_str[:2000] + '...<truncated>'
            except Exception:
                raw_payload_str = '<unrepresentable payload>'
            logger.debug(f"Payload for {stream_id} from {stat_url}: {raw_payload_str}")

            # Handle both snake_case and camelCase field names from AceStream API
            # Some engine versions return speedDown/speedUp, others return speed_down/speed_up
            # Use explicit None check to preserve 0 values (0 is valid speed)
            speed_down_snake = payload.get("speed_down")
            speed_down_camel = payload.get("speedDown")
            speed_down = speed_down_snake if speed_down_snake is not None else speed_down_camel
            speed_up_snake = payload.get("speed_up")
            speed_up_camel = payload.get("speedUp")
            speed_up = speed_up_snake if speed_up_snake is not None else speed_up_camel

            # Log which keys were used so it's clear which engine version responded
            logger.debug(
                f"Selected speed values for {stream_id}: speed_down={speed_down} (snake={speed_down_snake} camel={speed_down_camel}), "
                f"speed_up={speed_up} (snake={speed_up_snake} camel={speed_up_camel})"
            )

            # Extract status field
            status = payload.get("status")

            # Extract bitrate
            bitrate = payload.get("bitrate")
            if bitrate is None:
                bitrate = payload.get("bitRate")

            # Extract livepos data (for live streams)
            # AceStream engines return livepos object with fields that may vary by version:
            # - pos: current playback position timestamp
            # - live_first/first_ts/first: start of live buffer (preference: live_first > first_ts > first)
            # - live_last/last_ts/last: end of live buffer (preference: live_last > last_ts > last)
            # - buffer_pieces: number of buffered pieces
            # Example from AceStream 3.x API: {"pos": "1767629806", "live_first": "1767628008", "live_last": "1767629808", ...}
            livepos_data = None
            livepos_raw = payload.get("livepos")
            if livepos_raw:
                from ..models.schemas import LivePosData
                livepos_data = LivePosData(
                    pos=livepos_raw.get("pos"),
                    # Prefer live_first, fallback to first_ts, then first (for older versions)
                    live_first=livepos_raw.get("live_first") or livepos_raw.get("first_ts") or livepos_raw.get("first"),
                    # Prefer live_last, fallback to last_ts, then last (for older versions)
                    live_last=livepos_raw.get("live_last") or livepos_raw.get("last_ts") or livepos_raw.get("last"),
                    # Store both first_ts and last_ts for compatibility
                    first_ts=livepos_raw.get("first_ts") or livepos_raw.get("first"),
                    last_ts=livepos_raw.get("last_ts") or livepos_raw.get("last"),
                    buffer_pieces=livepos_raw.get("buffer_pieces")
                )

            # Get the stream key to query proxy metrics
            proxy_pieces = None
            stream = state.get_stream(stream_id)
            if stream and stream.key:
                proxy_pieces = _get_proxy_stream_buffer_pieces(stream.key)

            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=payload.get("peers"),
                speed_down=speed_down,
                speed_up=speed_up,
                downloaded=payload.get("downloaded"),
                uploaded=payload.get("uploaded"),
                status=status,
                bitrate=bitrate,
                livepos=livepos_data,
                proxy_buffer_pieces=proxy_pieces,
            )
            state.append_stat(stream_id, snap)
            logger.debug(f"Appended stat for {stream_id}: peers={snap.peers} speed_down={snap.speed_down} speed_up={snap.speed_up} downloaded={snap.downloaded} uploaded={snap.uploaded} status={snap.status} livepos={bool(livepos_data)} proxy_buffer={proxy_pieces}")

            # Update cumulative byte metrics
            try:
                on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
            except Exception:
                logger.exception(f"Error updating cumulative metrics for stream {stream_id}")
        
        except httpx.ConnectTimeout:
            # Connection timeout - engine may be slow or unavailable
            logger.debug(f"Connection timeout collecting stats for {stream_id} from {stat_url}")
            return
        except httpx.TimeoutException:
            # Other timeout exceptions (read timeout, pool timeout, etc.)
            logger.debug(f"Timeout collecting stats for {stream_id} from {stat_url}")
            return
        except httpx.HTTPError as e:
            # Other HTTP-related errors (connection errors, etc.)
            logger.debug(f"HTTP error collecting stats for {stream_id} from {stat_url}: {e}")
            return
        except Exception:
            logger.exception(f"Unhandled exception while collecting stats for {stream_id} from {stat_url}")
            return

    async def _collect_legacy_stream(self, stream_id: str, stream) -> None:
        """Collect stats for a legacy API stream through the active proxy session."""
        try:
            # Legacy stats can only be queried on the same API session used for START,
            # so we read them from the in-process proxy stream manager.
            from ..proxy.server import ProxyServer
            from ..proxy.hls_proxy import HLSProxyServer
            from .hls_segmenter import hls_segmenter_service

            proxy = ProxyServer.get_instance()
            manager = proxy.stream_managers.get(stream.key) if proxy else None
            probe = None

            if manager:
                async with self._legacy_probe_semaphore:
                    probe = await asyncio.to_thread(
                        manager.collect_legacy_stats_probe,
                        1,
                        1.0,
                    )

            if not probe:
                # Try integrated HLS Proxy
                hls_proxy = HLSProxyServer.get_instance()
                hls_manager = hls_proxy.stream_managers.get(stream.key) if hls_proxy else None
                if hls_manager:
                    async with self._legacy_probe_semaphore:
                        probe = await asyncio.to_thread(hls_manager.collect_legacy_stats_probe)

            if not probe:
                # API-mode HLS sessions are controlled by external segmenter service.
                async with self._legacy_probe_semaphore:
                    probe = await asyncio.to_thread(
                        hls_segmenter_service.collect_legacy_stats_probe,
                        stream.key,
                        1,
                        1.0,
                    )

            if not probe:
                # Stream may be reusing a monitoring session (no direct legacy socket on proxy side).
                from .legacy_stream_monitoring import legacy_stream_monitoring_service

                reusable = await legacy_stream_monitoring_service.get_reusable_session_for_content(stream.key)
                if reusable:
                    probe = reusable.get("latest_status") or None

            if not probe:
                return

            speed_down = probe.get("speed_down")
            if speed_down is None:
                speed_down = probe.get("http_speed_down")

            peers = probe.get("peers")
            if peers is None:
                peers = probe.get("http_peers")

            downloaded = probe.get("downloaded")
            if downloaded is None:
                downloaded = probe.get("http_downloaded")

            bitrate = probe.get("bitrate")
            if bitrate is None:
                bitrate = probe.get("http_bitrate")

            # Keep numeric fields stable for panel/metrics even if probe omits fields.
            speed_down = 0 if speed_down is None else speed_down
            speed_up = 0 if probe.get("speed_up") is None else probe.get("speed_up")
            downloaded = 0 if downloaded is None else downloaded
            uploaded = 0 if probe.get("uploaded") is None else probe.get("uploaded")

            livepos = None
            livepos_raw = probe.get("livepos") or {}
            if livepos_raw:
                livepos = LivePosData(
                    pos=livepos_raw.get("pos"),
                    live_first=livepos_raw.get("live_first") or livepos_raw.get("first_ts"),
                    live_last=livepos_raw.get("live_last") or livepos_raw.get("last_ts"),
                    first_ts=livepos_raw.get("first_ts"),
                    last_ts=livepos_raw.get("last_ts"),
                    buffer_pieces=str(livepos_raw.get("buffer_pieces")) if livepos_raw.get("buffer_pieces") is not None else None,
                )

            proxy_pieces = _get_proxy_stream_buffer_pieces(stream.key)

            snap = StreamStatSnapshot(
                ts=datetime.now(timezone.utc),
                peers=peers,
                speed_down=speed_down,
                speed_up=speed_up,
                downloaded=downloaded,
                uploaded=uploaded,
                status=probe.get("status_text") or probe.get("status"),
                bitrate=bitrate,
                livepos=livepos,
                proxy_buffer_pieces=proxy_pieces,
            )
            state.append_stat(stream_id, snap)

            try:
                on_stream_stat_update(stream_id, snap.uploaded, snap.downloaded)
            except Exception:
                logger.exception(f"Error updating cumulative metrics for legacy stream {stream_id}")
        except Exception:
            logger.exception(f"Unhandled exception while collecting legacy stats for {stream_id}")
            return

def _get_proxy_stream_buffer_pieces(stream_key: str) -> Optional[int]:
    try:
        from .client_tracker import client_tracking_service

        # 1. Check HLS Proxy (HTTP mode HLS)
        try:
            from ..proxy.hls_proxy import HLSProxyServer
            hls_proxy = HLSProxyServer.get_instance()
            if hls_proxy:
                buffer = hls_proxy.stream_buffers.get(stream_key)
                
                if buffer and buffer.keys():
                    latest_seq = max(buffer.keys())

                    hls_clients = client_tracking_service.get_stream_clients(
                        stream_key,
                        protocol="HLS",
                        worker_id="hls_proxy",
                    )
                    if hls_clients:
                        min_client_seq = latest_seq
                        has_active_clients = False

                        for client in hls_clients:
                            c_seq = client.get("last_sequence")
                            if c_seq is not None:
                                if c_seq < min_client_seq:
                                    min_client_seq = c_seq
                                has_active_clients = True
                        
                        if has_active_clients:
                            # Lag is the number of segments between head and slowest client
                            return max(0, latest_seq - min_client_seq)
                    
                    # If no clients, just show the current buffer size
                    return len(buffer.keys())
        except Exception:
            pass

        # 2. Check HLS Segmenter (API mode HLS)
        try:
            from .hls_segmenter import hls_segmenter_service
            
            # Use the segmenter service to calculate the lag for API-mode HLS.
            # We treat 'monitor_id' as the stream_key for external segmenters.
            if hls_segmenter_service.has_session(stream_key):
                latest_seq = hls_segmenter_service._latest_manifest_sequence(stream_key)
                
                if latest_seq is not None:
                    api_hls_clients = client_tracking_service.get_stream_clients(
                        stream_key,
                        protocol="HLS",
                        worker_id="api_hls_segmenter",
                    )
                    
                    if api_hls_clients:
                        min_client_seq = latest_seq
                        has_active_clients = False

                        for client in api_hls_clients:
                            c_seq = client.get("last_sequence")
                            if c_seq is not None:
                                try:
                                    c_seq_int = int(c_seq)
                                    if c_seq_int < min_client_seq:
                                        min_client_seq = c_seq_int
                                    has_active_clients = True
                                except (TypeError, ValueError):
                                    continue
                        
                        if has_active_clients:
                            # Lag is the number of segments between head and slowest client.
                            return max(0, latest_seq - min_client_seq)
                
                # If no clients or sequence not available, fall back to manifest window depth
                lag_seconds = hls_segmenter_service.estimate_manifest_buffer_seconds_behind(stream_key)
                # Normalize seconds to a 'pieces' equivalent (roughly 1 piece per second)
                return int(lag_seconds)
        except Exception:
            pass

        # 3. Check TS Proxy (HTTP and API mode MPEG-TS)
        from ..proxy.manager import ProxyManager
        from ..proxy.redis_keys import RedisKeys
        proxy = ProxyManager.get_instance()
        rc = getattr(proxy, "redis_client", None)
        if rc:
            b_val = rc.get(RedisKeys.buffer_index(stream_key))
            if not b_val:
                return 0
            latest_idx = int(b_val)

            client_ids = rc.smembers(RedisKeys.clients(stream_key)) or []
            if not client_ids:
                return 0

            min_client_idx = latest_idx
            from ..proxy.config_helper import Config as ProxyConfig
            chunk_size = int(getattr(ProxyConfig, "BUFFER_CHUNK_SIZE", 188 * 5644))

            has_clients = False
            for cid in client_ids:
                if isinstance(cid, bytes): cid = cid.decode("utf-8")
                client_key = RedisKeys.client_metadata(stream_key, cid)
                
                # Fetch both bytes_sent and initial_index to calculate absolute chunk position
                client_data = rc.hmget(client_key, ["bytes_sent", "initial_index"])
                if client_data and any(v is not None for v in client_data):
                    try:
                        b_sent = int(client_data[0] or 0)
                        initial_idx = int(client_data[1] or 0)
                        
                        # Absolute client position = start position + chunks consumed
                        c_idx = initial_idx + (b_sent // chunk_size)
                        
                        if c_idx < min_client_idx:
                            min_client_idx = c_idx
                        has_clients = True
                    except (TypeError, ValueError):
                        continue

            if has_clients:
                # Buffer size is distance between last written chunk and furthest client
                return max(0, latest_idx - min_client_idx)
            return 0
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Failed to get proxy buffer pieces for {stream_key}: {e}")
    return None

collector = Collector()
