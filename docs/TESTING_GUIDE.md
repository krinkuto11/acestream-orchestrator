# Testing Guide: VPN Port Allocation Fix

> **Note**: This document describes testing for a specific VPN port allocation fix. For general deployment and testing, see:
> - [Deployment Guide](DEPLOY.md) - Complete deployment and testing instructions
> - [Gluetun Integration](GLUETUN_INTEGRATION.md) - VPN setup and configuration

## Quick Test with Your Setup

Based on your docker-compose configuration, follow these steps to test the fix:

### 1. Update Environment Configuration

Add these lines to your `.env` file (or directly in the orchestrator service environment in docker-compose):

```bash
# VPN-specific port ranges - MUST match your docker-compose port mappings!
GLUETUN_PORT_RANGE_1=19000-19499  # For gluetun
GLUETUN_PORT_RANGE_2=19500-19999  # For gluetun_2
```

### 2. Update Your Docker Compose

Your orchestrator service should have these environment variables:

```yaml
orchestrator:
  # ... other config ...
  environment:
    - VPN_MODE=redundant
    - GLUETUN_CONTAINER_NAME=gluetun
    - GLUETUN_CONTAINER_NAME_2=gluetun_2
    - GLUETUN_PORT_RANGE_1=19000-19499
    - GLUETUN_PORT_RANGE_2=19500-19999
    - PORT_RANGE_HOST=19000-19999
    # ... other variables from your .env file ...
```

### 3. Restart the Orchestrator

```bash
docker-compose down orchestrator
docker-compose up -d orchestrator
```

### 4. Verify the Fix

Check the `/engines` endpoint:

```bash
curl http://localhost:8000/engines | jq
```

You should now see:
- ✅ Engines with `"host": "gluetun"` have ports in range **19000-19499**
- ✅ Engines with `"host": "gluetun_2"` have ports in range **19500-19999**
- ✅ Each VPN has at least one engine with `"forwarded": true`

### Expected Output Example

```json
[
  {
    "container_name": "acestream-1",
    "host": "gluetun",
    "port": 19000,
    "forwarded": true,
    "vpn_container": "gluetun"
  },
  {
    "container_name": "acestream-2",
    "host": "gluetun",
    "port": 19001,
    "forwarded": false,
    "vpn_container": "gluetun"
  },
  {
    "container_name": "acestream-3",
    "host": "gluetun_2",
    "port": 19500,
    "forwarded": true,
    "vpn_container": "gluetun_2"
  }
]
```

### 5. Verify Forwarded Engines

Count the forwarded engines:

```bash
curl http://localhost:8000/engines | jq '[.[] | select(.forwarded == true)] | length'
```

You should see **2** (one per VPN) instead of just 1.

## Troubleshooting

### Still seeing wrong ports?

1. Check orchestrator logs for configuration errors:
   ```bash
   docker logs orchestrator | grep -i "port range"
   ```

2. Verify environment variables are set:
   ```bash
   docker exec orchestrator printenv | grep GLUETUN_PORT
   ```

3. Make sure you restarted the orchestrator container after adding the variables

### Engines not starting?

1. Check if port ranges are large enough for your `MAX_ACTIVE_REPLICAS`
2. Verify no overlap between the two port ranges
3. Check orchestrator logs for detailed errors

### Still only 1 forwarded engine?

This might be expected if:
- Only one VPN has a working forwarded port from the VPN provider
- Check VPN status endpoint: `curl http://localhost:8000/vpn`
- Verify both VPNs have `"forwarded_port"` values

## Rollback

If you encounter issues, you can rollback by:
1. Removing the `GLUETUN_PORT_RANGE_1` and `GLUETUN_PORT_RANGE_2` variables
2. Restarting the orchestrator
3. The system will fall back to global port allocation

## Getting Help

If you encounter issues:
1. Check the orchestrator logs: `docker logs orchestrator`
2. Verify your docker-compose port mappings match the configured ranges
3. Review [GLUETUN_INTEGRATION.md](GLUETUN_INTEGRATION.md) for detailed VPN configuration information
