# Gluetun VPN Failure and Recovery Scenarios

This document describes the failure and recovery scenarios for Gluetun VPN integration with the AceStream Orchestrator.

## Table of Contents

- [Scenario 1: VPN Connection Loss](#scenario-1-vpn-connection-loss)
- [Scenario 2: Gluetun Container Restart](#scenario-2-gluetun-container-restart)
- [Scenario 3: Redundant VPN Failover](#scenario-3-redundant-vpn-failover)
- [Scenario 4: Port Forwarding Loss](#scenario-4-port-forwarding-loss)
- [Monitoring and Alerts](#monitoring-and-alerts)

---

## Scenario 1: VPN Connection Loss

### Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    VPN Connection Loss                      │
└─────────────────────────────────────────────────────────────┘

Time: 0s                        30s                60s+
┌──────────────────────────┐   ┌──────────────┐   ┌──────────────┐
│   Gluetun: Healthy       │   │  Unhealthy   │   │   Healthy    │
│   Engines: Active        │   │  Engines:    │   │   Engines:   │
│   Streams: Playing       │──▶│  Hidden      │──▶│   Restarted  │
│                          │   │  Streams:    │   │   Streams:   │
│                          │   │  Continue    │   │   Playing    │
└──────────────────────────┘   └──────────────┘   └──────────────┘

        │                           │                   │
        │                           │                   │
        ▼                           ▼                   ▼
┌──────────────┐           ┌──────────────┐    ┌──────────────┐
│ VPN Provider │           │  VPN Down    │    │ VPN Restored │
│  Connected   │           │  Reconnect   │    │  Connected   │
└──────────────┘           └──────────────┘    └──────────────┘
```

### Sequence of Events

1. **Detection (0-5s)**
   - Gluetun health check fails
   - Orchestrator detects unhealthy status
   - Double-checks via engine network connectivity
   - Confirms VPN is down

2. **Immediate Response (5-10s)**
   - Engines using this VPN are hidden from `/engines` endpoint
   - Active streams continue without interruption (using existing connections)
   - New stream requests are blocked from using this VPN
   - Orchestrator logs: `Gluetun VPN became unhealthy`

3. **VPN Reconnection (10-30s)**
   - Gluetun attempts to reconnect to VPN provider
   - Health checks continue every 5 seconds
   - If unhealthy for >60s, orchestrator triggers Docker restart

4. **Recovery (30-60s)**
   - Gluetun health check passes
   - Orchestrator detects recovery: `Gluetun VPN recovered and is now healthy`
   - If `VPN_RESTART_ENGINES_ON_RECONNECT=true`:
     - All engines assigned to this VPN are restarted
     - Ensures engines bind to new VPN IP address
   - Engines become available again in `/engines` endpoint

5. **Post-Recovery**
   - Autoscaler ensures `MIN_REPLICAS` are maintained
   - New streams can be assigned to recovered engines
   - System returns to normal operation

### Configuration

```bash
# .env
GLUETUN_HEALTH_CHECK_INTERVAL_S=5          # Fast detection
VPN_RESTART_ENGINES_ON_RECONNECT=true      # Restart on recovery
VPN_UNHEALTHY_RESTART_TIMEOUT_S=60         # Force restart after 60s
```

### Impact

- **Active Streams**: Continue playing (using existing TCP connections)
- **New Streams**: Cannot be assigned to engines on failed VPN
- **Engine Availability**: Engines hidden until VPN recovers
- **User Experience**: Minimal disruption for active streams

---

## Scenario 2: Gluetun Container Restart

### Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                  Gluetun Container Restart                  │
└─────────────────────────────────────────────────────────────┘

Time: 0s                    30s                120s+
┌──────────────────────┐   ┌──────────────┐   ┌──────────────┐
│ Container: Running   │   │ Stopped      │   │  Running     │
│ Engines: Active      │──▶│ Engines:     │──▶│  Engines:    │
│                      │   │ Stopped      │   │  Restarted   │
└──────────────────────┘   └──────────────┘   └──────────────┘

        │                       │                   │
        │                       │                   │
        ▼                       ▼                   ▼
┌──────────────┐        ┌──────────────┐    ┌──────────────┐
│ Manual/Auto  │        │  Container   │    │  VPN Ready   │
│   Restart    │        │  Restarting  │    │ Health Check │
└──────────────┘        └──────────────┘    └──────────────┘
```

### Sequence of Events

1. **Container Stop (0s)**
   - Gluetun container stopped (manual or crash)
   - All engines using `network_mode: container:gluetun` lose network
   - Orchestrator detects container is not running
   - Status changes to: `"status": "stopped"`

2. **Engine Impact (0-5s)**
   - Engines lose all network connectivity
   - Active streams immediately fail
   - Engines cannot be reached by orchestrator or proxies
   - Engines are marked as unhealthy

3. **Container Restart (5-60s)**
   - Docker restarts Gluetun (if `restart: unless-stopped`)
   - Container goes through startup process:
     - Initialize network interfaces
     - Connect to VPN provider
     - Establish port forwarding (if configured)
   - Health check starts after `start_period` (default: 60s)

4. **VPN Establishment (60-90s)**
   - Gluetun establishes VPN connection
   - Health check passes: `test: ["CMD", "wget", "-q", "--spider", "http://cloudflare.com"]`
   - Orchestrator detects healthy status
   - Port forwarding is re-established

5. **Engine Recovery (90-120s)**
   - If `VPN_RESTART_ENGINES_ON_RECONNECT=true`:
     - Orchestrator restarts all engines
     - Engines reconnect to Gluetun's network
   - If false:
     - Engines may need manual restart
   - Autoscaler provisions new engines if needed

6. **Service Restoration (120s+)**
   - Engines become available in `/engines` endpoint
   - New streams can be assigned
   - System returns to normal operation

### Configuration

```bash
# docker-compose.yml
gluetun:
  restart: unless-stopped                    # Auto-restart on failure
  healthcheck:
    start_period: 60s                        # Wait before health checks
    interval: 30s                            # Check every 30s
    retries: 3                               # Fail after 3 attempts
```

### Impact

- **Active Streams**: All fail immediately (network loss)
- **Engine Availability**: All engines unavailable until VPN recovers
- **Recovery Time**: 90-120 seconds typical
- **Data Loss**: Active stream progress lost

---

## Scenario 3: Redundant VPN Failover

### Diagram

```
┌─────────────────────────────────────────────────────────────┐
│              Redundant VPN Mode - Failover                  │
└─────────────────────────────────────────────────────────────┘

VPN1: Healthy                    VPN1: FAILED           VPN1: Recovering
VPN2: Healthy                    VPN2: Healthy          VPN2: Healthy
┌──────────────────────┐        ┌────────────────┐     ┌──────────────────┐
│ Engines on VPN1: 50% │        │ Hidden         │     │ Restarted        │
│ Engines on VPN2: 50% │───────▶│ Active         │────▶│ Active           │
│                      │        │                │     │                  │
│ Load: Balanced       │        │ Load: All VPN2 │     │ Load: Rebalanced │
└──────────────────────┘        └────────────────┘     └──────────────────┘

┌────────────────────────────────────────────────────────────────┐
│                    Load Distribution                           │
├────────────────────────────────────────────────────────────────┤
│ Normal:    [VPN1: ████████] [VPN2: ████████]                 │
│ Failover:  [VPN1: --------] [VPN2: ████████████████]         │
│ Recovery:  [VPN1: ████████] [VPN2: ████████]                 │
└────────────────────────────────────────────────────────────────┘
```

### Sequence of Events

1. **Normal Operation**
   - Both VPN containers healthy
   - Engines distributed 50/50 via round-robin
   - Each VPN has one "forwarded" engine for P2P
   - Load balanced across both VPNs

2. **VPN1 Fails (0-5s)**
   - VPN1 health check fails
   - Orchestrator detects failure immediately
   - Engines on VPN1 hidden from `/engines` endpoint
   - Streams on VPN1 continue (existing connections)

3. **Traffic Shift (5-30s)**
   - All new stream requests go to VPN2 engines only
   - VPN2 handles 100% of new traffic
   - Active streams on VPN1 continue until they naturally end
   - No new engines provisioned on VPN1

4. **VPN1 Recovery Attempt (30-60s)**
   - Orchestrator monitors VPN1 health continuously
   - If unhealthy >60s, triggers Docker restart of VPN1
   - VPN1 begins reconnection process

5. **VPN1 Recovery (60-120s)**
   - VPN1 establishes new connection
   - Health checks pass
   - Orchestrator detects: `Gluetun VPN recovered and is now healthy`
   - If `VPN_RESTART_ENGINES_ON_RECONNECT=true`:
     - Engines on VPN1 are restarted
     - Reconnect to new VPN1 IP

6. **Load Rebalancing (120s+)**
   - New engines provisioned on VPN1
   - Round-robin distribution resumes
   - Load gradually returns to 50/50
   - System fully recovered

### Configuration

```bash
# .env - Redundant VPN Mode
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME=gluetun1
GLUETUN_CONTAINER_NAME_2=gluetun2
GLUETUN_PORT_RANGE_1=19000-19499        # VPN1 port range
GLUETUN_PORT_RANGE_2=19500-19999        # VPN2 port range
VPN_UNHEALTHY_RESTART_TIMEOUT_S=60
```

### Benefits

- **Zero Downtime**: Active streams continue without interruption
- **Automatic Failover**: No manual intervention required
- **Load Distribution**: Healthy VPN absorbs all traffic
- **Graceful Recovery**: Failed VPN rejoins automatically

### Impact

- **Active Streams on Failed VPN**: Continue until natural end
- **New Streams**: Only assigned to healthy VPN
- **Engine Capacity**: Reduced by ~50% during single VPN operation
- **Recovery Time**: Transparent to users

---

## Scenario 4: Port Forwarding Loss

### Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                 Port Forwarding Loss                        │
└─────────────────────────────────────────────────────────────┘

Time: 0s                       30s                  60s+
┌─────────────────────┐    ┌──────────────────┐   ┌────────────────┐
│ Forwarded Port: OK  │    │ Port Lost        │   │ Port Restored  │
│ P2P: Optimal        │───▶│ P2P: Degraded    │──▶│ P2P: Optimal   │
│                     │    │                  │   │                │
└─────────────────────┘    └──────────────────┘   └────────────────┘

P2P Performance:
┌────────────────────────────────────────────────────────────────┐
│ Normal:    [Peers: ████████████] Download: ████████████       │
│ Degraded:  [Peers: ████-----] Download: ████----              │
│ Restored:  [Peers: ████████████] Download: ████████████       │
└────────────────────────────────────────────────────────────────┘
```

### Sequence of Events

1. **Port Forwarding Active (0s)**
   - Gluetun provides forwarded port (e.g., 65432)
   - One engine marked as "forwarded" with P2P port
   - Optimal P2P connectivity and peer count
   - Best download speeds

2. **Port Forwarding Loss (0-5s)**
   - VPN provider changes port or disables forwarding
   - Gluetun API returns null forwarded port
   - Orchestrator detects via: `GET /v1/openvpn/portforwarded`
   - Forwarded engine still functional but P2P degraded

3. **Performance Impact (5-30s)**
   - Forwarded engine loses P2P optimization
   - Fewer peer connections established
   - Download speeds may decrease
   - Streams still functional (HTTP streaming continues)

4. **Port Recovery Attempt (30-60s)**
   - Gluetun attempts to re-establish port forwarding
   - May require VPN reconnection
   - Orchestrator polls Gluetun API every 60s (port cache TTL)

5. **Port Restoration (60s+)**
   - New forwarded port obtained from VPN provider
   - Orchestrator updates forwarded engine with new port
   - May require engine restart to bind new port
   - P2P connectivity restored

### Configuration

```bash
# .env
GLUETUN_PORT_CACHE_TTL_S=60              # Poll for new port every 60s
VPN_RESTART_ENGINES_ON_RECONNECT=true    # Restart to rebind port
```

### Impact

- **Streaming**: Continues without interruption (HTTP doesn't use P2P port)
- **P2P Performance**: Degraded but not broken
- **Peer Connectivity**: Reduced peer count
- **Download Speeds**: May decrease by 20-50%

### Mitigation

1. **Use Reliable VPN Provider**: Choose providers with stable port forwarding
2. **Monitor Forwarded Port**: Track via `/vpn/status` endpoint
3. **Alert on Loss**: Set up monitoring for forwarded port null
4. **Quick Recovery**: Enable engine restart on VPN reconnect

---

## Monitoring and Alerts

### Key Metrics to Monitor

1. **VPN Health Status**
   ```bash
   curl http://localhost:8000/vpn/status
   ```
   Monitor: `health`, `connected`, `forwarded_port`

2. **Engine Availability**
   ```bash
   curl http://localhost:8000/engines
   ```
   Monitor: Count of healthy engines per VPN

3. **Prometheus Metrics**
   ```
   orch_vpn_health_status{vpn="gluetun"}
   orch_engines_total{vpn_container="gluetun"}
   orch_streams_active{vpn_container="gluetun"}
   ```

### Alert Thresholds

- **Critical**: VPN down for >60 seconds
- **Warning**: VPN unhealthy for >30 seconds
- **Info**: Port forwarding lost
- **Info**: Engine restart triggered

### Log Messages

```
# VPN Failure
WARN: Gluetun VPN became unhealthy
INFO: Hiding 5 engines assigned to unhealthy VPN

# VPN Recovery
INFO: Gluetun VPN recovered and is now healthy
INFO: Restarting 5 engines on recovered VPN

# Redundant Failover
INFO: VPN1 failed, all traffic routed to VPN2
INFO: VPN1 recovered, resuming round-robin distribution

# Port Forwarding
WARN: Forwarded port lost, P2P performance degraded
INFO: New forwarded port obtained: 54321
```

### Dashboard Indicators

- **VPN Status Card**: Shows real-time health and connection status
- **Engine Health**: Color-coded indicators (green/red/gray)
- **Last Check**: Timestamp of most recent health verification
- **Forwarded Port**: Current port or "Not Available"

---

## Best Practices

1. **Use Redundant VPN Mode** for mission-critical deployments
2. **Monitor Health Continuously** via dashboard or Prometheus
3. **Set Appropriate Timeouts** based on your use case
4. **Enable Engine Restart** on VPN reconnect
5. **Choose Reliable VPN Providers** with stable connections
6. **Test Failure Scenarios** regularly in non-production
7. **Document Recovery Procedures** for your team
8. **Set Up Alerts** for VPN and engine health

---

## Troubleshooting

### VPN Recovery Taking Too Long

1. Check Gluetun logs: `docker logs gluetun`
2. Verify VPN credentials are correct
3. Ensure VPN provider is not blocking connection
4. Check network connectivity to VPN servers
5. Consider shorter `VPN_UNHEALTHY_RESTART_TIMEOUT_S`

### Engines Not Restarting After VPN Recovery

1. Verify `VPN_RESTART_ENGINES_ON_RECONNECT=true`
2. Check orchestrator logs for restart errors
3. Ensure engines are properly labeled with VPN container name
4. Manually restart engines if needed

### Redundant Mode Not Failing Over

1. Verify `VPN_MODE=redundant` is set
2. Check both VPN container names are configured
3. Ensure both VPNs have separate port ranges
4. Verify orchestrator can reach both VPN APIs

### Streams Still Failing During VPN Failure

1. This is expected if VPN container restarts (Scenario 2)
2. Use redundant mode to prevent stream interruption
3. Active streams will fail when network is lost
4. New streams will route to healthy VPN

---

## Related Documentation

- [Gluetun Integration Guide](GLUETUN_INTEGRATION.md)
- [Configuration Reference](CONFIG.md)
- [Health Monitoring](HEALTH_MONITORING.md)
- [API Documentation](API.md)
