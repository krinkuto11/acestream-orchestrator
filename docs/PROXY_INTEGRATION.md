# Proxy Integration

## Overview

The orchestrator provides comprehensive status information and error handling to help proxies make intelligent decisions during failures. This document describes the integration workflow, error scenarios, and recommended handling strategies.

## Status Monitoring

### GET /orchestrator/status

Provides real-time system status including:
- **Overall status**: `healthy`, `degraded`, or `unavailable`
- **Engine counts**: total, running, healthy, unhealthy
- **Stream counts**: active and total
- **Capacity**: available slots, limits
- **VPN status**: connection state, health
- **Provisioning status**: whether new engines can be created, blocked reasons with recovery guidance

**Example Response:**
```json
{
  "status": "healthy",
  "engines": {
    "total": 10,
    "running": 10,
    "healthy": 9,
    "unhealthy": 1
  },
  "provisioning": {
    "can_provision": true,
    "circuit_breaker_state": "closed",
    "blocked_reason": null,
    "blocked_reason_details": null
  },
  "timestamp": "2025-10-13T14:00:00Z"
}
```

**When Provisioning is Blocked:**
```json
{
  "status": "degraded",
  "provisioning": {
    "can_provision": false,
    "circuit_breaker_state": "open",
    "blocked_reason": "Circuit breaker is open",
    "blocked_reason_details": {
      "code": "circuit_breaker",
      "message": "Circuit breaker is open due to repeated failures",
      "recovery_eta_seconds": 180,
      "can_retry": false,
      "should_wait": true
    }
  }
}
```

**Recommended Usage:**
- Poll every 30 seconds to monitor orchestrator health
- Check before attempting to provision new engines
- Use `blocked_reason_details` to decide retry strategy

## Workflow

### 1) Provision engine (optional on-demand)
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"labels":{"stream_id":"ch-42"}}' \
  http://localhost:8000/provision/acestream
# → host_http_port e.g. 19023
```

**Success Response (200):**
```json
{
  "container_id": "abc123...",
  "container_name": "acestream-5",
  "host_http_port": 19023,
  "container_http_port": 6878,
  "container_https_port": 6879
}
```

**Error Response (503) - Provisioning Blocked:**
```json
{
  "detail": {
    "error": "provisioning_blocked",
    "code": "vpn_disconnected",
    "message": "VPN connection is required but currently disconnected",
    "recovery_eta_seconds": 60,
    "can_retry": true,
    "should_wait": true
  }
}
```

### 2) Start playback against the engine
The proxy calls the engine with `format=json` to get control URLs.
```bash
curl "http://127.0.0.1:19023/ace/manifest.m3u8?format=json&infohash=0a48..."
# response.playback_url, response.stat_url, response.command_url
```
### 3) Emit `stream_started`
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "container_id":"<docker_id>",
    "engine":{"host":"127.0.0.1","port":19023},
    "stream":{"key_type":"infohash","key":"0a48..."},
    "session":{
      "playback_session_id":"…",
      "stat_url":"http://127.0.0.1:19023/ace/stat/…",
      "command_url":"http://127.0.0.1:19023/ace/cmd/…",
      "is_live":1
    },
    "labels":{"stream_id":"ch-42"}
  }' \
  http://localhost:8000/events/stream_started
```

**Response (200):**
```json
{
  "id": "0a48...|abc123",
  "status": "started",
  "container_id": "abc123...",
  ...
}
```
### 4) Emit `stream_ended`
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"container_id":"<docker_id>","stream_id":"ch-42","reason":"player_stopped"}' \
  http://localhost:8000/events/stream_ended
