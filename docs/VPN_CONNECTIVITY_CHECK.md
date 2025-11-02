# VPN Connectivity Check Enhancement

## Overview

This document describes the enhancement to the VPN connectivity double-check mechanism, which now uses the AceStream engine's network connection status endpoint to verify internet connectivity.

## What Changed

### Previous Implementation

The previous implementation checked for active streams to infer VPN connectivity:
- If active streams existed, the system assumed the VPN was working
- This was an indirect method that didn't actually verify internet connectivity

### New Implementation

The new implementation directly checks internet connectivity using the AceStream engine's API:
- Uses the `/server/api?api_version=3&method=get_network_connection_status` endpoint
- Returns `{"result": {"connected": true}}` when the engine has internet connectivity
- Directly verifies that engines can reach the internet through the VPN

## How It Works

### 1. Endpoint Details

**URL Pattern**: `http://{engine_host}:{engine_port}/server/api?api_version=3&method=get_network_connection_status`

**Successful Response**:
```json
{
  "result": {
    "connected": true
  }
}
```

**Disconnected Response**:
```json
{
  "result": {
    "connected": false
  }
}
```

### 2. VPN Health Check Flow

When Gluetun container reports "unhealthy", the system performs a double-check:

1. **Get all engines**: Retrieves the list of all running AceStream engines
2. **Check each engine**: Queries the network connection status endpoint for each engine
3. **Aggregate results**: If at least one engine reports `connected: true`, considers VPN healthy
4. **Return status**: Returns "healthy" or "unhealthy" based on the aggregated results

### 3. Code Location

**Implementation**: `app/services/gluetun.py` - `_double_check_connectivity_via_engines()`

**Helper Function**: `app/services/health.py` - `check_engine_network_connection()`

**Usage**: Called from `get_vpn_status()` when Gluetun container health is "unhealthy"

## Benefits

1. **Direct Verification**: Actually checks if engines can reach the internet
2. **More Accurate**: Not dependent on whether streams are currently active
3. **Better Diagnostics**: Provides clearer indication of VPN connectivity status
4. **Fault Tolerant**: Checks multiple engines and considers VPN healthy if any can connect
5. **Error Resilient**: Handles individual engine check failures gracefully

## Example Scenarios

### Scenario 1: Gluetun Unhealthy but VPN Working

```
Gluetun Container: unhealthy (Docker health check failing)
Engine 1: connected=true
Engine 2: connected=true
Result: VPN status reported as "healthy"
```

This can happen when Gluetun's health check script fails for reasons unrelated to network connectivity.

### Scenario 2: Actual VPN Disconnection

```
Gluetun Container: unhealthy
Engine 1: connected=false
Engine 2: connected=false
Result: VPN status reported as "unhealthy"
```

This indicates a real VPN connectivity issue that needs attention.

### Scenario 3: Partial Connectivity

```
Gluetun Container: unhealthy
Engine 1: Network error (timeout)
Engine 2: connected=true
Engine 3: connected=false
Result: VPN status reported as "healthy"
```

Even with some engines having issues, if at least one reports connectivity, the VPN is considered operational.

## Testing

A comprehensive test suite verifies the new implementation:

**Test File**: `tests/test_vpn_network_connectivity.py`

**Test Cases**:
1. No engines available → returns "unhealthy"
2. All engines connected → returns "healthy"
3. Some engines connected → returns "healthy"
4. No engines connected → returns "unhealthy"
5. Exceptions during checks → handles gracefully
6. Correct endpoint usage → verifies URL construction
7. Disconnected response → handles false connection status

**Running Tests**:
```bash
python3 tests/test_vpn_network_connectivity.py
```

## Configuration

No new configuration variables are required. The feature uses existing settings:

- `GLUETUN_CONTAINER_NAME`: Name of the Gluetun VPN container
- `GLUETUN_HEALTH_CHECK_INTERVAL_S`: How often to check Gluetun health
- Engine host/port information from the state manager

## API Impact

The `/vpn/status` endpoint may now return `"health": "healthy"` even when Gluetun reports unhealthy, if engines verify connectivity:

```json
{
  "enabled": true,
  "status": "running",
  "container_name": "gluetun",
  "health": "healthy",
  "connected": true,
  "forwarded_port": 12345,
  "last_check": "2025-11-02T10:55:00Z"
}
```

## Monitoring

The system logs provide visibility into the VPN double-check process:

**Success Logs**:
```
INFO: VPN double-check: Engine abc123def456 reports internet connectivity
INFO: VPN double-check: 2/3 engine(s) have internet connectivity - considering VPN healthy
```

**Failure Logs**:
```
WARNING: VPN double-check: None of 3 engine(s) have internet connectivity
```

**Debugging**:
```
DEBUG: VPN double-check: No engines available to verify connectivity
DEBUG: VPN double-check: Failed to check engine abc123def456: Connection timeout
```

## Troubleshooting

### VPN shows healthy but streams fail

If the VPN status shows "healthy" due to engine connectivity, but streams are failing:

1. Check individual engine health status
2. Verify network routing configuration
3. Review Gluetun logs for specific health check failures
4. Test manual connectivity from engines

### All engines report disconnected

If all engines report `connected: false`:

1. Check Gluetun VPN connection status
2. Verify VPN credentials and configuration
3. Review Gluetun network settings
4. Check firewall rules

## Related Documentation

- [Gluetun Integration](../README.md#vpn-integration) - VPN setup and configuration
- [API Documentation](API.md) - VPN status endpoint details
- [Health Monitoring](../README.md#health-monitoring) - Engine health checks
- [Stale Stream Cleanup](STALE_STREAM_CLEANUP.md) - Related cleanup processes
