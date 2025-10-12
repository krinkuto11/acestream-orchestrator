# Acexy Proxy Integration Guide

This document describes how to integrate the Acexy AceStream proxy with the Orchestrator for dynamic engine management and load balancing.

## Overview

Acexy is a Go-based proxy that sits between clients and AceStream engines. When integrated with the Orchestrator, it provides:

- **Dynamic Engine Pool**: Automatically provisions engines as needed
- **Load Balancing**: Distributes streams across available engines
- **High Availability**: Automatic failover to healthy engines
- **Zero Manual Configuration**: Engines are provisioned on-demand

## Architecture

```
Client → Acexy Proxy → Orchestrator → AceStream Engines
                     ↓
                VPN (Gluetun)
```

## Configuration

### Orchestrator Setup

1. Configure the orchestrator with appropriate settings in `.env`:

```bash
# Minimum engines to maintain
MIN_REPLICAS=1

# Maximum engines allowed
MAX_REPLICAS=10

# Target Docker image for engines
TARGET_IMAGE=ghcr.io/krinkuto11/acestream-http-proxy:latest

# API key for authentication
API_KEY=your-secret-key-here

# Optional: VPN configuration
GLUETUN_CONTAINER_NAME=gluetun
VPN_RESTART_ENGINES_ON_RECONNECT=true
```

2. Start the orchestrator:

```bash
docker-compose up -d
```

### Acexy Setup

Configure acexy to use the orchestrator:

```bash
# Orchestrator URL
export ACEXY_ORCH_URL=http://orchestrator:8000

# Orchestrator API key
export ACEXY_ORCH_APIKEY=your-secret-key-here

# Maximum streams per engine (recommended: 1 for best performance)
export ACEXY_MAX_STREAMS_PER_ENGINE=1

# Fallback engine (used if orchestrator is unavailable)
export ACEXY_HOST=localhost
export ACEXY_PORT=6878
```

Start acexy:

```bash
docker run -d \
  -e ACEXY_ORCH_URL=http://orchestrator:8000 \
  -e ACEXY_ORCH_APIKEY=your-secret-key-here \
  -e ACEXY_MAX_STREAMS_PER_ENGINE=1 \
  -p 6878:6878 \
  ghcr.io/javinator9889/acexy:latest
```

## How It Works

### Engine Selection Flow

1. **Client Request**: Client requests a stream via acexy
2. **Engine Selection**: Acexy calls `/engines` to get available engines
3. **Load Check**: For each engine, acexy checks active stream count
4. **Engine Selection**: Selects engine with:
   - Healthy status (prioritized)
   - Lowest active stream count
   - Oldest last_stream_usage (among engines with same health and load)
5. **Provisioning**: If all engines are at capacity, provisions new engine via `/provision/acestream`
6. **Stream Start**: Routes stream to selected engine
7. **Event Tracking**: Emits `stream_started` and `stream_ended` events to orchestrator

### Provisioning Flow

When acexy needs to provision a new engine:

1. **Call Provision API**:
   ```bash
   POST /provision/acestream
   Authorization: Bearer {API_KEY}
   Content-Type: application/json
   
   {
     "labels": {},
     "env": {}
   }
   ```

2. **Response**:
   ```json
   {
     "container_id": "abc123...",
     "container_name": "acestream-1",
     "host_http_port": 19000,
     "container_http_port": 40000,
     "container_https_port": 45000
   }
   ```

3. **State Sync**: Orchestrator immediately adds engine to state (via reindex)

4. **Engine Ready**: Acexy can immediately query `/engines` and find the new engine

5. **Wait for Health**: Acexy waits 10 seconds for engine initialization

6. **Use Engine**: Routes stream to new engine

## API Endpoints

### For Acexy Integration

#### GET /engines
Get all available engines.

**Response**:
```json
[
  {
    "container_id": "abc123...",
    "container_name": "acestream-1",
    "host": "gluetun",
    "port": 19000,
    "health_status": "healthy",
    "last_health_check": "2024-01-01T12:00:00Z",
    "last_stream_usage": "2024-01-01T11:55:00Z",
    "streams": ["stream1", "stream2"]
  }
]
```

