from prometheus_client import Counter, Gauge, make_asgi_app, Enum

# Keep old internal metrics for backward compatibility in code
orch_events_started = Counter("orch_events_started_total", "stream_started events")
orch_events_ended = Counter("orch_events_ended_total", "stream_ended events")
orch_collect_errors = Counter("orch_collector_errors_total", "collector errors")
orch_stale_streams_detected = Counter("orch_stale_streams_detected_total", "stale streams detected and auto-ended")
orch_streams_active = Gauge("orch_streams_active", "active streams")
orch_provision_total = Counter("orch_provision_total", "provision requests", ["kind"])

# New aggregated metrics replacing the old metrics
orch_total_uploaded_bytes = Gauge("orch_total_uploaded_bytes", "Total bytes uploaded from all engines")
orch_total_downloaded_bytes = Gauge("orch_total_downloaded_bytes", "Total bytes downloaded from all engines")
orch_total_upload_speed_mbps = Gauge("orch_total_upload_speed_mbps", "Current sum of upload speeds from all engines in MB/s")
orch_total_download_speed_mbps = Gauge("orch_total_download_speed_mbps", "Current sum of download speeds from all engines in MB/s")
orch_total_peers = Gauge("orch_total_peers", "Current total peers across all engines")
orch_total_streams = Gauge("orch_total_streams", "Current number of active streams")
orch_healthy_engines = Gauge("orch_healthy_engines", "Number of healthy engines")
orch_unhealthy_engines = Gauge("orch_unhealthy_engines", "Number of unhealthy engines")
orch_used_engines = Gauge("orch_used_engines", "Number of engines currently handling streams")
orch_vpn_health = Enum("orch_vpn_health", "Current health status of VPN container", states=["healthy", "unhealthy", "unknown", "disabled"])
orch_extra_engines = Gauge("orch_extra_engines", "Number of engines beyond MIN_REPLICAS")

metrics_app = make_asgi_app()


def update_custom_metrics():
    """
    Update custom Prometheus metrics with aggregated data from all engines.
    
    This function collects data from all engines and updates the following metrics:
    - orch_total_uploaded_bytes: Total bytes uploaded from all engines
    - orch_total_downloaded_bytes: Total bytes downloaded from all engines
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
    
    # Aggregate stats from all streams
    total_uploaded = 0
    total_downloaded = 0
    total_speed_up = 0  # bytes/s
    total_speed_down = 0  # bytes/s
    total_peers = 0
    
    for stream in streams:
        if stream.uploaded:
            total_uploaded += stream.uploaded
        if stream.downloaded:
            total_downloaded += stream.downloaded
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
    
    # Update all metrics
    orch_total_uploaded_bytes.set(total_uploaded)
    orch_total_downloaded_bytes.set(total_downloaded)
    orch_total_upload_speed_mbps.set(total_upload_speed_mbps)
    orch_total_download_speed_mbps.set(total_download_speed_mbps)
    orch_total_peers.set(total_peers)
    orch_total_streams.set(len(streams))
    orch_healthy_engines.set(healthy_engines)
    orch_unhealthy_engines.set(unhealthy_engines)
    orch_used_engines.set(used_engines)
    orch_vpn_health.state(vpn_health_str)
    orch_extra_engines.set(extra_engines)
