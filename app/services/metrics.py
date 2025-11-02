from prometheus_client import Counter, Gauge, make_asgi_app

# Keep old Prometheus metrics for backward compatibility in code
orch_events_started = Counter("orch_events_started_total", "stream_started events")
orch_events_ended = Counter("orch_events_ended_total", "stream_ended events")
orch_collect_errors = Counter("orch_collector_errors_total", "collector errors")
orch_stale_streams_detected = Counter("orch_stale_streams_detected_total", "stale streams detected and auto-ended")
orch_streams_active = Gauge("orch_streams_active", "active streams")
orch_provision_total = Counter("orch_provision_total", "provision requests", ["kind"])

metrics_app = make_asgi_app()


def get_custom_metrics() -> dict:
    """
    Generate custom metrics aggregated from all engines.
    
    Returns a dictionary with:
    - total_uploaded: Total bytes uploaded from all engines
    - total_downloaded: Total bytes downloaded from all engines
    - total_upload_speed_mbps: Current sum of upload speeds in MB/s
    - total_download_speed_mbps: Current sum of download speeds in MB/s
    - total_peers: Current total peers across all engines
    - total_streams: Current number of active streams
    - healthy_engines: Number of healthy engines
    - unhealthy_engines: Number of unhealthy engines
    - used_engines: Number of engines currently handling streams
    - vpn_health: Current health status of VPN container
    - extra_engines: Number of engines beyond MIN_REPLICAS
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
    vpn_health = vpn_status.get("health", "unknown")
    
    # Calculate extra engines (beyond minimum)
    extra_engines = max(0, total_engines - cfg.MIN_REPLICAS)
    
    return {
        "total_uploaded": total_uploaded,
        "total_downloaded": total_downloaded,
        "total_upload_speed_mbps": total_upload_speed_mbps,
        "total_download_speed_mbps": total_download_speed_mbps,
        "total_peers": total_peers,
        "total_streams": len(streams),
        "healthy_engines": healthy_engines,
        "unhealthy_engines": unhealthy_engines,
        "used_engines": used_engines,
        "vpn_health": vpn_health,
        "extra_engines": extra_engines
    }