#### GET /streams?container_id={id}&status=started
Get active streams for a specific engine.

**Response**:
```json
[
  {
    "id": "stream1",
    "container_id": "abc123...",
    "status": "started",
    "started_at": "2024-01-01T12:00:00Z"
  }
]
```

#### POST /provision/acestream
Provision a new engine.

**Request**:
```json
{
  "labels": {"source": "acexy"},
  "env": {}
}
```

**Response**: See provisioning flow above

**Error Responses**:
- `503`: VPN not available (if VPN is configured)
- `503`: Circuit breaker open (too many recent failures)
- `500`: Other provisioning errors

#### POST /events/stream_started
Notify orchestrator that a stream started (called by acexy).

**Request**:
```json
{
  "container_id": "abc123...",
  "engine": {
    "host": "gluetun",
    "port": 19000
  },
  "stream": {
    "key_type": "infohash",
    "key": "abc123..."
  },
  "session": {
    "playback_session_id": "xyz789",
    "stat_url": "http://...",
    "command_url": "http://...",
    "is_live": 1
  }
}
```

#### POST /events/stream_ended
Notify orchestrator that a stream ended (called by acexy).

**Request**:
```json
{
  "stream_id": "stream1",
  "reason": "client_disconnect"
}
```

#### GET /orchestrator/status
Get comprehensive orchestrator status (useful for acexy health checks).

**Response**:
```json
{
  "status": "healthy",
  "engines": {
    "total": 3,
    "running": 3,
    "healthy": 2,
    "unhealthy": 1
  },
  "streams": {
    "active": 5,
    "total": 10
  },
  "capacity": {
    "total": 3,
    "used": 5,
    "available": -2,
    "max_replicas": 10,
    "min_replicas": 1
  },
  "vpn": {
    "enabled": true,
    "connected": true,
    "health": "healthy",
    "container": "gluetun",
    "forwarded_port": 54321
  },
  "provisioning": {
    "can_provision": true,
    "circuit_breaker_state": "closed",
    "last_failure": null,
    "blocked_reason": null
  },
  "config": {
    "auto_delete": true,
    "grace_period_s": 30,
    "target_image": "ghcr.io/krinkuto11/acestream-http-proxy:latest"
  }
}
```

#### GET /vpn/status
Get VPN status (if VPN integration is enabled).

**Response**:
```json
{
  "enabled": true,
  "status": "running",
  "container": "gluetun",
  "health": "healthy",
  "connected": true,
  "forwarded_port": 54321,
  "last_check_at": "2024-01-01T12:00:00Z"
}
```

## Orchestrator States and Acexy Behavior

### VPN Failure

When VPN (Gluetun) is unhealthy:

- **Orchestrator**: Blocks new engine provisioning
- **Acexy**: Gets 503 error when trying to provision
- **Behavior**: Acexy falls back to existing healthy engines or returns error to client

**Status Response**:
```json
{
  "vpn": {
    "enabled": true,
    "connected": false,
    "health": "unhealthy"
  },
  "provisioning": {
    "can_provision": false,
    "blocked_reason": "VPN not connected"
  }
}
```

### Circuit Breaker Open

When too many provisioning failures occur:

- **Orchestrator**: Opens circuit breaker, blocks provisioning
- **Acexy**: Gets 503 error when trying to provision
- **Behavior**: Acexy uses existing engines or returns error

**Status Response**:
```json
{
  "provisioning": {
    "can_provision": false,
    "circuit_breaker_state": "open",
    "blocked_reason": "Circuit breaker is open"
  }
}
```

### No Available Engines

When all engines are at capacity:

- **Orchestrator**: MIN_REPLICAS engines exist but all are busy
- **Acexy**: Provisions new engine via `/provision/acestream`
- **Behavior**: New engine starts, acexy waits and uses it

### Engine Provisioning

When acexy provisions a new engine:

1. Orchestrator creates Docker container
2. **Immediately** adds to state (via reindex)
3. Returns container info to acexy
4. Acexy waits 10s for initialization
5. Acexy uses engine for stream

**Critical**: Engine appears in `/engines` endpoint immediately after provisioning completes.

## Testing Integration

### Manual Test

Use the provided test script:

```bash
cd tests
./test_provision_behavior.sh
```

This tests:
- Orchestrator accessibility
- Status endpoint
- VPN status
- Provisioning flow
- State synchronization
- Docker container creation

### Automated Test

Run the Python integration test:

```bash
python3 tests/test_orchestrator_acexy_integration.py
```

This verifies:
- Provisioning creates containers
- State updates immediately
- Engines appear in API
- Status endpoint works
- VPN status accessible

## Monitoring

### Key Metrics

Monitor these endpoints for health:

1. **GET /orchestrator/status**: Overall health
2. **GET /vpn/status**: VPN connectivity
3. **GET /health/status**: Engine health details
4. **GET /engines**: Available engines

### Health Checks

Acexy should periodically (every 30-60s):

1. Call `/orchestrator/status` to check:
   - Orchestrator is reachable
   - VPN is connected (if enabled)
   - Provisioning is available
   - Capacity is sufficient

2. If status shows issues:
   - Log warning
   - Use existing engines only
   - Retry provisioning after delay

## Troubleshooting

### Engine Not Found After Provisioning

**Symptom**: Acexy provisions engine but can't find it in `/engines`

**Cause**: State not synchronized (should be fixed)

**Solution**: Ensure orchestrator version includes reindex after provision

### Provisioning Returns 503

**Possible Causes**:

1. **VPN Not Connected**: Check `/vpn/status`, ensure Gluetun is healthy
2. **Circuit Breaker Open**: Too many recent failures, wait or reset via `/health/circuit-breaker/reset`
3. **Resource Limits**: MAX_REPLICAS reached

### Engines Show as Unhealthy

**Check**:
1. Engine containers are running: `docker ps`
2. Engine API is responsive: `curl http://engine-host:port/webui/api/service?method=get_status`
3. VPN is connected (if using VPN)

### Stream Routing Fails

**Check**:
1. Engine is in `/engines` list
2. Engine health_status is "healthy"
3. Engine has capacity (active_streams < max_streams_per_engine)
4. Network connectivity between acexy and engine

## Best Practices

1. **Set MAX_STREAMS_PER_ENGINE=1**: One stream per engine for best performance
2. **Monitor VPN**: Use `/vpn/status` to detect VPN issues early
3. **Use Status Endpoint**: Check `/orchestrator/status` before provisioning
4. **Handle Errors**: Gracefully handle 503 errors from provisioning
5. **Set MIN_REPLICAS**: Keep at least 1-2 engines ready for immediate use
6. **Monitor Capacity**: Alert when capacity is low
7. **Use Health Checks**: Only route to engines with health_status="healthy"

## Example Acexy Usage

```go
// Check orchestrator status before provisioning
func (c *orchClient) checkCanProvision() bool {
    resp, err := c.hc.Get(c.base + "/orchestrator/status")
    if err != nil {
        return false
    }
    defer resp.Body.Close()
    
    var status map[string]interface{}
    json.NewDecoder(resp.Body).Decode(&status)
    
    provisioning := status["provisioning"].(map[string]interface{})
    return provisioning["can_provision"].(bool)
}

// Select engine with status check
func (c *orchClient) SelectBestEngine() (string, int, error) {
    // Check status first
    if !c.checkCanProvision() {
        return "", 0, fmt.Errorf("orchestrator cannot provision engines")
    }
    
    // Get engines and select best...
    // If no capacity, provision new engine
}
```

## Migration from Static Configuration

If migrating from static acexy configuration:

1. Keep fallback configuration:
   ```bash
   ACEXY_HOST=localhost
   ACEXY_PORT=6878
   ```

2. Add orchestrator configuration:
   ```bash
   ACEXY_ORCH_URL=http://orchestrator:8000
   ACEXY_ORCH_APIKEY=your-key
   ```

3. Acexy will prefer orchestrator but fall back to static if unavailable

4. Gradually increase MAX_REPLICAS as needed

5. Monitor both endpoints during migration

## Support

For issues:
1. Check orchestrator logs: `docker logs orchestrator`
2. Check acexy logs: `docker logs acexy`
3. Run test scripts in `tests/`
4. Review this documentation
5. Check VPN status if VPN is configured
