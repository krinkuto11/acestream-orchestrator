# Gluetun Port Configuration Guide

This document provides a comprehensive guide to configuring ports for Gluetun VPN integration with the AceStream Orchestrator.

## Overview

When using Gluetun VPN with the orchestrator, you need to configure two types of ports:

1. **HTTP Control Server Port** - For Gluetun API access (port forwarding, health checks)
2. **AceStream Engine Ports** - For serving AceStream content through the VPN

## HTTP Control Server Port

### What is it?

The HTTP control server is Gluetun's built-in API that provides:
- Port forwarding information: `/v1/openvpn/portforwarded` (for OpenVPN with port forwarding)
- Public IP information: `/v1/publicip/ip`
- VPN status and control endpoints

**Note:** Port forwarding endpoints are available when using OpenVPN or WireGuard with providers that support port forwarding. The orchestrator automatically queries the appropriate endpoint based on your VPN configuration.

### Configuration

#### In Gluetun Container

Set the `HTTP_CONTROL_SERVER_ADDRESS` environment variable:

```yaml
environment:
  - HTTP_CONTROL_SERVER_ADDRESS=:8001
```

This tells Gluetun to listen on port 8001 (internal) for API requests.

#### Exposing the Port

Map the internal port to a host port:

```yaml
ports:
  - "8001:8001"  # External:Internal
```

For redundant mode with two Gluetun containers, use different external ports but same internal port:

```yaml
# Gluetun1
ports:
  - "8001:8001"  # External 8001 -> Internal 8001

# Gluetun2
ports:
  - "8002:8001"  # External 8002 -> Internal 8001
```

#### In Orchestrator

Set the `GLUETUN_API_PORT` environment variable to match the **internal** port:

```bash
GLUETUN_API_PORT=8001
```

The orchestrator accesses Gluetun using container names (e.g., `http://gluetun:8001`), so it uses the internal port, not the external port.

### Common Configurations

| Setup | Gluetun HTTP_CONTROL_SERVER_ADDRESS | Gluetun Port Mapping | Orchestrator GLUETUN_API_PORT |
|-------|-------------------------------------|----------------------|-------------------------------|
| Single VPN | `:8001` | `8001:8001` | `8001` |
| Single VPN (alt) | `:8000` | `8000:8000` | `8000` |
| Redundant VPN1 | `:8001` | `8001:8001` | `8001` |
| Redundant VPN2 | `:8001` | `8002:8001` | `8001` |

**Important:** In redundant mode, both Gluetun containers should use the same internal port (e.g., `:8001`), but can have different external ports for host access.

## AceStream Engine Ports

### What are they?

These are the ports used by AceStream engines to serve content. When using Gluetun, all engine traffic is routed through the VPN container(s).

### Configuration

#### Port Range

The port range must be:
1. Declared in the orchestrator's `PORT_RANGE_HOST` configuration
2. Mapped through the Gluetun container(s)

#### Single VPN Mode

For single VPN mode, map the entire port range through one Gluetun container:

**Orchestrator Configuration:**
```bash
PORT_RANGE_HOST=19000-19999
```

**Gluetun Port Mapping:**
```yaml
ports:
  - "19000-19999:19000-19999"
```

#### Redundant VPN Mode

For redundant mode, split the port range between two Gluetun containers:

**Orchestrator Configuration:**
```bash
PORT_RANGE_HOST=19000-19999  # Total range covering both VPNs
```

**Gluetun1 Port Mapping:**
```yaml
ports:
  - "19000-19499:19000-19499"  # First half
```

**Gluetun2 Port Mapping:**
```yaml
ports:
  - "19500-19999:19500-19999"  # Second half
```

The orchestrator automatically distributes engines across both VPNs in round-robin fashion.

### Port Range Considerations

1. **Size**: Choose a range that can accommodate your `MAX_REPLICAS` setting
2. **Firewall**: Ensure the ports are allowed through your firewall
3. **No Overlap**: Port ranges should not overlap with other services
4. **Sequential**: Ports should be sequential for easier management

## Complete Configuration Examples

### Single VPN Mode

**.env:**
```bash
VPN_MODE=single
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_API_PORT=8001
PORT_RANGE_HOST=19000-19999
```

**docker-compose.yml:**
```yaml
services:
  gluetun:
    image: qmcgaw/gluetun
    container_name: gluetun
    environment:
      - HTTP_CONTROL_SERVER_ADDRESS=:8001
      # ... other VPN settings ...
    ports:
      - "19000-19999:19000-19999"  # Engine ports
      - "8001:8001"                # HTTP control server

  orchestrator:
    image: ghcr.io/krinkuto11/acestream-orchestrator:latest
    environment:
      - GLUETUN_CONTAINER_NAME=gluetun
      - GLUETUN_API_PORT=8001
      - PORT_RANGE_HOST=19000-19999
```

See `docker-compose.gluetun.yml` for a complete example.

### Redundant VPN Mode