```

### 5) Query
 - `GET /streams?status=started`
 - `GET /streams/{id}/stats`
 - `GET /by-label?key=stream_id&value=ch-42` (protected)

Notes:
 - `stream_id` in `labels` helps correlate.
 - If you don't send `stream_id`, the orchestrator will generate one with `key|playback_session_id`.

## Error Scenarios & Recovery

### Scenario 1: VPN Disconnected

**Symptoms:**
- Provisioning requests return 503
- `error.code` = "vpn_disconnected"
- Existing engines may timeout

**Orchestrator Status:**
```json
{
  "status": "degraded",
  "vpn": {
    "enabled": true,
    "connected": false
  },
  "provisioning": {
    "can_provision": false,
    "blocked_reason_details": {
      "code": "vpn_disconnected",
      "recovery_eta_seconds": 60,
      "should_wait": true
    }
  }
}
```

**Proxy Behavior:**
- **DO NOT** fail streams immediately
- **WAIT** for VPN to reconnect (typically < 60s)
- **RETRY** provisioning after recovery_eta_seconds
- **KEEP** existing connections alive (serve buffered content or placeholder)
- **POLL** /orchestrator/status every 5-10 seconds during outage

**Recovery:**
- VPN reconnects automatically
- Circuit breaker may open if failures persist
- Engines restart automatically (if configured)

### Scenario 2: Circuit Breaker Open

**Symptoms:**
- Multiple provisioning failures trigger circuit breaker
- `error.code` = "circuit_breaker"
- Provisioning blocked for recovery period

**Orchestrator Status:**
```json
{
  "status": "degraded",
  "provisioning": {
    "can_provision": false,
    "circuit_breaker_state": "open",
    "blocked_reason_details": {
      "code": "circuit_breaker",
      "recovery_eta_seconds": 180,
      "should_wait": true,
      "can_retry": false
    }
  }
}
```

**Proxy Behavior:**
- **DO NOT** retry provisioning while circuit is open
- **WAIT** for circuit breaker to enter half_open state
- **USE** existing healthy engines
- **QUEUE** new stream requests or return "service busy" to clients
- **MONITOR** status endpoint for state change

**Recovery:**
- After recovery_timeout (default 300s), circuit enters half_open
- Successful provisioning closes circuit
- Manual reset available via `/health/circuit-breaker/reset` (admin)

### Scenario 3: Maximum Capacity Reached

**Symptoms:**
- All engines at max capacity (MAX_REPLICAS reached)
- `error.code` = "max_capacity"
- No free engines available

**Orchestrator Status:**
```json
{
  "status": "healthy",
  "capacity": {
    "total": 20,
    "used": 20,
    "available": 0,
    "max_replicas": 20
  },
  "provisioning": {
    "can_provision": false,
    "blocked_reason_details": {
      "code": "max_capacity",
      "recovery_eta_seconds": 120,
      "should_wait": true
    }
  }
}
```

**Proxy Behavior:**
- **WAIT** for existing streams to end
- **IMPLEMENT** queue for new requests
- **RETURN** "service busy" to clients (503) with Retry-After header
- **DO NOT** fail existing streams
- **CONSIDER** load shedding if queue grows too large

**Recovery:**
- Streams end naturally, freeing capacity
- If AUTO_DELETE enabled, engines removed after grace period
- New engines can be provisioned when under MAX_REPLICAS

### Scenario 4: Engine Startup Slow

**Symptoms:**
- Engine provisioned but not ready
- Timeout connecting to engine
- Engine shows as "unhealthy"

**Proxy Behavior:**
- **RETRY** connection with exponential backoff (1s, 2s, 4s, 8s)
- **MAX** 4-5 retries before failing
- **FALLBACK** to another engine if available
- **EMIT** stream_ended if all retries fail

**Recovery:**
- Engine becomes healthy after startup (typically 5-15s)
- Health monitor will detect and mark as healthy

### Scenario 5: Temporary Engine Overload

**Symptoms:**
- Engine responds but slowly
- High latency
- Occasional timeouts

**Proxy Behavior:**
- **INCREASE** timeout for engine requests
- **BUFFER** more aggressively
- **DO NOT** immediately fail stream
- **MONITOR** for recovery

**Recovery:**
- Load decreases as streams end
- Autoscaler may provision additional engines

## Best Practices

### 1. Health Monitoring
```go
// Poll orchestrator status
ticker := time.NewTicker(30 * time.Second)
for {
    select {
    case <-ticker.C:
        status := getOrchestratorStatus()
        updateLocalState(status)
    }
}
```

### 2. Graceful Degradation
- Keep buffered content to smooth over short outages
- Show "loading" or placeholder instead of error
- Only fail after all recovery attempts exhausted

### 3. Client Communication
- Set appropriate Retry-After headers
- Provide meaningful error messages
- Don't expose internal orchestrator details

### 4. Retry Strategy
```go
func shouldRetryProvisioning(errorDetails map[string]interface{}) bool {
    shouldWait, _ := errorDetails["should_wait"].(bool)
    recoveryETA, _ := errorDetails["recovery_eta_seconds"].(int)
    
    if !shouldWait {
        return false // Permanent error
    }
    
    if recoveryETA > 300 {
        return false // Too long to wait
    }
    
    return true
}

func getRetryDelay(errorDetails map[string]interface{}) time.Duration {
    eta, ok := errorDetails["recovery_eta_seconds"].(int)
    if !ok || eta <= 0 {
        return 30 * time.Second // Default
    }
    
    // Wait for half the ETA, then poll
    return time.Duration(eta/2) * time.Second
}
```

### 5. Queue Management
```go
type StreamRequest struct {
    ID          string
    EnqueueTime time.Time
    Retries     int
}

const maxQueueSize = 100
const maxWaitTime = 5 * time.Minute

func shouldEnqueue(status OrchestratorStatus) bool {
    return status.Provisioning.BlockedReasonDetails.ShouldWait &&
           status.Provisioning.BlockedReasonDetails.RecoveryETASeconds < 300
}
```

## Monitoring & Metrics

### Key Metrics to Track
1. **Orchestrator availability**: % of time status is "healthy"
2. **Provisioning success rate**: successful provisions / total attempts
3. **Circuit breaker activations**: count and duration
4. **VPN disconnection frequency**: count per hour
5. **Queue depth**: number of waiting stream requests
6. **Recovery time**: time from degraded to healthy

### Alerting Thresholds
- Orchestrator unavailable > 1 minute
- Circuit breaker open > 5 minutes
- Queue depth > 50 requests
- VPN disconnects > 3 per hour