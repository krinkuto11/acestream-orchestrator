# Gluetun Enhancements

This document describes the Gluetun enhancements implemented to improve VPN integration with AceStream engines.

## Overview

Three key enhancements have been implemented for better Gluetun VPN integration:

1. **Host Configuration**: Engines use Gluetun container name as host
2. **Replica Management**: MAX_ACTIVE_REPLICAS environment variable limits concurrent instances
3. **P2P Port Forwarding**: Automatic P2P_PORT configuration from VPN forwarded port

## Implementation Details

### 1. Host Configuration

**Before**: Engines used `localhost` as hostname when Gluetun was enabled.
**After**: Engines use the Gluetun container name (from `GLUETUN_CONTAINER_NAME`) as hostname.

This change is implemented in `app/services/reindex.py`:

```python
# Determine host based on Gluetun configuration
if cfg.GLUETUN_CONTAINER_NAME:
    host = cfg.GLUETUN_CONTAINER_NAME  # Use Gluetun container name
else:
    host = container_name or "127.0.0.1"  # Use container name or fallback
```

### 2. Maximum Active Replicas

**New Environment Variable**: `MAX_ACTIVE_REPLICAS` (default: 20)

This variable limits the number of engine instances that can run simultaneously when using Gluetun. Ports are allocated sequentially starting from 19000.

Configuration in `app/core/config.py`:
```python
MAX_ACTIVE_REPLICAS: int = int(os.getenv("MAX_ACTIVE_REPLICAS", 20))
```

Port allocation logic in `app/services/ports.py`:
- New `alloc_gluetun_port()` method allocates ports from 19000-19000+MAX_ACTIVE_REPLICAS
- Enforces replica limit by tracking allocated ports
- Automatic cleanup when engines are stopped

### 3. P2P Port Forwarding

**New Feature**: Automatic P2P_PORT environment variable configuration

When Gluetun is enabled, the orchestrator:
1. Queries Gluetun's API at `http://localhost:8000/v1/openvpn/portforwarded`
2. Extracts the forwarded port number
3. Sets `P2P_PORT` environment variable in AceStream engines

Implementation in `app/services/gluetun.py`:
```python
def get_forwarded_port_sync() -> Optional[int]:
    """Get the VPN forwarded port from Gluetun API."""
    try:
        with httpx.Client() as client:
            response = client.get(f"http://localhost:{cfg.GLUETUN_API_PORT}/v1/openvpn/portforwarded", timeout=10)
            response.raise_for_status()
            data = response.json()
            return int(data.get("port")) if data.get("port") else None
    except Exception as e:
        logger.error(f"Failed to get forwarded port from Gluetun: {e}")
        return None
```

The API endpoint is now configurable via the `GLUETUN_API_PORT` environment variable.

## Testing

Comprehensive tests have been added to verify all functionality:

- `tests/test_gluetun_enhancements.py`: Individual feature tests
- `tests/test_complete_integration.py`: End-to-end integration test
- `tests/test_gluetun_port_fix.py`: Updated for new port allocation logic

## Configuration Example

Update your `.env` file:

```bash
# Enable Gluetun integration
GLUETUN_CONTAINER_NAME=gluetun

# Optional: Gluetun API port (default: 8000)
GLUETUN_API_PORT=8000

# Optional: Limit concurrent engine instances (default: 20)
MAX_ACTIVE_REPLICAS=10

# Other Gluetun settings
GLUETUN_HEALTH_CHECK_INTERVAL_S=5
VPN_RESTART_ENGINES_ON_RECONNECT=true
```

## Port Range Allocation

When using Gluetun:
- Engine ports start at 19000
- Sequential allocation: 19000, 19001, 19002, etc.
- Maximum concurrent engines limited by MAX_ACTIVE_REPLICAS
- Automatic port cleanup when engines stop

Ensure your Gluetun docker-compose.yml maps the port range:
```yaml
ports:
  - "19000-19999:19000-19999"  # Adjust range as needed
```