# VPN-Specific Port Ranges in Redundant Mode

## Overview

When using redundant VPN mode with multiple Gluetun containers, each VPN container needs its own port range for the AceStream engines. This ensures that engines assigned to different VPNs use ports that are correctly mapped in the docker-compose configuration.

## Problem

Previously, all engines were allocated ports from a single global pool (starting at 19000), regardless of which VPN container they were assigned to. This caused issues where:

- Engines assigned to `gluetun` (with ports 19000-19499 mapped) got correct ports
- But engines assigned to `gluetun_2` (with ports 19500-19999 mapped) also got ports from the 19000-19499 range
- This broke the docker port mapping as `gluetun_2` only exposes ports 19500-19999

## Solution

Configure VPN-specific port ranges that match your docker-compose port mappings.

## Configuration

### Environment Variables

Add these environment variables to your `.env` file or docker-compose configuration:

```bash
# VPN mode
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_CONTAINER_NAME_2=gluetun_2

# VPN-specific port ranges (must match docker-compose port mappings)
GLUETUN_PORT_RANGE_1=19000-19499  # Port range for first VPN
GLUETUN_PORT_RANGE_2=19500-19999  # Port range for second VPN

# Global port range (must cover both VPN ranges)
PORT_RANGE_HOST=19000-19999
```

### Docker Compose

Your docker-compose.yml must map the correct port ranges for each VPN:

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun
    container_name: gluetun
    ports:
      - "19000-19499:19000-19499"  # Must match GLUETUN_PORT_RANGE_1
      - "8001:8001"
    # ... other config ...

  gluetun_2:
    image: qmcgaw/gluetun
    container_name: gluetun_2
    ports:
      - "19500-19999:19500-19999"  # Must match GLUETUN_PORT_RANGE_2
      - "8002:8001"
    # ... other config ...

  orchestrator:
    image: ghcr.io/krinkuto11/acestream-orchestrator:latest
    environment:
      - VPN_MODE=redundant
      - GLUETUN_CONTAINER_NAME=gluetun
      - GLUETUN_CONTAINER_NAME_2=gluetun_2
      - GLUETUN_PORT_RANGE_1=19000-19499
      - GLUETUN_PORT_RANGE_2=19500-19999
      - PORT_RANGE_HOST=19000-19999
    # ... other config ...
```

## How It Works

1. **VPN Assignment**: When starting a new engine, the orchestrator assigns it to a VPN container using round-robin distribution, preferring healthy VPNs.

2. **Port Allocation**: Based on the VPN assignment, the orchestrator allocates a port from that VPN's specific range:
   - Engines on `gluetun` get ports from 19000-19499
   - Engines on `gluetun_2` get ports from 19500-19999

3. **Container Labeling**: Each engine container is labeled with its assigned VPN using the `acestream.vpn_container` label.

4. **Port Release**: When an engine is stopped, its port is released back to the correct VPN-specific pool.

## Backwards Compatibility

If `GLUETUN_PORT_RANGE_1` and `GLUETUN_PORT_RANGE_2` are not configured:
- The system falls back to the previous global allocation behavior
- All engines get ports from a single pool starting at 19000
- This works fine for single VPN mode but may cause issues in redundant mode

## Example Output

With correct configuration, the `/engines` endpoint will show:

```json
[
  {
    "container_name": "acestream-1",
    "host": "gluetun",
    "port": 19000,
    "vpn_container": "gluetun",
    "forwarded": true
  },
  {
    "container_name": "acestream-2",
    "host": "gluetun",
    "port": 19001,
    "vpn_container": "gluetun",
    "forwarded": false
  },
  {
    "container_name": "acestream-3",
    "host": "gluetun_2",
    "port": 19500,
    "vpn_container": "gluetun_2",
    "forwarded": true
  },
  {
    "container_name": "acestream-4",
    "host": "gluetun_2",
    "port": 19501,
    "vpn_container": "gluetun_2",
    "forwarded": false
  }
]
```

Note how:
- Engines on `gluetun` have ports 19000, 19001 (from the 19000-19499 range)
- Engines on `gluetun_2` have ports 19500, 19501 (from the 19500-19999 range)
- Each VPN has one forwarded engine

## Troubleshooting

### Engines have incorrect ports

**Symptoms**: Engines assigned to `gluetun_2` have ports in the 19000-19499 range

**Solution**: Check that:
1. `GLUETUN_PORT_RANGE_1` and `GLUETUN_PORT_RANGE_2` are set in your environment
2. The port ranges match your docker-compose port mappings
3. The orchestrator container has been restarted after adding these variables

### Port allocation errors

**Symptoms**: Errors like "No available ports in Gluetun port range"

**Solution**: 
1. Check that your port ranges are large enough for `MAX_ACTIVE_REPLICAS`
2. Verify that the ranges in `GLUETUN_PORT_RANGE_1` and `GLUETUN_PORT_RANGE_2` don't overlap
3. Check the orchestrator logs for any configuration errors

### Invalid port range format

**Symptoms**: Error logs showing "Invalid GLUETUN_PORT_RANGE_X format"

**Solution**: Port ranges must be in the format `min-max`, e.g., `19000-19499`
- Both values must be integers
- Min must be less than or equal to max
- Ports must be in the valid range (1-65535)

## See Also

- [GLUETUN_INTEGRATION.md](GLUETUN_INTEGRATION.md) - General Gluetun integration guide
- [docker-compose.gluetun-redundant.yml](../docker-compose.gluetun-redundant.yml) - Example redundant setup
