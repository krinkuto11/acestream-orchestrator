# Enhanced Reliability Features

The acestream-orchestrator now includes enhanced reliability features to ensure the `/engines` endpoint is always up-to-date and the system efficiently manages container resources.

## New Features

### 1. Continuous Docker Monitoring

A background monitoring service continuously syncs the internal state with Docker containers:

- **Frequency**: Every 10 seconds (configurable via `MONITOR_INTERVAL_S`)
- **Purpose**: Detects when containers are stopped/started outside the orchestrator
- **Actions**: Removes stale engines from state, adds new containers discovered in Docker

### 2. Grace Period for Empty Engines

Empty engines (those without active streams) are not immediately deleted:

- **Grace Period**: 30 seconds (configurable via `ENGINE_GRACE_PERIOD_S`)
- **Purpose**: Handles rapid stream changes where engines become briefly empty
- **Benefit**: Faster stream switching since existing engines don't need to be recreated

### 3. Free Engine Management

The autoscaler now distinguishes between "free" and "used" engines:

- **Logic**: `MIN_REPLICAS` now means minimum FREE engines, not total engines
- **Example**: With `MIN_REPLICAS=3` and 2 engines in use, the system maintains 5 total engines (2 used + 3 free)
- **Benefit**: Always have available engines for new streams

### 4. Periodic Autoscaling

Autoscaling runs continuously instead of just at startup:

- **Frequency**: Every 30 seconds (configurable via `AUTOSCALE_INTERVAL_S`)
- **Purpose**: Maintains desired engine pool continuously
- **Actions**: Starts new engines when needed, cleans up engines after grace period

### 5. Enhanced /engines Endpoint

The `/engines` endpoint now includes Docker verification:

- **Verification**: Checks actual Docker containers against internal state
- **Fallback**: Still returns all engines even if Docker verification fails
- **Logging**: Logs mismatches for debugging without breaking functionality

## Configuration

Add these environment variables to customize the behavior:

```bash
# How often to check Docker containers (seconds)
MONITOR_INTERVAL_S=10

# How long to wait before deleting empty engines (seconds)  
ENGINE_GRACE_PERIOD_S=30

# How often to run autoscaling (seconds)
AUTOSCALE_INTERVAL_S=30
```

## Benefits

1. **High Reliability**: `/engines` endpoint always reflects reality
2. **Fast Stream Switching**: Grace period prevents unnecessary container recreation
3. **Optimal Resource Usage**: Free engine logic ensures availability without waste
4. **Self-Healing**: Continuous monitoring fixes state inconsistencies automatically
5. **Configurable**: All timing aspects can be tuned for your use case

## Backward Compatibility

All existing functionality remains unchanged. The new features enhance reliability without breaking existing behavior:

- Existing tests pass without modification
- API endpoints have the same interface
- Configuration is backward compatible (defaults maintain old behavior)
- Grace period can be disabled by setting `ENGINE_GRACE_PERIOD_S=0`