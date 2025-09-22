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

# Optional: Health check frequency (default: 5 seconds)
GLUETUN_HEALTH_CHECK_INTERVAL_S=5

# Optional: Restart engines on VPN reconnect (default: true)
VPN_RESTART_ENGINES_ON_RECONNECT=true
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
    volumes:
      - orchestrator-db:/app
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
  orchestrator-db:
```

### Key Points

1. **Gluetun Health Check**: Essential for detecting VPN disconnections
2. **Port Mapping**: Map your AceStream port range through Gluetun
3. **Dependencies**: Orchestrator should wait for Gluetun to be healthy
4. **Container Name**: Must match `GLUETUN_CONTAINER_NAME` exactly

## How It Works

### Network Routing

When `GLUETUN_CONTAINER_NAME` is set, the orchestrator:
1. Uses `network_mode: container:gluetun` for all AceStream engines
2. This routes all engine traffic through Gluetun's network stack
3. Engines inherit Gluetun's IP address and VPN connection
4. **Host Configuration**: Engines use `localhost` for inter-service communication since they share the same network stack

### Host Resolution Behavior

The orchestrator automatically adjusts how engines resolve hostnames:

- **Without Gluetun**: Engines use their container names as hostnames for communication
- **With Gluetun**: Engines use `localhost` as hostname since they share Gluetun's network stack

This ensures that:
- Services can communicate properly within the VPN container network
- No manual hostname configuration is required
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
- Port mappings must be configured through Gluetun
- Some VPN providers may have bandwidth limitations
- Network performance depends on VPN server quality