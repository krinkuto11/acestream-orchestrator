from prometheus_client import Counter, Gauge, Histogram, make_asgi_app, Enum
import threading
import time
from datetime import datetime, timedelta, timezone
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

# Aggregated metrics from all engines
orch_total_uploaded_bytes = Gauge("orch_total_uploaded_bytes", "Total bytes uploaded from all engines")
orch_total_downloaded_bytes = Gauge("orch_total_downloaded_bytes", "Total bytes downloaded from all engines")
orch_total_uploaded_mb = Gauge("orch_total_uploaded_mb", "Total MB uploaded from all engines")
orch_total_downloaded_mb = Gauge("orch_total_downloaded_mb", "Total MB downloaded from all engines")
orch_total_upload_speed_mbps = Gauge("orch_total_upload_speed_mbps", "Current sum of upload speeds from all engines in MB/s")
orch_total_download_speed_mbps = Gauge("orch_total_download_speed_mbps", "Current sum of download speeds from all engines in MB/s")
orch_total_peers = Gauge("orch_total_peers", "Current total peers across all engines")
orch_total_streams = Gauge("orch_total_streams", "Current number of active streams")
orch_healthy_engines = Gauge("orch_healthy_engines", "Number of healthy engines")
orch_unhealthy_engines = Gauge("orch_unhealthy_engines", "Number of unhealthy engines")
orch_used_engines = Gauge("orch_used_engines", "Number of engines currently handling streams")
orch_vpn_health = Enum("orch_vpn_health", "Current health status of primary VPN container", states=["healthy", "unhealthy", "unknown", "disabled", "starting"])
orch_vpn1_health = Enum("orch_vpn1_health", "Health status of VPN1 container", states=["healthy", "unhealthy", "unknown", "disabled", "starting"])
orch_vpn2_health = Enum("orch_vpn2_health", "Health status of VPN2 container", states=["healthy", "unhealthy", "unknown", "disabled", "starting"])
orch_vpn1_engines = Gauge("orch_vpn1_engines", "Number of engines assigned to VPN1")
orch_vpn2_engines = Gauge("orch_vpn2_engines", "Number of engines assigned to VPN2")
orch_extra_engines = Gauge("orch_extra_engines", "Number of engines beyond MIN_REPLICAS")

# Performance metrics - operation timing statistics
orch_performance_count = Gauge("orch_performance_operation_count", "Number of samples for operation", ["operation"])
orch_performance_avg_ms = Gauge("orch_performance_operation_avg_ms", "Average duration in milliseconds", ["operation"])
orch_performance_p50_ms = Gauge("orch_performance_operation_p50_ms", "50th percentile (median) duration in milliseconds", ["operation"])
orch_performance_p95_ms = Gauge("orch_performance_operation_p95_ms", "95th percentile duration in milliseconds", ["operation"])
orch_performance_p99_ms = Gauge("orch_performance_operation_p99_ms", "99th percentile duration in milliseconds", ["operation"])
orch_performance_min_ms = Gauge("orch_performance_operation_min_ms", "Minimum duration in milliseconds", ["operation"])
orch_performance_max_ms = Gauge("orch_performance_operation_max_ms", "Maximum duration in milliseconds", ["operation"])
orch_performance_success_rate = Gauge("orch_performance_operation_success_rate", "Success rate percentage", ["operation"])

