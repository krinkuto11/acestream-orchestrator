# Gluetun VPN Integration

The AceStream Orchestrator can integrate with [Gluetun](https://github.com/qdm12/gluetun) to route all AceStream engines through a VPN connection.

## Overview

When Gluetun integration is enabled:
- All AceStream engines use Gluetun's network stack via `network_mode: container:gluetun`
- The orchestrator monitors Gluetun's health status continuously
- Engines are automatically restarted when VPN reconnects after a disconnection
- Engine provisioning waits for Gluetun to be healthy before starting

## Configuration

Add these variables to your `.env` file:

```bash
# Required: Name of your Gluetun container
GLUETUN_CONTAINER_NAME=gluetun

# Optional: Gluetun API port (default: 8000)
GLUETUN_API_PORT=8000

# Optional: Health check frequency (default: 5 seconds)
GLUETUN_HEALTH_CHECK_INTERVAL_S=5

# Optional: Restart engines on VPN reconnect (default: true)
VPN_RESTART_ENGINES_ON_RECONNECT=true

# Optional: Maximum number of active engine replicas when using Gluetun (default: 20)
# This limits the number of engine instances that can run simultaneously
# Ports will be allocated starting from 19000
MAX_ACTIVE_REPLICAS=20
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
    volumes:
      - /dev/net/tun:/dev/net/tun
    ports:
      # Map AceStream ports through Gluetun
      - "19000-19999:19000-19999"
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

# Force restart unhealthy VPN after timeout
VPN_UNHEALTHY_RESTART_TIMEOUT_S=60
```

### Docker Compose for Redundant Mode

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
    volumes:
      - /dev/net/tun:/dev/net/tun
    ports:
      - "19000-19499:19000-19499"  # Half the port range
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://ipinfo.io"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped

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
      - SERVER_COUNTRIES=Canada  # Use different server for redundancy
    volumes:
      - /dev/net/tun:/dev/net/tun
    ports:
      - "19500-19999:19500-19999"  # Other half of port range
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
      - VPN_MODE=redundant
      - GLUETUN_CONTAINER_NAME=gluetun1
      - GLUETUN_CONTAINER_NAME_2=gluetun2
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
5. **Port Allocation**: Engines are allocated ports from the range starting at 19000, limited by `MAX_ACTIVE_REPLICAS`
6. **Host Configuration**: Engines use the Gluetun container name as hostname for service discovery

### Host Resolution Behavior

The orchestrator automatically adjusts how engines resolve hostnames:

- **Without Gluetun**: Engines use their container names as hostnames for communication
- **With Gluetun**: Engines use the Gluetun container name as hostname since they share Gluetun's network stack

This ensures that:
- Services can communicate properly within the VPN container network
- No manual hostname configuration is required
- Service discovery works correctly in both VPN and non-VPN modes

### VPN Port Forwarding

When using Gluetun with port forwarding enabled, the orchestrator:
1. Queries Gluetun's API at `http://localhost:{GLUETUN_API_PORT}/v1/openvpn/portforwarded` to get the forwarded port
2. Sets the `P2P_PORT` environment variable in AceStream engines with the forwarded port
3. This allows AceStream engines to use the VPN's forwarded port for P2P traffic

The Gluetun API port is configurable via the `GLUETUN_API_PORT` environment variable (default: 8000).

### Port Management

The orchestrator implements intelligent port management:
- **Standard Mode**: Uses configurable port ranges for HTTP/HTTPS
- **Gluetun Mode**: Allocates sequential ports starting from 19000
- **Max Replicas**: Limits concurrent engines to `MAX_ACTIVE_REPLICAS` when using Gluetun
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