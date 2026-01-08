# Orchestrator Error Handling and Intelligent Retry

This document describes the enhanced error handling and intelligent retry features added to acexy's orchestrator integration.

## Overview

The acexy proxy now includes sophisticated error handling for orchestrator integration failures, allowing it to:

1. **Understand structured error responses** from the orchestrator
2. **Implement intelligent retry logic** based on recovery ETAs
3. **Provide user-friendly error messages** to clients
4. **Maintain backward compatibility** with legacy error formats

## Features

### Structured Error Types

The orchestrator can now return detailed error information:

```json
{
  "detail": {
    "error": "provisioning_blocked",
    "code": "vpn_disconnected",
    "message": "VPN is disconnected",
    "recovery_eta_seconds": 60,
    "can_retry": true,
    "should_wait": true
  }
}
```

### Error Codes

The following error codes are supported:

- **`vpn_disconnected`**: VPN is down, typically recovers in 60s
- **`circuit_breaker`**: Too many failures, wait for recovery timeout
- **`max_capacity`**: All engines at capacity, wait for streams to end
- **`vpn_error`**: VPN error during provisioning
- **`general_error`**: Other provisioning errors

### Intelligent Retry Logic

When provisioning fails, acexy will:

1. Parse the error response to extract recovery information
2. Wait based on the `recovery_eta_seconds` field:
   - First retry: Wait for half the ETA
   - Subsequent retries: Wait for full ETA
3. If no ETA is provided, use exponential backoff (30s, 60s, 120s max)
4. Stop retrying if `should_wait` is false (permanent errors)

### User-Friendly HTTP Responses

When provisioning is blocked, clients receive:

```http
HTTP/1.1 503 Service Unavailable
Retry-After: 60
Content-Type: application/json

{
  "error": "Service temporarily unavailable: VPN connection is being restored",
  "retry_after": 60
}
```

Different error codes result in different user messages:

- **vpn_disconnected**: "Service temporarily unavailable: VPN connection is being restored"
- **circuit_breaker**: "Service temporarily unavailable: System is recovering from errors"
- **max_capacity**: "Service at capacity: Please try again in a moment"

## Implementation Details

### Enhanced Health Monitoring

The orchestrator health status now includes:

```go
type OrchestratorHealth struct {
    status            string
    canProvision      bool
    blockedReason     string
    blockedReasonCode string      // NEW: Error code
    recoveryETA       int          // NEW: Estimated recovery time
    shouldWait        bool         // NEW: Whether to wait/retry
    capacity          CapacityInfo // NEW: Capacity information
}
```

### Error Parsing

The `parseProvisionError()` function handles both:

1. **New structured format** (JSON object with error details)
2. **Legacy string format** (simple string message)

For legacy formats, the parser infers error codes from keywords:
- "VPN" → `vpn_disconnected`
- "circuit breaker" → `circuit_breaker`
- "capacity" → `max_capacity`

### Retry Behavior

```go
// calculateWaitTime determines how long to wait before retrying
func calculateWaitTime(recoveryETA, attempt int) int {
    if recoveryETA > 0 {
        if attempt == 1 {
            return recoveryETA / 2  // First retry: half the ETA
        }
        return recoveryETA  // Subsequent: full ETA
    }
    
    // Exponential backoff if no ETA
    waitTime := 30 * (1 << uint(attempt))
    if waitTime > 120 {
        return 120  // Cap at 120 seconds
    }
    return waitTime
}
```

## Usage Examples

### Scenario 1: VPN Disconnection

1. Client requests stream
2. Orchestrator returns 503 with `vpn_disconnected` error (ETA: 60s)
3. Acexy returns 503 to client with `Retry-After: 60` header
4. If client retries immediately:
   - First retry waits 30s (half ETA)
   - Second retry waits 60s (full ETA)
5. Once VPN reconnects, provisioning succeeds

### Scenario 2: Circuit Breaker Open

1. Orchestrator returns 503 with `circuit_breaker` error (ETA: 180s)
2. Acexy waits intelligently:
   - First retry: 90s
   - Second retry: 180s
3. When circuit closes, provisioning proceeds

### Scenario 3: Capacity Exhaustion

1. Orchestrator returns 503 with `max_capacity` error (ETA: 30s)
2. Client receives friendly message: "Service at capacity"
3. As streams end and capacity becomes available, new streams succeed

## Backward Compatibility

The implementation maintains full backward compatibility:

- **Legacy error format** (string) is automatically converted to structured format
- **Existing error handling** continues to work unchanged
- **Fallback behavior** is preserved when orchestrator is unavailable

## Testing

Comprehensive test coverage includes:

1. **Unit tests** for error parsing (both formats)
2. **Integration tests** with mock orchestrator
3. **E2E tests** simulating real scenarios:
   - VPN recovery
   - Circuit breaker recovery
   - Capacity availability
   - Legacy format compatibility

Run tests with:

```bash
cd acexy
go test -v ./...
```

## Configuration

No additional configuration is required. The features activate automatically when the orchestrator is configured via:

```bash
ACEXY_ORCH_URL=http://orchestrator:8000
ACEXY_ORCH_APIKEY=your-api-key  # Optional
```

## Monitoring

Enhanced logging provides visibility into error handling:

```
WARN Provisioning failed, will retry attempt=1 code=vpn_disconnected recovery_eta=60
INFO Waiting before retry based on previous error attempt=2 wait_seconds=30 reason=vpn_disconnected
```

Set `ACEXY_LOG_LEVEL=DEBUG` for detailed orchestrator interaction logs.

## Benefits

1. **Improved Reliability**: Automatic recovery from temporary failures
2. **Better UX**: Clear error messages and retry guidance for clients
3. **Reduced Load**: Intelligent backoff prevents overwhelming the orchestrator
4. **Observability**: Detailed logs help diagnose issues
5. **Future-Proof**: Structured format enables new error types without code changes

## Future Enhancements

Potential improvements for future versions:

1. **Request Queuing**: Queue requests during capacity exhaustion
2. **Metrics/Prometheus**: Export retry and error metrics
3. **Circuit Breaker Client-Side**: Implement client-side circuit breaker
4. **Adaptive Backoff**: Learn optimal wait times from historical data