# Proxy RED metrics (rate, errors, duration)
orch_proxy_stream_requests_total = Counter(
    "orch_proxy_stream_requests_total",
    "Total stream requests handled by proxy endpoint",
    ["mode", "endpoint", "result"],
)
orch_proxy_stream_request_duration_seconds = Histogram(
    "orch_proxy_stream_request_duration_seconds",
    "Duration of stream endpoint request handling",
    ["mode", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
orch_proxy_ttfb_seconds = Histogram(
    "orch_proxy_ttfb_seconds",
    "Approximate time-to-first-byte for stream responses",
    ["mode", "endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20),
)
orch_proxy_http_errors_total = Counter(
    "orch_proxy_http_errors_total",
    "Proxy HTTP errors by endpoint and status",
    ["endpoint", "status_code"],
)
orch_proxy_client_connect_total = Counter(
    "orch_proxy_client_connect_total",
    "Total client connections accepted by proxy",
    ["mode"],
)
orch_proxy_client_disconnect_total = Counter(
    "orch_proxy_client_disconnect_total",
    "Total client disconnects observed by proxy",
    ["mode"],
)

# Proxy high-level gauges
orch_proxy_active_clients = Gauge("orch_proxy_active_clients", "Current active clients across TS and HLS proxy")
orch_proxy_active_clients_ts = Gauge("orch_proxy_active_clients_ts", "Current active clients connected to TS proxy")
orch_proxy_active_clients_hls = Gauge("orch_proxy_active_clients_hls", "Current active clients connected to HLS proxy")
orch_proxy_disconnect_rate_per_minute = Gauge("orch_proxy_disconnect_rate_per_minute", "Client disconnect rate over last minute")
orch_proxy_success_rate = Gauge("orch_proxy_success_rate", "Successful stream request rate over last minute in percentage")
orch_proxy_4xx_rate_per_minute = Gauge("orch_proxy_4xx_rate_per_minute", "4xx request rate over last minute")
orch_proxy_5xx_rate_per_minute = Gauge("orch_proxy_5xx_rate_per_minute", "5xx request rate over last minute")
orch_proxy_ttfb_avg_ms = Gauge("orch_proxy_ttfb_avg_ms", "Average proxy TTFB over last minute in ms")
orch_proxy_ttfb_p95_ms = Gauge("orch_proxy_ttfb_p95_ms", "Approximate proxy TTFB p95 over last minute in ms")
orch_proxy_ingress_bytes_total = Gauge("orch_proxy_ingress_bytes_total", "Total bytes ingressed by proxy from upstream engines")
orch_proxy_egress_bytes_total = Gauge("orch_proxy_egress_bytes_total", "Total bytes egressed by proxy to downstream clients")
orch_proxy_ingress_rate_bps = Gauge("orch_proxy_ingress_rate_bps", "Proxy ingress rate in bytes/s")
orch_proxy_egress_rate_bps = Gauge("orch_proxy_egress_rate_bps", "Proxy egress rate in bytes/s")

# Engine and stream depth gauges
orch_engine_state_count = Gauge("orch_engine_state_count", "Engine counts by derived state", ["state"])
orch_engine_uptime_avg_seconds = Gauge("orch_engine_uptime_avg_seconds", "Average engine uptime in seconds")
orch_stream_buffer_pieces_avg = Gauge("orch_stream_buffer_pieces_avg", "Average stream buffer pieces across live streams")
orch_stream_buffer_pieces_min = Gauge("orch_stream_buffer_pieces_min", "Minimum stream buffer pieces across live streams")
orch_active_infohash = Gauge("orch_active_infohash", "Indicator for currently active stream keys", ["stream_key"])

# Docker USE metrics
orch_docker_total_cpu_percent = Gauge("orch_docker_total_cpu_percent", "Total Docker CPU usage percent across engines")
orch_docker_total_memory_bytes = Gauge("orch_docker_total_memory_bytes", "Total Docker memory usage in bytes across engines")
orch_docker_network_rx_bytes_total = Gauge("orch_docker_network_rx_bytes_total", "Total Docker network ingress bytes across engines")
orch_docker_network_tx_bytes_total = Gauge("orch_docker_network_tx_bytes_total", "Total Docker network egress bytes across engines")
orch_docker_network_rx_rate_bps = Gauge("orch_docker_network_rx_rate_bps", "Docker network ingress rate in bytes/s across engines")
orch_docker_network_tx_rate_bps = Gauge("orch_docker_network_tx_rate_bps", "Docker network egress rate in bytes/s across engines")
orch_docker_block_read_bytes_total = Gauge("orch_docker_block_read_bytes_total", "Total Docker block read bytes across engines")
orch_docker_block_write_bytes_total = Gauge("orch_docker_block_write_bytes_total", "Total Docker block write bytes across engines")
orch_docker_restart_total = Gauge("orch_docker_restart_total", "Total restart count across engine containers")
orch_docker_oom_killed_total = Gauge("orch_docker_oom_killed_total", "Number of engine containers marked OOM killed")

# North-star gauges
orch_global_egress_bandwidth_mbps = Gauge("orch_global_egress_bandwidth_mbps", "Global outbound bandwidth in Mbps")
orch_system_success_rate = Gauge("orch_system_success_rate", "System success rate in percentage")

metrics_app = make_asgi_app()

# Cumulative byte tracking across all streams (active and ended)
# Protected by lock for thread-safety
_cumulative_lock = threading.Lock()
_cumulative_uploaded_bytes = 0
_cumulative_downloaded_bytes = 0
_stream_last_values: Dict[str, Dict[str, Optional[int]]] = {}  # stream_id -> {uploaded, downloaded}
_active_stream_keys: Set[str] = set()

_proxy_window_lock = threading.Lock()
_proxy_request_events: Deque[Dict[str, Any]] = deque(maxlen=5000)
_proxy_disconnect_events: Deque[float] = deque(maxlen=5000)
_proxy_ttfb_events: Deque[float] = deque(maxlen=5000)

_proxy_io_lock = threading.Lock()
_proxy_ingress_observed_bytes = 0
_proxy_egress_observed_bytes = 0
_last_proxy_egress_observed_bytes = 0
_last_proxy_ingress_observed_bytes = 0
_last_proxy_sample_ts: Optional[float] = None
_last_proxy_client_bytes: Dict[str, int] = {}
_last_proxy_stream_ingress_bytes: Dict[str, int] = {}
_proxy_total_ingress_bytes = 0
_proxy_total_egress_bytes = 0
_last_proxy_ingress_rate_bps = 0.0
_last_proxy_egress_rate_bps = 0.0
_last_proxy_rate_ts: Optional[float] = None
_dashboard_last_persist_ts: Optional[float] = None

_docker_rate_lock = threading.Lock()
_last_network_rx_bytes: Optional[int] = None
_last_network_tx_bytes: Optional[int] = None
_last_network_sample_ts: Optional[float] = None


def _trim_old_events(now: float, max_age_seconds: float = 60.0):
    """Trim old metric events from rolling one-minute windows."""
    with _proxy_window_lock:
        while _proxy_request_events and (now - _proxy_request_events[0]["ts"]) > max_age_seconds:
            _proxy_request_events.popleft()
        while _proxy_disconnect_events and (now - _proxy_disconnect_events[0]) > max_age_seconds:
            _proxy_disconnect_events.popleft()
        while _proxy_ttfb_events and (now - _proxy_ttfb_events[0]["ts"]) > max_age_seconds:
            _proxy_ttfb_events.popleft()


def observe_proxy_request(mode: str, endpoint: str, duration_seconds: float, success: bool, status_code: Optional[int] = None):
    """Record proxy stream request RED metrics."""
    result = "success" if success else "error"
    safe_mode = mode or "unknown"
    safe_endpoint = endpoint or "unknown"

    orch_proxy_stream_requests_total.labels(mode=safe_mode, endpoint=safe_endpoint, result=result).inc()
    orch_proxy_stream_request_duration_seconds.labels(mode=safe_mode, endpoint=safe_endpoint).observe(max(0.0, duration_seconds))

    now = time.time()
    event = {
        "ts": now,
        "success": success,
        "status_code": int(status_code) if status_code is not None else (200 if success else 500),
    }
    with _proxy_window_lock:
        _proxy_request_events.append(event)

    if not success and status_code is not None:
        orch_proxy_http_errors_total.labels(endpoint=safe_endpoint, status_code=str(status_code)).inc()

    _trim_old_events(now)


def observe_proxy_ttfb(mode: str, endpoint: str, ttfb_seconds: float):
    """Record proxy TTFB metrics."""
    safe_mode = mode or "unknown"
    safe_endpoint = endpoint or "unknown"
    sanitized_ttfb = max(0.0, ttfb_seconds)

    orch_proxy_ttfb_seconds.labels(mode=safe_mode, endpoint=safe_endpoint).observe(sanitized_ttfb)

    now = time.time()
    with _proxy_window_lock:
        _proxy_ttfb_events.append({"ts": now, "value": sanitized_ttfb})
    _trim_old_events(now)


def observe_proxy_client_connect(mode: str):
    """Record proxy client connect events."""
    orch_proxy_client_connect_total.labels(mode=mode or "unknown").inc()


def observe_proxy_client_disconnect(mode: str):
    """Record proxy client disconnect events for rolling disconnect rate."""
    orch_proxy_client_disconnect_total.labels(mode=mode or "unknown").inc()
    now = time.time()
    with _proxy_window_lock:
        _proxy_disconnect_events.append(now)
    _trim_old_events(now)


def observe_proxy_ingress_bytes(mode: str, byte_count: int):
    """Observe upstream bytes received by proxy (used for HLS downloads)."""
    if byte_count <= 0:
        return
    global _proxy_ingress_observed_bytes
    with _proxy_io_lock:
        _proxy_ingress_observed_bytes += int(byte_count)


def observe_proxy_egress_bytes(mode: str, byte_count: int):
    """Observe downstream bytes sent by proxy (used for HLS segments)."""
    if byte_count <= 0:
        return
    global _proxy_egress_observed_bytes
    with _proxy_io_lock:
        _proxy_egress_observed_bytes += int(byte_count)


def _compute_proxy_clients_snapshot() -> Dict[str, int]:
    """Best-effort snapshot of active clients for TS and HLS proxies."""
    ts_clients = 0
    hls_clients = 0

    try:
        from ..proxy.manager import ProxyManager

        proxy = ProxyManager.get_instance()
        redis_client = getattr(proxy, "redis_client", None)
        if redis_client:
            from .state import state
            from ..proxy.redis_keys import RedisKeys

            stream_keys = [s.key for s in state.list_streams(status="started") if s.key]
            for stream_key in stream_keys:
                try:
                    ts_clients += int(redis_client.scard(RedisKeys.clients(stream_key)) or 0)
                except Exception:
                    continue
    except Exception:
        pass

    try:
        from ..proxy.hls_proxy import HLSProxyServer

        hls_proxy = HLSProxyServer.get_instance()
        managers = getattr(hls_proxy, "client_managers", {}) or {}
        for manager in managers.values():
            try:
                with manager.lock:
                    hls_clients += len(manager.last_activity)
            except Exception:
                continue
    except Exception:
        pass

    return {
        "ts": ts_clients,
        "hls": hls_clients,
        "total": ts_clients + hls_clients,
    }


def _compute_proxy_window_snapshot() -> Dict[str, float]:
    """Compute one-minute rolling RED summaries for dashboard and Prometheus gauges."""
    now = time.time()
    _trim_old_events(now)

    with _proxy_window_lock:
        request_events = list(_proxy_request_events)
        disconnect_events = list(_proxy_disconnect_events)
        ttfb_events = [e["value"] for e in _proxy_ttfb_events]

    total_requests = len(request_events)
    success_requests = sum(1 for e in request_events if e["success"])
    errors_4xx = sum(1 for e in request_events if 400 <= int(e["status_code"]) < 500)
    errors_5xx = sum(1 for e in request_events if int(e["status_code"]) >= 500)

    success_rate = (success_requests / total_requests * 100.0) if total_requests else 100.0
    disconnect_rate = float(len(disconnect_events))

    ttfb_avg_ms = (sum(ttfb_events) / len(ttfb_events) * 1000.0) if ttfb_events else 0.0
    ttfb_p95_ms = 0.0
    if ttfb_events:
        sorted_ttfb = sorted(ttfb_events)
        p95_idx = min(len(sorted_ttfb) - 1, int(round(0.95 * (len(sorted_ttfb) - 1))))
        ttfb_p95_ms = sorted_ttfb[p95_idx] * 1000.0

    return {
        "total_requests_1m": float(total_requests),
        "success_rate_percent": round(success_rate, 2),
        "disconnect_rate_per_min": disconnect_rate,
        "error_4xx_rate_per_min": float(errors_4xx),
        "error_5xx_rate_per_min": float(errors_5xx),
        "ttfb_avg_ms": round(ttfb_avg_ms, 2),
        "ttfb_p95_ms": round(ttfb_p95_ms, 2),
    }


def _compute_proxy_throughput_snapshot() -> Dict[str, float]:
    """Compute proxy-native ingress/egress totals and rates.

    - TS egress uses Redis client `bytes_sent` deltas.
    - TS ingress uses Redis stream buffer index deltas.
    - HLS ingress/egress uses explicit observer hooks in HLS fetch/serve paths.
    """
    from .state import state

    now = time.time()
    current_client_bytes: Dict[str, int] = {}
    current_stream_ingress_bytes: Dict[str, int] = {}

    try:
        from ..proxy.manager import ProxyManager
        from ..proxy.redis_keys import RedisKeys
        from ..proxy.config_helper import Config as ProxyConfig

        proxy = ProxyManager.get_instance()
        redis_client = getattr(proxy, "redis_client", None)
        if redis_client:
            active_streams = state.list_streams(status="started")
            if not active_streams:
                # Fallback for transitional states where streams may not yet be marked as started.
                active_streams = state.list_streams()
            active_keys = {s.key for s in active_streams if s.key}
            chunk_size = int(getattr(ProxyConfig, "BUFFER_CHUNK_SIZE", 188 * 5644))

            for stream_key in active_keys:
                try:
                    buffer_index_raw = redis_client.get(RedisKeys.buffer_index(stream_key))
                    buffer_index = int(buffer_index_raw or 0)
                    current_stream_ingress_bytes[stream_key] = max(0, buffer_index) * chunk_size
                except Exception:
                    continue

                try:
                    client_ids = redis_client.smembers(RedisKeys.clients(stream_key)) or []
                except Exception:
                    client_ids = []

                for client_id_raw in client_ids:
                    try:
                        client_id = client_id_raw.decode("utf-8") if isinstance(client_id_raw, bytes) else str(client_id_raw)
                        client_key = RedisKeys.client_metadata(stream_key, client_id)
                        bytes_sent_raw = redis_client.hget(client_key, "bytes_sent")
                        if bytes_sent_raw is None:
                            continue
                        bytes_sent = int(bytes_sent_raw.decode("utf-8") if isinstance(bytes_sent_raw, bytes) else bytes_sent_raw)
                        current_client_bytes[f"{stream_key}:{client_id}"] = max(0, bytes_sent)
                    except Exception:
                        continue
    except Exception:
        pass

    global _proxy_total_ingress_bytes, _proxy_total_egress_bytes
    global _last_proxy_sample_ts, _last_proxy_client_bytes, _last_proxy_stream_ingress_bytes
    global _last_proxy_egress_observed_bytes, _last_proxy_ingress_observed_bytes
    global _last_proxy_ingress_rate_bps, _last_proxy_egress_rate_bps, _last_proxy_rate_ts

    with _proxy_io_lock:
        delta_ts_egress = 0
        for key, current_value in current_client_bytes.items():
            prev_value = _last_proxy_client_bytes.get(key)
            if prev_value is not None and current_value >= prev_value:
                delta_ts_egress += current_value - prev_value

        # If client IDs churn quickly, per-client deltas can be lost; fall back to aggregate deltas.
        if delta_ts_egress == 0 and current_client_bytes and _last_proxy_client_bytes:
            current_total = sum(current_client_bytes.values())
            last_total = sum(_last_proxy_client_bytes.values())
            if current_total > last_total:
                delta_ts_egress = current_total - last_total

        delta_ts_ingress = 0
        for stream_key, current_value in current_stream_ingress_bytes.items():
            prev_value = _last_proxy_stream_ingress_bytes.get(stream_key)
            if prev_value is not None and current_value >= prev_value:
                delta_ts_ingress += current_value - prev_value

        # Fallback for stream-key churn/reindexing where per-key diff may miss positive movement.
        if delta_ts_ingress == 0 and current_stream_ingress_bytes and _last_proxy_stream_ingress_bytes:
            current_total = sum(current_stream_ingress_bytes.values())
            last_total = sum(_last_proxy_stream_ingress_bytes.values())
            if current_total > last_total:
                delta_ts_ingress = current_total - last_total

        observed_delta_ingress = max(0, _proxy_ingress_observed_bytes - _last_proxy_ingress_observed_bytes)
        observed_delta_egress = max(0, _proxy_egress_observed_bytes - _last_proxy_egress_observed_bytes)

        # Prefer direct observed deltas from live data paths. Fall back to Redis-derived
        # deltas when observers are unavailable.
        delta_ingress = observed_delta_ingress if observed_delta_ingress > 0 else delta_ts_ingress
        delta_egress = observed_delta_egress if observed_delta_egress > 0 else delta_ts_egress

        _proxy_total_ingress_bytes += delta_ingress
        _proxy_total_egress_bytes += delta_egress

        ingress_rate_bps = 0.0
        egress_rate_bps = 0.0
        if _last_proxy_sample_ts is not None and now > _last_proxy_sample_ts:
            elapsed = now - _last_proxy_sample_ts
            if elapsed > 0:
                ingress_rate_bps = delta_ingress / elapsed
                egress_rate_bps = delta_egress / elapsed

        # Dashboard polls and Prometheus scrapes can interleave. In those cases, one poll
        # may consume the full delta while the next sees zero immediately after. Keep the
        # last fresh non-zero rate for a short window to avoid false zero spikes.
        rate_hold_seconds = 10.0
        if ingress_rate_bps > 0 or egress_rate_bps > 0:
            _last_proxy_ingress_rate_bps = ingress_rate_bps
            _last_proxy_egress_rate_bps = egress_rate_bps
            _last_proxy_rate_ts = now
        elif _last_proxy_rate_ts is not None and (now - _last_proxy_rate_ts) <= rate_hold_seconds:
            ingress_rate_bps = _last_proxy_ingress_rate_bps
            egress_rate_bps = _last_proxy_egress_rate_bps
        else:
            _last_proxy_ingress_rate_bps = 0.0
            _last_proxy_egress_rate_bps = 0.0
            _last_proxy_rate_ts = None

        _last_proxy_client_bytes = current_client_bytes
        _last_proxy_stream_ingress_bytes = current_stream_ingress_bytes
        _last_proxy_ingress_observed_bytes = _proxy_ingress_observed_bytes
        _last_proxy_egress_observed_bytes = _proxy_egress_observed_bytes
        _last_proxy_sample_ts = now

        return {
            "ingress_total_bytes": float(_proxy_total_ingress_bytes),
            "egress_total_bytes": float(_proxy_total_egress_bytes),
            "ingress_rate_bps": round(max(0.0, ingress_rate_bps), 2),
            "egress_rate_bps": round(max(0.0, egress_rate_bps), 2),
        }


def _persist_dashboard_sample(snapshot: Dict[str, Any]):
    """Persist snapshot sample points for windowed dashboard history."""
    from .db import SessionLocal
    from ..core.config import cfg
    from ..models.db_models import DashboardMetricSampleRow

    global _dashboard_last_persist_ts
    now = time.time()
    if _dashboard_last_persist_ts is not None and (now - _dashboard_last_persist_ts) < cfg.DASHBOARD_PERSIST_INTERVAL_S:
        return

    row = DashboardMetricSampleRow(
        ts=datetime.now(timezone.utc),
        proxy_ingress_rate_bps=float(snapshot.get("proxy", {}).get("throughput", {}).get("ingress_bps", 0.0)),
        proxy_egress_rate_bps=float(snapshot.get("proxy", {}).get("throughput", {}).get("egress_bps", 0.0)),
        active_streams=int(snapshot.get("north_star", {}).get("global_active_streams", 0)),
        active_clients=int(snapshot.get("north_star", {}).get("proxy_active_clients", 0)),
        success_rate_percent=float(snapshot.get("proxy", {}).get("request_window_1m", {}).get("success_rate_percent", 100.0)),
        ttfb_p95_ms=float(snapshot.get("proxy", {}).get("ttfb", {}).get("p95_ms", 0.0)),
        docker_cpu_percent=float(snapshot.get("docker", {}).get("cpu_percent", 0.0)),
        docker_memory_bytes=float(snapshot.get("docker", {}).get("memory_usage", 0.0)),
    )

    try:
        with SessionLocal() as session:
            session.add(row)

            retention_cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg.DASHBOARD_METRICS_RETENTION_HOURS)
            session.query(DashboardMetricSampleRow).filter(DashboardMetricSampleRow.ts < retention_cutoff).delete()
            session.commit()

        _dashboard_last_persist_ts = now
    except Exception:
        # Persistence should not break live metrics path
        return


def _load_dashboard_history(window_seconds: int, max_points: int) -> Dict[str, List[Any]]:
    """Load persisted dashboard metric samples for a given observation window."""
    from .db import SessionLocal
    from ..models.db_models import DashboardMetricSampleRow

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(60, int(window_seconds)))

    try:
        with SessionLocal() as session:
            rows = (
                session.query(DashboardMetricSampleRow)
                .filter(DashboardMetricSampleRow.ts >= cutoff)
                .order_by(DashboardMetricSampleRow.ts.asc())
                .all()
            )
    except Exception:
        rows = []

    if not rows:
        return {
            "timestamps": [],
            "egressMbps": [],
            "ingressMbps": [],
            "activeStreams": [],
            "activeClients": [],
            "successRate": [],
            "ttfbP95Ms": [],
            "cpuPercent": [],
            "memoryBytes": [],
        }

    step = 1
    if max_points > 0 and len(rows) > max_points:
        step = max(1, len(rows) // max_points)
    sampled = rows[::step] if step > 1 else rows

    return {
        "timestamps": [r.ts.isoformat() for r in sampled],
        "egressMbps": [round((float(r.proxy_egress_rate_bps) * 8.0) / 1_000_000.0, 3) for r in sampled],
        "ingressMbps": [round((float(r.proxy_ingress_rate_bps) * 8.0) / 1_000_000.0, 3) for r in sampled],
        "activeStreams": [int(r.active_streams or 0) for r in sampled],
        "activeClients": [int(r.active_clients or 0) for r in sampled],
        "successRate": [float(r.success_rate_percent or 0.0) for r in sampled],
        "ttfbP95Ms": [float(r.ttfb_p95_ms or 0.0) for r in sampled],
        "cpuPercent": [float(r.docker_cpu_percent or 0.0) for r in sampled],
        "memoryBytes": [float(r.docker_memory_bytes or 0.0) for r in sampled],
    }


def _compute_docker_metrics_snapshot() -> Dict[str, float]:
    """Compute Docker USE snapshots and derivative throughput rates."""
    from .docker_stats_collector import docker_stats_collector

    total_stats = docker_stats_collector.get_total_stats() or {}
    cpu_percent = float(total_stats.get("total_cpu_percent", 0.0) or 0.0)
    memory_usage = float(total_stats.get("total_memory_usage", 0) or 0)
    network_rx = int(total_stats.get("total_network_rx_bytes", 0) or 0)
    network_tx = int(total_stats.get("total_network_tx_bytes", 0) or 0)
    block_read = float(total_stats.get("total_block_read_bytes", 0) or 0)
    block_write = float(total_stats.get("total_block_write_bytes", 0) or 0)

    now = time.time()
    rx_rate = 0.0
    tx_rate = 0.0

    global _last_network_rx_bytes, _last_network_tx_bytes, _last_network_sample_ts
    with _docker_rate_lock:
        if _last_network_sample_ts is not None and now > _last_network_sample_ts:
            elapsed = now - _last_network_sample_ts
            if elapsed > 0:
                rx_rate = max(0.0, (network_rx - (_last_network_rx_bytes or 0)) / elapsed)
                tx_rate = max(0.0, (network_tx - (_last_network_tx_bytes or 0)) / elapsed)

        _last_network_rx_bytes = network_rx
        _last_network_tx_bytes = network_tx
        _last_network_sample_ts = now

    restart_total = 0
    oom_killed_total = 0
    try:
        from .docker_client import get_client
        from .state import state

        docker_client = get_client()
        for engine in state.list_engines():
            try:
                container = docker_client.containers.get(engine.container_id)
                container.reload()
                attrs = container.attrs or {}
                restart_total += int(attrs.get("RestartCount", 0) or 0)
                state_info = attrs.get("State", {}) or {}
                if state_info.get("OOMKilled"):
                    oom_killed_total += 1
            except Exception:
                continue
    except Exception:
        pass

    return {
        "cpu_percent": round(cpu_percent, 2),
        "memory_usage": memory_usage,
        "network_rx_bytes": float(network_rx),
        "network_tx_bytes": float(network_tx),
        "network_rx_rate_bps": round(rx_rate, 2),
        "network_tx_rate_bps": round(tx_rate, 2),
        "block_read_bytes": block_read,
        "block_write_bytes": block_write,
        "restart_total": float(restart_total),
        "oom_killed_total": float(oom_killed_total),
    }


def get_dashboard_snapshot(window_seconds: int = 900, max_points: int = 360) -> Dict[str, Any]:
    """Return a structured snapshot used by the pane-based dashboard."""
    from .state import state

    streams = state.list_streams_with_stats(status="started")
    engines = state.list_engines()

    total_speed_up_bps = 0.0
    total_speed_down_bps = 0.0
    total_peers = 0
    buffer_pieces: List[int] = []

    for stream in streams:
        if stream.speed_up is not None:
            total_speed_up_bps += float(stream.speed_up) * 1024.0
        if stream.speed_down is not None:
            total_speed_down_bps += float(stream.speed_down) * 1024.0
        if stream.peers is not None:
            total_peers += int(stream.peers)

        livepos = getattr(stream, "livepos", None)
        if livepos and getattr(livepos, "buffer_pieces", None) is not None:
            try:
                buffer_pieces.append(int(livepos.buffer_pieces))
            except Exception:
                continue

    engines_with_streams = set(stream.container_id for stream in streams)
    now = time.time()
    uptime_values: List[float] = []
    for engine in engines:
        try:
            uptime_values.append(max(0.0, now - engine.first_seen.timestamp()))
        except Exception:
            continue

    engine_state_counts = {
        "playing": len(engines_with_streams),
        "idle": max(0, len(engines) - len(engines_with_streams)),
        "unhealthy": sum(1 for e in engines if e.health_status == "unhealthy"),
        "unknown": sum(1 for e in engines if e.health_status == "unknown"),
    }

    proxy_clients = _compute_proxy_clients_snapshot()
    proxy_window = _compute_proxy_window_snapshot()
    proxy_throughput = _compute_proxy_throughput_snapshot()
    docker_metrics = _compute_docker_metrics_snapshot()

    return {
        "timestamp": int(time.time()),
        "north_star": {
            "global_active_streams": len(streams),
            "global_egress_bandwidth_mbps": round((proxy_throughput["egress_rate_bps"] * 8.0) / 1_000_000.0, 3),
            "system_success_rate_percent": proxy_window["success_rate_percent"],
            "proxy_active_clients": proxy_clients["total"],
        },
        "proxy": {
            "active_clients": proxy_clients,
            "request_window_1m": proxy_window,
            "throughput": {
                "ingress_mbps": round((proxy_throughput["ingress_rate_bps"] * 8.0) / 1_000_000.0, 3),
                "egress_mbps": round((proxy_throughput["egress_rate_bps"] * 8.0) / 1_000_000.0, 3),
                "ingress_bps": proxy_throughput["ingress_rate_bps"],
                "egress_bps": proxy_throughput["egress_rate_bps"],
                "ingress_total_bytes": proxy_throughput["ingress_total_bytes"],
                "egress_total_bytes": proxy_throughput["egress_total_bytes"],
            },
            "ttfb": {
                "avg_ms": proxy_window["ttfb_avg_ms"],
                "p95_ms": proxy_window["ttfb_p95_ms"],
            },
        },
        "engines": {
            "total": len(engines),
            "healthy": sum(1 for e in engines if e.health_status == "healthy"),
            "unhealthy": sum(1 for e in engines if e.health_status == "unhealthy"),
            "used": len(engines_with_streams),
            "state_counts": engine_state_counts,
            "uptime_avg_seconds": round(sum(uptime_values) / len(uptime_values), 2) if uptime_values else 0.0,
        },
        "streams": {
            "active": len(streams),
            "total_peers": total_peers,
            "download_speed_mbps": round(total_speed_down_bps / (1024.0 * 1024.0), 3),
            "upload_speed_mbps": round(total_speed_up_bps / (1024.0 * 1024.0), 3),
            "buffer": {
                "avg_pieces": round(sum(buffer_pieces) / len(buffer_pieces), 2) if buffer_pieces else 0.0,
                "min_pieces": float(min(buffer_pieces)) if buffer_pieces else 0.0,
            },
            "active_keys": sorted({s.key for s in streams if s.key}),
        },
        "docker": docker_metrics,
        "observation_window_seconds": int(window_seconds),
    }


def on_stream_stat_update(stream_id: str, uploaded: Optional[int], downloaded: Optional[int]):
    """
    Update cumulative byte totals when new stream stats arrive.
    Calculates deltas from last known values and adds to cumulative totals.
    
    Args:
        stream_id: Unique stream identifier
        uploaded: Current total bytes uploaded for this stream
        downloaded: Current total bytes downloaded for this stream
    """
    global _cumulative_uploaded_bytes, _cumulative_downloaded_bytes
    
    with _cumulative_lock:
        # Get or initialize last known values for this stream
        if stream_id not in _stream_last_values:
            _stream_last_values[stream_id] = {'uploaded': None, 'downloaded': None}
        
        last_values = _stream_last_values[stream_id]
        
        # Calculate and add uploaded delta
        if uploaded is not None:
            if last_values['uploaded'] is not None:
                delta = uploaded - last_values['uploaded']
                if delta > 0:  # Only add positive deltas (handle counter resets gracefully)
                    _cumulative_uploaded_bytes += delta
            else:
                # First time seeing this stream, add the initial value
                _cumulative_uploaded_bytes += uploaded
            last_values['uploaded'] = uploaded
        
        # Calculate and add downloaded delta
        if downloaded is not None:
            if last_values['downloaded'] is not None:
                delta = downloaded - last_values['downloaded']
                if delta > 0:  # Only add positive deltas (handle counter resets gracefully)
                    _cumulative_downloaded_bytes += delta
            else:
                # First time seeing this stream, add the initial value
                _cumulative_downloaded_bytes += downloaded
            last_values['downloaded'] = downloaded


def on_stream_ended(stream_id: str):
    """
    Clean up tracking data when a stream ends.
    The cumulative totals are already updated, so we just remove the tracking entry.
    
    Args:
        stream_id: Unique stream identifier
    """
    with _cumulative_lock:
        # Remove the stream's last known values
        _stream_last_values.pop(stream_id, None)


def reset_cumulative_metrics():
    """
    Reset cumulative byte tracking. Used for testing or system resets.
    """
    global _cumulative_uploaded_bytes, _cumulative_downloaded_bytes
    
    with _cumulative_lock:
        _cumulative_uploaded_bytes = 0
        _cumulative_downloaded_bytes = 0
        _stream_last_values.clear()


def update_custom_metrics(window_seconds: int = 900, max_points: int = 360) -> Dict[str, Any]:
    """
    Update custom Prometheus metrics with aggregated data from all engines.
    
    This function collects data from all engines and updates the following metrics:
    - orch_total_uploaded_bytes: Cumulative bytes uploaded from all engines (all-time total)
    - orch_total_downloaded_bytes: Cumulative bytes downloaded from all engines (all-time total)
    - orch_total_upload_speed_mbps: Current sum of upload speeds in MB/s
    - orch_total_download_speed_mbps: Current sum of download speeds in MB/s
    - orch_total_peers: Current total peers across all engines
    - orch_total_streams: Current number of active streams
    - orch_healthy_engines: Number of healthy engines
    - orch_unhealthy_engines: Number of unhealthy engines
    - orch_used_engines: Number of engines currently handling streams
    - orch_vpn_health: Current health status of VPN container
    - orch_extra_engines: Number of engines beyond MIN_REPLICAS
    - orch_performance_operation_*: Performance metrics for key operations (last 5 minutes)
      - count: Number of samples
      - avg_ms: Average duration in milliseconds
      - p50_ms, p95_ms, p99_ms: Percentile durations
      - min_ms, max_ms: Min/max durations
      - success_rate: Success rate percentage
    """
    from .state import state
    from .gluetun import get_vpn_status
    from ..core.config import cfg
    
    # Get all active streams with their latest stats
    streams = state.list_streams_with_stats(status="started")
    
    # Aggregate instantaneous stats from active streams
    # (speeds and peers are point-in-time values)
    # Note: AceStream API returns speeds in KB/s, so we need to convert to bytes/s first
    total_speed_up = 0  # bytes/s
    total_speed_down = 0  # bytes/s
    total_peers = 0
    
    for stream in streams:
        if stream.speed_up is not None:
            # Convert from KB/s to bytes/s
            total_speed_up += stream.speed_up * 1024
        if stream.speed_down is not None:
            # Convert from KB/s to bytes/s
            total_speed_down += stream.speed_down * 1024
        if stream.peers is not None:
            total_peers += stream.peers
    
    # Convert speeds from bytes/s to MB/s
    total_upload_speed_mbps = round(total_speed_up / (1024 * 1024), 2) if total_speed_up > 0 else 0.0
    total_download_speed_mbps = round(total_speed_down / (1024 * 1024), 2) if total_speed_down > 0 else 0.0
    
    # Get engine counts
    engines = state.list_engines()
    total_engines = len(engines)
    healthy_engines = sum(1 for e in engines if e.health_status == "healthy")
    unhealthy_engines = sum(1 for e in engines if e.health_status == "unhealthy")
    
    # Count engines with active streams (used engines)
    engines_with_streams = set(stream.container_id for stream in streams)
    used_engines = len(engines_with_streams)
    
    # Get VPN health status
    vpn_status = get_vpn_status()
    vpn_health_str = vpn_status.get("health", "unknown")
    if not vpn_status.get("enabled", False):
        vpn_health_str = "disabled"
    
    # Calculate extra engines (beyond minimum)
    extra_engines = max(0, total_engines - cfg.MIN_REPLICAS)
    
    # Get cumulative byte totals (from all streams, active and ended)
    with _cumulative_lock:
        cumulative_uploaded = _cumulative_uploaded_bytes
        cumulative_downloaded = _cumulative_downloaded_bytes
    
    # Convert bytes to MB for human-readable metrics
    cumulative_uploaded_mb = round(cumulative_uploaded / (1024 * 1024), 2) if cumulative_uploaded > 0 else 0.0
    cumulative_downloaded_mb = round(cumulative_downloaded / (1024 * 1024), 2) if cumulative_downloaded > 0 else 0.0
    
    # Update all metrics
    orch_total_uploaded_bytes.set(cumulative_uploaded)
    orch_total_downloaded_bytes.set(cumulative_downloaded)
    orch_total_uploaded_mb.set(cumulative_uploaded_mb)
    orch_total_downloaded_mb.set(cumulative_downloaded_mb)
    orch_total_upload_speed_mbps.set(total_upload_speed_mbps)
    orch_total_download_speed_mbps.set(total_download_speed_mbps)
    orch_total_peers.set(total_peers)
    orch_total_streams.set(len(streams))
    orch_healthy_engines.set(healthy_engines)
    orch_unhealthy_engines.set(unhealthy_engines)
    orch_used_engines.set(used_engines)
    orch_vpn_health.state(vpn_health_str)
    
    # Update VPN-specific metrics for redundant mode
    if cfg.VPN_MODE == 'redundant':
        # Get individual VPN statuses
        vpn1_status = vpn_status.get("vpn1", {})
        vpn2_status = vpn_status.get("vpn2", {})
        
        vpn1_health = vpn1_status.get("health", "unknown")
        vpn2_health = vpn2_status.get("health", "unknown")
        
        orch_vpn1_health.state(vpn1_health)
        orch_vpn2_health.state(vpn2_health)
        
        # Count engines per VPN
        vpn1_engine_count = sum(1 for e in engines if e.vpn_container == cfg.GLUETUN_CONTAINER_NAME)
        vpn2_engine_count = sum(1 for e in engines if e.vpn_container == cfg.GLUETUN_CONTAINER_NAME_2)
        
        orch_vpn1_engines.set(vpn1_engine_count)
        orch_vpn2_engines.set(vpn2_engine_count)
    else:
        # Single VPN mode - set VPN1 metrics, VPN2 to 0
        orch_vpn1_health.state(vpn_health_str)
        orch_vpn2_health.state("disabled")
        orch_vpn1_engines.set(total_engines if cfg.GLUETUN_CONTAINER_NAME else 0)
        orch_vpn2_engines.set(0)
    
    orch_extra_engines.set(extra_engines)

    # Engine state and uptime metrics
    now = time.time()
    uptime_seconds: List[float] = []
    for engine in engines:
        try:
            uptime_seconds.append(max(0.0, now - engine.first_seen.timestamp()))
        except Exception:
            continue

    engine_state_values = {
        "playing": float(used_engines),
        "idle": float(max(0, total_engines - used_engines)),
        "unhealthy": float(unhealthy_engines),
        "unknown": float(sum(1 for e in engines if e.health_status == "unknown")),
    }
    for state_name, value in engine_state_values.items():
        orch_engine_state_count.labels(state=state_name).set(value)

    orch_engine_uptime_avg_seconds.set((sum(uptime_seconds) / len(uptime_seconds)) if uptime_seconds else 0.0)

    # Stream buffer health metrics
    buffer_pieces: List[int] = []
    for stream in streams:
        livepos = getattr(stream, "livepos", None)
        if livepos and getattr(livepos, "buffer_pieces", None) is not None:
            try:
                buffer_pieces.append(int(livepos.buffer_pieces))
            except Exception:
                continue

    orch_stream_buffer_pieces_avg.set((sum(buffer_pieces) / len(buffer_pieces)) if buffer_pieces else 0.0)
    orch_stream_buffer_pieces_min.set(min(buffer_pieces) if buffer_pieces else 0.0)

    # Active stream key gauges
    active_keys = {s.key for s in streams if s.key}
    global _active_stream_keys
    for stale_key in (_active_stream_keys - active_keys):
        orch_active_infohash.labels(stream_key=stale_key).set(0)
    for active_key in active_keys:
        orch_active_infohash.labels(stream_key=active_key).set(1)
    _active_stream_keys = active_keys

    # Proxy and Docker advanced metrics
    proxy_clients = _compute_proxy_clients_snapshot()
    proxy_window = _compute_proxy_window_snapshot()
    proxy_throughput = _compute_proxy_throughput_snapshot()
    docker_metrics = _compute_docker_metrics_snapshot()

    orch_proxy_active_clients.set(proxy_clients["total"])
    orch_proxy_active_clients_ts.set(proxy_clients["ts"])
    orch_proxy_active_clients_hls.set(proxy_clients["hls"])
    orch_proxy_disconnect_rate_per_minute.set(proxy_window["disconnect_rate_per_min"])
    orch_proxy_success_rate.set(proxy_window["success_rate_percent"])
    orch_proxy_4xx_rate_per_minute.set(proxy_window["error_4xx_rate_per_min"])
    orch_proxy_5xx_rate_per_minute.set(proxy_window["error_5xx_rate_per_min"])
    orch_proxy_ttfb_avg_ms.set(proxy_window["ttfb_avg_ms"])
    orch_proxy_ttfb_p95_ms.set(proxy_window["ttfb_p95_ms"])
    orch_proxy_ingress_bytes_total.set(proxy_throughput["ingress_total_bytes"])
    orch_proxy_egress_bytes_total.set(proxy_throughput["egress_total_bytes"])
    orch_proxy_ingress_rate_bps.set(proxy_throughput["ingress_rate_bps"])
    orch_proxy_egress_rate_bps.set(proxy_throughput["egress_rate_bps"])

    orch_docker_total_cpu_percent.set(docker_metrics["cpu_percent"])
    orch_docker_total_memory_bytes.set(docker_metrics["memory_usage"])
    orch_docker_network_rx_bytes_total.set(docker_metrics["network_rx_bytes"])
    orch_docker_network_tx_bytes_total.set(docker_metrics["network_tx_bytes"])
    orch_docker_network_rx_rate_bps.set(docker_metrics["network_rx_rate_bps"])
    orch_docker_network_tx_rate_bps.set(docker_metrics["network_tx_rate_bps"])
    orch_docker_block_read_bytes_total.set(docker_metrics["block_read_bytes"])
    orch_docker_block_write_bytes_total.set(docker_metrics["block_write_bytes"])
    orch_docker_restart_total.set(docker_metrics["restart_total"])
    orch_docker_oom_killed_total.set(docker_metrics["oom_killed_total"])

    # North-star metrics
    global_egress_mbps = (proxy_throughput["egress_rate_bps"] * 8.0) / 1_000_000.0
    orch_global_egress_bandwidth_mbps.set(global_egress_mbps)
    orch_system_success_rate.set(proxy_window["success_rate_percent"])
    
    # Update performance metrics
    # Import here to avoid circular dependency (performance_metrics imports from other modules)
    from .performance_metrics import performance_metrics
    
    # Get stats for all tracked operations (last 5 minutes)
    perf_stats = performance_metrics.get_all_stats(window_seconds=300)
    
    # Update metrics for each operation
    for operation, stats in perf_stats.items():
        orch_performance_count.labels(operation=operation).set(stats['count'])
        orch_performance_avg_ms.labels(operation=operation).set(stats['avg_ms'])
        orch_performance_p50_ms.labels(operation=operation).set(stats['p50_ms'])
        orch_performance_p95_ms.labels(operation=operation).set(stats['p95_ms'])
        orch_performance_p99_ms.labels(operation=operation).set(stats['p99_ms'])
        orch_performance_min_ms.labels(operation=operation).set(stats['min_ms'])
        orch_performance_max_ms.labels(operation=operation).set(stats['max_ms'])
        orch_performance_success_rate.labels(operation=operation).set(stats['success_rate'])

    snapshot = get_dashboard_snapshot(window_seconds=window_seconds, max_points=max_points)
    _persist_dashboard_sample(snapshot)
    snapshot["history"] = _load_dashboard_history(window_seconds=window_seconds, max_points=max_points)
    return snapshot
