# Orchestrator-Proxy Communication Flow

This document provides a high-level overview of the enhanced communication between the orchestrator and proxy.

## Normal Operation Flow

```
┌─────────┐         ┌──────────┐         ┌──────────────┐         ┌────────┐
│ Client  │         │  Proxy   │         │ Orchestrator │         │ Engine │
└────┬────┘         └─────┬────┘         └──────┬───────┘         └────┬───┘
     │                    │                     │                      │
     │ 1. Stream Request  │                     │                      │
     ├───────────────────>│                     │                      │
     │                    │                     │                      │
     │                    │ 2. GET /orchestrator/status               │
     │                    ├────────────────────>│                      │
     │                    │                     │                      │
     │                    │ 3. Status: healthy  │                      │
     │                    │    can_provision: true                     │
     │                    │<────────────────────┤                      │
     │                    │                     │                      │
     │                    │ 4. GET /engines     │                      │
     │                    ├────────────────────>│                      │
     │                    │                     │                      │
     │                    │ 5. Available engines│                      │
     │                    │<────────────────────┤                      │
     │                    │                     │                      │
     │                    │ 6. Select best      │                      │
     │                    │    engine           │                      │
     │                    │                     │                      │
     │                    │ 7. Start stream     │                      │
     │                    ├─────────────────────┼─────────────────────>│
     │                    │                     │                      │
     │                    │ 8. Stream metadata  │                      │
     │                    │<────────────────────┼──────────────────────┤
     │                    │                     │                      │
     │                    │ 9. POST /events/stream_started            │
     │                    ├────────────────────>│                      │
     │                    │                     │                      │
     │ 10. Stream ready   │                     │                      │
     │<───────────────────┤                     │                      │
     │                    │                     │                      │
     │ 11. Video data     │                     │                      │
     │<──────────────────────────────────────────────────────────────>│
     │                    │                     │                      │
     │ 12. Stream ends    │                     │                      │
     ├───────────────────>│                     │                      │
     │                    │                     │                      │
     │                    │ 13. POST /events/stream_ended             │
     │                    ├────────────────────>│                      │
     │                    │                     │                      │
```

## VPN Disconnection Flow (Enhanced)

```
┌─────────┐    ┌──────────┐    ┌──────────────┐    ┌────────┐    ┌────────┐
│ Client  │    │  Proxy   │    │ Orchestrator │    │ Gluetun│    │ Engine │
└────┬────┘    └─────┬────┘    └──────┬───────┘    └───┬────┘    └────┬───┘
     │               │                │                │              │
     │ Stream Request│                │                │              │
     ├──────────────>│                │                │              │
     │               │                │                │              │
     │               │ POST /provision│                │              │
     │               ├───────────────>│                │              │
     │               │                │                │              │
     │               │                │  Check VPN     │              │
     │               │                ├───────────────>│              │
     │               │                │                │              │
     │               │                │  ❌ VPN DOWN   │              │
     │               │                │<───────────────┤              │
     │               │                │                │              │
     │               │  503 Error     │                │              │
     │               │  {             │                │              │
     │               │   code: "vpn_disconnected"       │              │
     │               │   recovery_eta: 60               │              │
     │               │   should_wait: true              │              │
     │               │  }             │                │              │
     │               │<───────────────┤                │              │
     │               │                │                │              │
     │  Loading...   │  KEEP STREAM   │                │              │
     │<──────────────┤  ALIVE         │                │              │
     │               │  (buffering)   │                │              │
     │               │                │                │              │
     │               │  Poll status   │                │              │
     │               │  every 10s     │                │              │
     │               ├───────────────>│                │              │
     │               │                │                │              │
     │               │  Still degraded│                │              │
     │               │<───────────────┤                │              │
     │               │                │                │              │
     │               │    ... wait for VPN ...         │              │
     │               │                │                │              │
     │               │                │                │  ✅ VPN UP  │
     │               │                │                │<─────────────┤
     │               │                │                │              │
     │               │  Poll status   │                │              │
     │               ├───────────────>│                │              │
     │               │                │                │              │
     │               │  Healthy ✅    │                │              │
     │               │  can_provision: true            │              │
     │               │<───────────────┤                │              │
     │               │                │                │              │
     │               │  Retry provision                │              │
     │               ├───────────────>│────────────────┼─────────────>│
     │               │                │                │              │
     │  Stream ready │                │                │              │
     │<──────────────┤                │                │              │
     │               │                │                │              │
```

**Key Improvements:**
- ✅ Proxy keeps stream alive during VPN reconnection
- ✅ Polls status to detect recovery
- ✅ Retries after recovery ETA
- ✅ User sees loading/buffering instead of error

