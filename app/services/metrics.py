from prometheus_client import Counter, Gauge, make_asgi_app, Enum
import threading
from typing import Dict, Optional

# Stale stream detection metric
orch_stale_streams_detected = Counter("orch_stale_streams_detected_total", "stale streams detected and auto-ended")

# Aggregated metrics from all engines
orch_total_uploaded_bytes = Gauge("orch_total_uploaded_bytes", "Total bytes uploaded from all engines")
orch_total_downloaded_bytes = Gauge("orch_total_downloaded_bytes", "Total bytes downloaded from all engines")
orch_total_upload_speed_mbps = Gauge("orch_total_upload_speed_mbps", "Current sum of upload speeds from all engines in MB/s")
orch_total_download_speed_mbps = Gauge("orch_total_download_speed_mbps", "Current sum of download speeds from all engines in MB/s")
orch_total_peers = Gauge("orch_total_peers", "Current total peers across all engines")
orch_total_streams = Gauge("orch_total_streams", "Current number of active streams")
orch_healthy_engines = Gauge("orch_healthy_engines", "Number of healthy engines")
orch_unhealthy_engines = Gauge("orch_unhealthy_engines", "Number of unhealthy engines")
orch_used_engines = Gauge("orch_used_engines", "Number of engines currently handling streams")
orch_vpn_health = Enum("orch_vpn_health", "Current health status of VPN container", states=["healthy", "unhealthy", "unknown", "disabled", "starting"])
orch_extra_engines = Gauge("orch_extra_engines", "Number of engines beyond MIN_REPLICAS")

metrics_app = make_asgi_app()

# Cumulative byte tracking across all streams (active and ended)
# Protected by lock for thread-safety
_cumulative_lock = threading.Lock()
_cumulative_uploaded_bytes = 0
_cumulative_downloaded_bytes = 0
_stream_last_values: Dict[str, Dict[str, Optional[int]]] = {}  # stream_id -> {uploaded, downloaded}


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


def update_custom_metrics():
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
    """
    from .state import state
    from .gluetun import get_vpn_status
    from ..core.config import cfg
    
    # Get all active streams with their latest stats
    streams = state.list_streams_with_stats(status="started")
    
    # Aggregate instantaneous stats from active streams
    # (speeds and peers are point-in-time values)
    total_speed_up = 0  # bytes/s
    total_speed_down = 0  # bytes/s
    total_peers = 0
    
    for stream in streams:
        if stream.speed_up:
            total_speed_up += stream.speed_up
        if stream.speed_down:
            total_speed_down += stream.speed_down
        if stream.peers:
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
    
    # Update all metrics
    orch_total_uploaded_bytes.set(cumulative_uploaded)
    orch_total_downloaded_bytes.set(cumulative_downloaded)
    orch_total_upload_speed_mbps.set(total_upload_speed_mbps)
    orch_total_download_speed_mbps.set(total_download_speed_mbps)
    orch_total_peers.set(total_peers)
    orch_total_streams.set(len(streams))
    orch_healthy_engines.set(healthy_engines)
    orch_unhealthy_engines.set(unhealthy_engines)
    orch_used_engines.set(used_engines)
    orch_vpn_health.state(vpn_health_str)
    orch_extra_engines.set(extra_engines)
