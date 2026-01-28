# Gluetun VPN Integration

The AceStream Orchestrator can integrate with [Gluetun](https://github.com/qdm12/gluetun) to route all AceStream engines through a VPN connection.

## Quick Links

- **[Failure & Recovery Scenarios](GLUETUN_FAILURE_RECOVERY.md)** - VPN failure handling with diagrams
- **[Deployment Guide](DEPLOY.md)** - Step-by-step setup for all VPN modes
- **[Configuration Reference](CONFIG.md)** - Complete environment variable guide

## Overview

When Gluetun integration is enabled:
- All AceStream engines use Gluetun's network stack via `network_mode: container:gluetun`
- The orchestrator monitors Gluetun's health status continuously
- Engines are automatically restarted when VPN reconnects after a disconnection
- Engine provisioning waits for Gluetun to be healthy before starting
- Forwarded engines leverage VPN port forwarding for optimal P2P connectivity

## Configuration

Add these variables to your `.env` file:

```bash
# Required: Name of your Gluetun container
GLUETUN_CONTAINER_NAME=gluetun

# Required: Gluetun HTTP control server port (must match HTTP_CONTROL_SERVER_ADDRESS in Gluetun)
# This is the port where Gluetun's HTTP API is accessible
# Default: 8000, but many examples use 8001
GLUETUN_API_PORT=8001

# Optional: Health check frequency (default: 5 seconds)
GLUETUN_HEALTH_CHECK_INTERVAL_S=5

# Optional: Restart engines on VPN reconnect (default: true)
VPN_RESTART_ENGINES_ON_RECONNECT=true

# Note: Engine replica limits are now controlled by MAX_REPLICAS setting
# Configure via UI (Engine Configuration tab) or engine_settings.json
```

## Docker Compose Setup

### Basic Setup

Here's a complete docker-compose.yml example with Gluetun:

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun:latest
    container_name: gluetun
    cap_add:
      - NET_ADMIN
    environment:
      # Example for NordVPN - adjust for your VPN provider
      - VPN_SERVICE_PROVIDER=nordvpn
      - VPN_TYPE=openvpn
      - OPENVPN_USER=your_username
      - OPENVPN_PASSWORD=your_password
      - SERVER_COUNTRIES=United States
      # HTTP control server port - REQUIRED for orchestrator API access
      # This must match GLUETUN_API_PORT in the orchestrator configuration
      - HTTP_CONTROL_SERVER_ADDRESS=:8001
    volumes:
      - /dev/net/tun:/dev/net/tun
    ports:
      # Map AceStream engine ports through Gluetun
      - "19000-19999:19000-19999"
      # Expose HTTP control server port for API access (port forwarding, health)
      - "8001:8001"
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://ipinfo.io"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped

  orchestrator:
    build: .
    env_file: .env
    environment:
      - DOCKER_HOST=tcp://docker:2375
      - GLUETUN_CONTAINER_NAME=gluetun
      # Must match the HTTP_CONTROL_SERVER_ADDRESS port in Gluetun
      - GLUETUN_API_PORT=8001
    ports:
      - "8000:8000"
    depends_on:
      gluetun:
        condition: service_healthy
      docker:
        condition: service_healthy
    restart: on-failure

  docker:
    image: docker:27.3.1-dind
    privileged: true
    environment:
      DOCKER_TLS_CERTDIR: ""
    ports:
      - "2375:2375"
    volumes:
      - docker-data:/var/lib/docker
    healthcheck:
      test: ["CMD", "docker", "info"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  docker-data:
```

### Key Points

1. **Gluetun Health Check**: Essential for detecting VPN disconnections
2. **Port Mapping**: Map your AceStream port range through Gluetun
3. **Dependencies**: Orchestrator should wait for Gluetun to be healthy
4. **Container Name**: Must match `GLUETUN_CONTAINER_NAME` exactly

### Required Port Configuration

The Gluetun integration requires two types of ports to be properly configured:

#### 1. HTTP Control Server Port (REQUIRED)

Gluetun's HTTP control server provides the API for:
- Port forwarding information (`/v1/openvpn/portforwarded`)
- Public IP information (`/v1/publicip/ip`)
- Health status checks

**Gluetun Configuration:**
```yaml
environment:
  - HTTP_CONTROL_SERVER_ADDRESS=:8001  # Internal port for HTTP API
ports:
  - "8001:8001"  # Expose HTTP control server
```

**Orchestrator Configuration:**
```bash
GLUETUN_API_PORT=8001  # Must match HTTP_CONTROL_SERVER_ADDRESS
```

**Important:** The orchestrator accesses the Gluetun API using the container name (e.g., `http://gluetun:8001`), so the `GLUETUN_API_PORT` must match the internal port specified in `HTTP_CONTROL_SERVER_ADDRESS`.

#### 2. AceStream Engine Ports (REQUIRED)

These ports are used by the AceStream engines to serve content. The port range should match your `PORT_RANGE_HOST` configuration:

**Gluetun Configuration:**
```yaml
ports:
  - "19000-19999:19000-19999"  # Must match PORT_RANGE_HOST in .env
```

**Orchestrator Configuration:**
```bash
PORT_RANGE_HOST=19000-19999  # Must match Gluetun port mapping
```

## Redundant VPN Mode (High Availability)

For mission-critical setups, you can configure two VPN containers for high availability. When one VPN fails, engines continue streaming through the healthy VPN without interruption.

### Configuration

```bash
# Enable redundant VPN mode
VPN_MODE=redundant

# Primary VPN container
GLUETUN_CONTAINER_NAME=gluetun1

# Secondary VPN container
GLUETUN_CONTAINER_NAME_2=gluetun2

# HTTP control server port (internal port, same for both containers)
GLUETUN_API_PORT=8001

# Force restart unhealthy VPN after timeout
VPN_UNHEALTHY_RESTART_TIMEOUT_S=60

# Port range must cover both VPN containers
PORT_RANGE_HOST=19000-19999
```

### Docker Compose for Redundant Mode

For a complete example, see `docker-compose.gluetun-redundant.yml` in the repository.

Key configuration points for redundant mode:

#### Port Distribution

In redundant mode, the port range is split between two VPN containers:

**Gluetun1 Configuration:**
```yaml
services:
  gluetun1:
    image: qmcgaw/gluetun:latest
    container_name: gluetun1
    cap_add:
      - NET_ADMIN
    environment:
      - VPN_SERVICE_PROVIDER=nordvpn
      - VPN_TYPE=openvpn
      - OPENVPN_USER=your_username
      - OPENVPN_PASSWORD=your_password
      - SERVER_COUNTRIES=United States
      # HTTP control server - REQUIRED
      - HTTP_CONTROL_SERVER_ADDRESS=:8001
    volumes:
      - /dev/net/tun:/dev/net/tun
    ports:
      # First half of engine port range (19000-19499)
      - "19000-19499:19000-19499"
      # HTTP control server API (external port 8001)
      - "8001:8001"
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://ipinfo.io"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped
```

**Gluetun2 Configuration:**
```yaml
  gluetun2:
    image: qmcgaw/gluetun:latest
    container_name: gluetun2
    cap_add:
      - NET_ADMIN
    environment:
      - VPN_SERVICE_PROVIDER=nordvpn
      - VPN_TYPE=openvpn
      - OPENVPN_USER=your_username
      - OPENVPN_PASSWORD=your_password
      - SERVER_COUNTRIES=Canada  # Different server for redundancy
      # HTTP control server - same internal port as gluetun1
      - HTTP_CONTROL_SERVER_ADDRESS=:8001
    volumes:
      - /dev/net/tun:/dev/net/tun
    ports:
      # Second half of engine port range (19500-19999)
      - "19500-19999:19500-19999"
      # HTTP control server API (external port 8002, internal 8001)
      - "8002:8001"
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://ipinfo.io"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped
```

**Orchestrator Configuration:**
```yaml
  orchestrator:
    build: .
    env_file: .env
    environment:
      - DOCKER_HOST=tcp://docker:2375
      - VPN_MODE=redundant
      - GLUETUN_CONTAINER_NAME=gluetun1
      - GLUETUN_CONTAINER_NAME_2=gluetun2
      # Internal port (same for both Gluetun containers)
      - GLUETUN_API_PORT=8001
      # Full port range covering both VPNs
      - PORT_RANGE_HOST=19000-19999
    ports:
      - "8000:8000"
    depends_on:
      gluetun1:
        condition: service_healthy
      gluetun2:
        condition: service_healthy
      docker:
        condition: service_healthy
    restart: on-failure
```

**Important Notes for Redundant Mode:**

1. **HTTP Control Server Ports:**
   - Both Gluetun containers use the same **internal** port (`:8001` in `HTTP_CONTROL_SERVER_ADDRESS`)
   - They can have different **external** ports (8001 and 8002 in the example)
   - The orchestrator uses the internal port via container names: `http://gluetun1:8001` and `http://gluetun2:8001`
   - Set `GLUETUN_API_PORT=8001` (the internal port) in the orchestrator

2. **Engine Port Ranges:**
   - Split your `PORT_RANGE_HOST` between the two VPN containers
   - Example: 19000-19499 for VPN1, 19500-19999 for VPN2
   - The orchestrator automatically distributes engines across both VPNs
   - Total range in orchestrator must cover both splits: `PORT_RANGE_HOST=19000-19999`

3. **VPN Server Selection:**
   - Use different server locations for each VPN for true redundancy
   - If one server/location has issues, the other VPN continues working

### How Redundant Mode Works

1. **Engine Distribution**: Engines are distributed evenly across both VPN containers using round-robin assignment
2. **Health Monitoring**: Each VPN container is monitored independently every 5 seconds
3. **Failover**: When a VPN becomes unhealthy:
   - Engines assigned to that VPN are hidden from the proxy (not returned in `/engines` endpoint)
   - Existing streams continue on healthy VPN's engines
   - New streams are only assigned to healthy VPN's engines
4. **Recovery**: When an unhealthy VPN recovers:
   - Engines assigned to it are restarted to reconnect to the new VPN address
   - Once engines are ready, they're made available to the proxy again
5. **Force Restart**: If a VPN is unhealthy for more than 60 seconds, it's automatically restarted via Docker

### Benefits

- **Zero Downtime**: Streams continue without interruption when one VPN fails
- **Automatic Recovery**: Failed VPN is restarted and engines reconnected automatically
- **Load Distribution**: Engine load is balanced across both VPNs
- **Failover Prevention**: Issues with one VPN provider don't affect the entire system

## How It Works

### Network Routing

When `GLUETUN_CONTAINER_NAME` is set, the orchestrator:
1. Uses `network_mode: container:gluetun` for all AceStream engines
2. This routes all engine traffic through Gluetun's network stack
3. Engines inherit Gluetun's IP address and VPN connection
4. **Port Management**: Engines share Gluetun's port mappings - no individual port mapping is performed
5. **Port Allocation**: Engines are allocated ports from the range starting at 19000
6. **Host Configuration**: Engines use the Gluetun container name as hostname for service discovery

### Host Resolution Behavior

The orchestrator automatically adjusts how engines resolve hostnames:

- **Without Gluetun**: Engines use their container names as hostnames for communication
- **With Gluetun**: Engines use the Gluetun container name as hostname since they share Gluetun's network stack

This ensures that:
- Services can communicate properly within the VPN container network
- No manual hostname configuration is required
- Service discovery works correctly in both VPN and non-VPN modes

### VPN Port Forwarding & Forwarded Engines

When using Gluetun with port forwarding enabled, the orchestrator implements a "forwarded engine" system:

#### What is a Forwarded Engine?

Only **one engine per VPN** can actually use the VPN's forwarded P2P port. This engine is marked as "forwarded" and receives optimal P2P connectivity:

- **Single VPN Mode**: One forwarded engine total
- **Redundant VPN Mode**: One forwarded engine per VPN (two total)

#### How It Works

1. **Port Discovery**: The orchestrator queries Gluetun's API to get the forwarded port:
   ```
   GET http://{gluetun}:{GLUETUN_API_PORT}/v1/openvpn/portforwarded
   ```

2. **Engine Selection**: When provisioning engines:
   - If no forwarded engine exists, the first engine becomes forwarded
   - The forwarded port is passed to the engine via `P2P_PORT` environment variable
   - Container is labeled with `acestream.forwarded=true`

3. **API Response**: The `/engines` endpoint includes forwarded status:
   ```json
   {
     "container_id": "abc123",
     "container_name": "acestream-1",
     "host": "gluetun",
     "port": 19000,
     "forwarded": true,
     "health_status": "healthy"
   }
   ```

4. **Dashboard Display**: Forwarded engines show a "FORWARDED" badge in the UI

#### Benefits

- **Proxy Prioritization**: Proxies can prioritize forwarded engines for best performance
- **Clear Identification**: Easy to identify which engine has the P2P port
- **Automatic Management**: System automatically selects and maintains forwarded engine

#### Port Change Handling

When the VPN restarts internally (e.g., due to reconnection or network changes), the forwarded port may change. The orchestrator handles this automatically:

1. **Detection**: Monitors forwarded port every 30 seconds
2. **Port Change Detected**: When port changes (e.g., from 43437 to 57611):
   - Old forwarded engine is stopped immediately
   - Removed from state to hide from `/engines` endpoint
   - **Immediate Autoscaling**: Triggers autoscaler immediately (not waiting for next cycle)
3. **Rapid Replacement**: New forwarded engine provisioned within ~4 seconds
4. **Recovery Stabilization**: 2-minute grace period prevents premature cleanup during recovery

**Timeline Example**:
```
16:30:02 - Port change detected (43437 → 57611)
16:30:12 - Old forwarded engine stopped
16:30:16 - New forwarded engine provisioned (gap: ~4 seconds)
```

This ensures minimal downtime when VPN ports change, reducing the gap from ~1-2 minutes (waiting for periodic autoscaler) to just a few seconds.

#### Double-Check Connectivity

When Gluetun's Docker health check reports unhealthy, the orchestrator performs a double-check:

1. Queries each engine's network connectivity endpoint:
   ```
   GET http://{engine}/server/api?api_version=3&method=get_network_connection_status
   ```

2. If any engine reports `{"result": {"connected": true}}`, the VPN is considered healthy

3. This prevents false negatives from Gluetun health check issues

This ensures accurate VPN status reporting even when Gluetun's internal health check fails for non-connectivity reasons.

### Port Management

The orchestrator implements intelligent port management with two types of ports:

#### 1. HTTP Control Server Port (Gluetun API)

**Purpose**: Used by the orchestrator to communicate with Gluetun for:
- Port forwarding information (`/v1/openvpn/portforwarded`)
- Public IP information (`/v1/publicip/ip`)
- Health status checks

**Configuration**:
- Gluetun: Set `HTTP_CONTROL_SERVER_ADDRESS=:8001`
- Gluetun: Expose port `8001:8001`
- Orchestrator: Set `GLUETUN_API_PORT=8001`

**Important**: Use the **internal** port (e.g., `:8001`), not external port.

#### 2. AceStream Engine Ports

**Purpose**: Used by engines to serve stream content.

**Single VPN Mode**:
```yaml
gluetun:
  ports:
    - "19000-19999:19000-19999"  # Must match PORT_RANGE_HOST
```

**Redundant VPN Mode** (split ranges):
```yaml
gluetun1:
  ports:
    - "19000-19499:19000-19499"  # First VPN
gluetun2:
  ports:
    - "19500-19999:19500-19999"  # Second VPN
```

**Configuration**:
```bash
# .env
PORT_RANGE_HOST=19000-19999           # Full range
GLUETUN_PORT_RANGE_1=19000-19499      # VPN1 range (redundant mode)
GLUETUN_PORT_RANGE_2=19500-19999      # VPN2 range (redundant mode)
```

**Port Allocation**:
- **Standard Mode**: Uses configurable port ranges for HTTP/HTTPS
- **Single VPN Mode**: Allocates sequential ports from PORT_RANGE_HOST
- **Redundant VPN Mode**: Allocates from VPN-specific ranges
- **Automatic Cleanup**: Releases ports when engines are stopped
- Seamless transition between VPN and non-VPN modes

### Health Monitoring

The orchestrator continuously monitors Gluetun's health status:
- **Healthy**: Container is running and health check passes
- **Unhealthy**: Container is running but health check fails
- **Stopped**: Container is not running

### VPN Reconnection Handling

When VPN disconnects and reconnects:
1. Gluetun transitions: `healthy` → `unhealthy` → `healthy`
2. Orchestrator detects the transition back to healthy
3. If `VPN_RESTART_ENGINES_ON_RECONNECT=true`, all engines are restarted
4. Autoscaler ensures minimum replicas are maintained

### Engine Provisioning

Before starting new engines:
1. Orchestrator checks if Gluetun is configured
2. Waits up to 30 seconds for Gluetun to be healthy
3. Only starts engines if Gluetun is healthy
4. Fails with error if Gluetun is not available

## Monitoring and Logs

Enable debug logging to monitor VPN integration:

```bash
# In your .env
LOG_LEVEL=DEBUG
```

Log messages to watch for:
- `Gluetun monitor started for container 'gluetun'`
- `Gluetun VPN became unhealthy`
- `Gluetun VPN recovered and is now healthy`
- `VPN reconnected - triggering AceStream engine restart`

## Troubleshooting

### Common Issues

1. **Engines fail to start**: Check if Gluetun is healthy
2. **No VPN traffic**: Verify port mappings through Gluetun
3. **Frequent restarts**: Check VPN stability and health check settings

### Verification

Check if VPN is working:
```bash
# Get engine IP (should be VPN IP)
docker exec <acestream-container> curl -s ifconfig.me

# Check Gluetun health
docker inspect gluetun | grep Health -A 10
```

### Performance Tuning

For better availability:
- Use `GLUETUN_HEALTH_CHECK_INTERVAL_S=3` for faster detection
- Ensure Gluetun health check is reliable and fast
- Use VPN servers with good stability

## Security Considerations

- All AceStream traffic routes through VPN when enabled
- Gluetun container must have NET_ADMIN capability
- Ensure VPN credentials are properly secured
- Consider using docker secrets for sensitive data

## Limitations

- Engines cannot start if Gluetun is unhealthy
- Port mappings must be configured through Gluetun (orchestrator automatically handles this)
- Some VPN providers may have bandwidth limitations
- Network performance depends on VPN server quality

**Note**: The orchestrator automatically detects when Gluetun is in use and skips individual container port mappings to prevent "port already allocated" errors. All port access is handled through the Gluetun container's port mappings.