## Circuit Breaker Flow (Enhanced)

```
┌─────────┐         ┌──────────┐         ┌──────────────┐
│ Client  │         │  Proxy   │         │ Orchestrator │
└────┬────┘         └─────┬────┘         └──────┬───────┘
     │                    │                     │
     │ (Multiple failures occur)                │
     │                    │  ❌ Fail 1          │
     │                    │  ❌ Fail 2          │
     │                    │  ❌ Fail 3          │
     │                    │  ❌ Fail 4          │
     │                    │  ❌ Fail 5          │
     │                    │                     │
     │                    │         Circuit Breaker OPENS
     │                    │                     │
     │  Stream Request    │                     │
     ├───────────────────>│                     │
     │                    │                     │
     │                    │  POST /provision    │
     │                    ├────────────────────>│
     │                    │                     │
     │                    │  503 Error          │
     │                    │  {                  │
     │                    │   code: "circuit_breaker"
     │                    │   state: "open"     │
     │                    │   recovery_eta: 180 │
     │                    │   can_retry: false  │
     │                    │   should_wait: true │
     │                    │  }                  │
     │                    │<────────────────────┤
     │                    │                     │
     │  503 Service Busy  │                     │
     │  Retry-After: 180  │  ENQUEUE REQUEST    │
     │<───────────────────┤                     │
     │                    │                     │
     │                    │  Poll status        │
     │                    │  every 30s          │
     │                    ├────────────────────>│
     │                    │                     │
     │                    │  ... wait 180s ...  │
     │                    │                     │
     │                    │     Circuit → HALF_OPEN
     │                    │                     │
     │  Retry Request     │                     │
     ├───────────────────>│                     │
     │                    │                     │
     │                    │  Retry provision    │
     │                    ├────────────────────>│
     │                    │                     │
     │                    │  ✅ Success         │
     │                    │<────────────────────┤
     │                    │                     │
     │                    │     Circuit → CLOSED
     │                    │                     │
     │  Stream ready      │                     │
     │<───────────────────┤                     │
     │                    │                     │
```

**Key Improvements:**
- ✅ Proxy respects circuit breaker state
- ✅ Queues requests instead of failing
- ✅ Waits for recovery timeout
- ✅ Retries after circuit half-opens

## Capacity Exhaustion Flow (Enhanced)

```
┌─────────┐         ┌──────────┐         ┌──────────────┐
│ Client  │         │  Proxy   │         │ Orchestrator │
└────┬────┘         └─────┬────┘         └──────┬───────┘
     │                    │                     │
     │  Stream Request    │                     │
     ├───────────────────>│                     │
     │                    │                     │
     │                    │  GET /orchestrator/status
     │                    ├────────────────────>│
     │                    │                     │
     │                    │  Status: healthy    │
     │                    │  capacity: {        │
     │                    │    total: 20,       │
     │                    │    used: 20,        │
     │                    │    available: 0     │
     │                    │  }                  │
     │                    │<────────────────────┤
     │                    │                     │
     │                    │  POST /provision    │
     │                    ├────────────────────>│
     │                    │                     │
     │                    │  503 Error          │
     │                    │  {                  │
     │                    │   code: "max_capacity"
     │                    │   recovery_eta: 120 │
     │                    │   should_wait: true │
     │                    │  }                  │
     │                    │<────────────────────┤
     │                    │                     │
     │  503 Service Busy  │                     │
     │  Retry-After: 120  │  ENQUEUE REQUEST    │
     │<───────────────────┤                     │
     │                    │                     │
     │                    │  ... streams end .. │
     │                    │                     │
     │                    │  POST /events/stream_ended
     │                    │<────────────────────┤
     │                    │                     │
     │                    │  Capacity freed!    │
     │                    │  PROCESS QUEUE      │
     │                    │                     │
     │  Stream ready      │                     │
     │<───────────────────┤                     │
     │                    │                     │
```

**Key Improvements:**
- ✅ Proxy distinguishes "at capacity" from "broken"
- ✅ Queues requests with Retry-After header
- ✅ Processes queue when capacity freed
- ✅ Prevents retry storm during capacity issues

## Health Monitoring Pattern