**.env:**
```bash
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME=gluetun1
GLUETUN_CONTAINER_NAME_2=gluetun2
GLUETUN_API_PORT=8001
PORT_RANGE_HOST=19000-19999
```

**docker-compose.yml:**
```yaml
services:
  gluetun1:
    image: qmcgaw/gluetun
    container_name: gluetun1
    environment:
      - HTTP_CONTROL_SERVER_ADDRESS=:8001
      # ... other VPN settings ...
    ports:
      - "19000-19499:19000-19499"  # Engine ports (first half)
      - "8001:8001"                # HTTP control server

  gluetun2:
    image: qmcgaw/gluetun
    container_name: gluetun2
    environment:
      - HTTP_CONTROL_SERVER_ADDRESS=:8001  # Same internal port
      # ... other VPN settings ...
    ports:
      - "19500-19999:19500-19999"  # Engine ports (second half)
      - "8002:8001"                # HTTP control server (different external port)

  orchestrator:
    image: ghcr.io/krinkuto11/acestream-orchestrator:latest
    environment:
      - VPN_MODE=redundant
      - GLUETUN_CONTAINER_NAME=gluetun1
      - GLUETUN_CONTAINER_NAME_2=gluetun2
      - GLUETUN_API_PORT=8001      # Internal port (same for both)
      - PORT_RANGE_HOST=19000-19999
```

See `docker-compose.gluetun-redundant.yml` for a complete example.

## Troubleshooting

### Port Already Allocated

**Error:** "port is already allocated"

**Cause:** Port mapping conflict

**Solutions:**
1. Check if another container is using the same ports
2. Verify Gluetun port mappings match `PORT_RANGE_HOST`
3. Ensure no overlap between different services

### Cannot Access Gluetun API

**Error:** "Failed to get forwarded port from Gluetun"

**Causes & Solutions:**

1. **Wrong GLUETUN_API_PORT:**
   - Verify `GLUETUN_API_PORT` matches `HTTP_CONTROL_SERVER_ADDRESS`
   - Use the internal port, not the external port

2. **HTTP Control Server Not Enabled:**
   - Add `HTTP_CONTROL_SERVER_ADDRESS=:8001` to Gluetun environment

3. **Port Not Exposed:**
   - Add port mapping: `"8001:8001"` to Gluetun ports

### Engines Cannot Start

**Error:** "VPN container is not healthy - cannot start AceStream engine"

**Causes & Solutions:**

1. **Gluetun Not Ready:**
   - Wait for Gluetun to complete VPN connection
   - Check Gluetun logs: `docker logs gluetun`

2. **Health Check Failing:**
   - Verify Gluetun health check configuration
   - Ensure VPN connection is stable

3. **Port Range Mismatch:**
   - Verify engine ports are mapped through Gluetun
   - Check that `PORT_RANGE_HOST` matches Gluetun port mappings

### Redundant Mode: Engines Only Use One VPN

**Symptom:** All engines assigned to one VPN container

**Causes & Solutions:**

1. **Second VPN Unhealthy:**
   - Check health status: `GET /vpn/status`
   - Review logs for both VPN containers

2. **Port Range Not Split:**
   - Verify each VPN has its own port range
   - Ensure ranges don't overlap

3. **Configuration Missing:**
   - Verify `VPN_MODE=redundant` is set
   - Verify `GLUETUN_CONTAINER_NAME_2` is configured

## Best Practices

1. **Use Descriptive Container Names:** Name containers clearly (e.g., `gluetun1`, `gluetun2`)

2. **Document Your Port Ranges:** Keep notes on which ports are allocated for what purpose

3. **Monitor VPN Health:** Enable health checks on Gluetun containers

4. **Test Configuration:** Use `/vpn/status` endpoint to verify VPN integration

5. **Split Ranges Evenly:** In redundant mode, split port ranges evenly for load balancing

6. **Use Same Internal Port:** In redundant mode, use the same internal HTTP control server port for both VPNs

7. **Different External Ports:** In redundant mode, use different external HTTP control server ports for host access

## Verification

### Check VPN Status

```bash
curl http://localhost:8000/vpn/status
```

Should return:
- `enabled: true`
- `connected: true`
- `forwarded_port: <port>` (if port forwarding is enabled)

### Check Engine Status

```bash
curl http://localhost:8000/engines
```

Verify engines show:
- Correct `host` (should be Gluetun container name)
- Correct `port` (within PORT_RANGE_HOST)

### Test Connectivity

From inside an engine container:
```bash
docker exec <acestream-container> curl ifconfig.me
```

Should return the VPN's public IP, not your host IP.

## References

- [Gluetun Documentation](https://github.com/qdm12/gluetun)
- [Gluetun Control Server API](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md)
- [CONFIG.md](CONFIG.md) - Full configuration reference
- [GLUETUN_INTEGRATION.md](GLUETUN_INTEGRATION.md) - Integration guide
- `docker-compose.gluetun.yml` - Single VPN example
- `docker-compose.gluetun-redundant.yml` - Redundant VPN example