```
┌──────────┐                    ┌──────────────┐
│  Proxy   │                    │ Orchestrator │
└─────┬────┘                    └──────┬───────┘
      │                                │
      │  Background Health Monitor     │
      │  (every 30 seconds)            │
      │                                │
      │  GET /orchestrator/status      │
      ├───────────────────────────────>│
      │                                │
      │  {                             │
      │    status: "healthy",          │
      │    engines: {...},             │
      │    capacity: {...},            │
      │    vpn: {...},                 │
      │    provisioning: {...},        │
      │    timestamp: "..."            │
      │  }                             │
      │<───────────────────────────────┤
      │                                │
      │  Update Local State:           │
      │  - can_provision               │
      │  - blocked_reason_code         │
      │  - recovery_eta                │
      │  - vpn_connected               │
      │  - capacity_available          │
      │                                │
      │  Log Metrics:                  │
      │  - orchestrator_status         │
      │  - capacity_utilization        │
      │  - provisioning_availability   │
      │                                │
      │  ... 30 seconds later ...      │
      │                                │
      │  GET /orchestrator/status      │
      ├───────────────────────────────>│
      │                                │
```

**Benefits:**
- ✅ Proactive awareness of orchestrator state
- ✅ Early detection of degraded conditions
- ✅ Metrics for monitoring and alerting
- ✅ Informed decision making

## Error Code Matrix

| Code | Meaning | Should Wait | Retry | ETA | Proxy Action |
|------|---------|-------------|-------|-----|--------------|
| `vpn_disconnected` | VPN down | ✅ Yes | ✅ Yes | 60s | Buffer stream, poll status |
| `circuit_breaker` | Too many failures | ✅ Yes | ❌ No | 180-300s | Queue requests, wait |
| `max_capacity` | All engines used | ✅ Yes | ❌ No | 120s | Queue with Retry-After |
| `vpn_error` | VPN issue | ✅ Yes | ✅ Yes | 60s | Retry with backoff |
| `general_error` | Other error | ❌ No | ❌ No | N/A | Fail immediately |

## Status Values

| Status | Meaning | Proxy Action |
|--------|---------|--------------|
| `healthy` | All systems operational | Normal operation |
| `degraded` | Some issues, but working | Increased monitoring, queue if needed |
| `unavailable` | Critical failure | Queue all requests, alert operators |

## Benefits Summary

### Before Enhancement
```
VPN Disconnects → Streams fail immediately → Users see errors
Circuit Opens → Empty error message → No guidance
At Capacity → Generic error → Retry storm
Engine Timeout → Immediate failure → No retry
```

### After Enhancement
```
VPN Disconnects → Stream buffering → Auto-recovery → Seamless UX
Circuit Opens → Queue requests → Wait for recovery → Gradual restart
At Capacity → Queue with ETA → Process when ready → No retry storm
Engine Timeout → Exponential backoff → Success → No user impact
```

## Integration Checklist

### Orchestrator (Complete ✅)
- [x] Enhanced /orchestrator/status endpoint
- [x] Structured error responses
- [x] Recovery ETA calculation
- [x] VPN status in responses
- [x] Capacity information
- [x] Documentation complete
- [x] Tests passing

### Proxy (To Do 📋)
- [ ] Parse structured error responses
- [ ] Update health monitoring
- [ ] Implement retry logic with ETAs
- [ ] Add request queuing (optional)
- [ ] Update metrics
- [ ] Test with orchestrator
- [ ] Deploy gradually

**See docs/ACEXY_INTEGRATION_PROMPT.md for complete implementation guide**

---

## Quick Reference

### Orchestrator Endpoints

```bash
# Get comprehensive status
GET /orchestrator/status

# Provision with error details
POST /provision/acestream
  → 200: Success with container details
  → 503: Blocked with recovery guidance
  → 500: Permanent error

# Report stream lifecycle
POST /events/stream_started
POST /events/stream_ended

# Query state
GET /engines
GET /streams?status=started
```

### Key Response Fields

```json
{
  "provisioning": {
    "blocked_reason_details": {
      "code": "...",              // Error code
      "recovery_eta_seconds": 60, // How long to wait
      "can_retry": true,          // Should retry?
      "should_wait": true         // Wait or fail?
    }
  }
}
```

### Monitoring Queries

```promql
# Orchestrator health
orchestrator_status{status="healthy"}

# Provisioning availability
provisioning_can_provision{} == 1

# Circuit breaker state
circuit_breaker_state{type="general"} == "closed"

# Capacity utilization
(capacity_used / capacity_total) * 100
```

---

**For complete details, see:**
- **IMPLEMENTATION_SUMMARY.md** - Overview and benefits
- **docs/PROXY_INTEGRATION.md** - API reference and workflow
- **docs/ERROR_SCENARIOS.md** - Detailed sequence diagrams
- **docs/ACEXY_INTEGRATION_PROMPT.md** - Complete implementation guide
