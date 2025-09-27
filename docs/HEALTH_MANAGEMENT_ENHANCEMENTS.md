# Health Management Enhancements

This document describes the comprehensive health management system implemented to ensure service availability at all times.

## Overview

The enhanced health management system ensures that acestream engine containers are always available by:

1. **Proactive Health Monitoring** - Continuously monitors engine health and automatically replaces unhealthy instances
2. **Service Availability Guarantees** - Always maintains minimum healthy engines during replacements
3. **Circuit Breaker Pattern** - Prevents cascading failures during problematic periods
4. **Configurable Behavior** - All thresholds and timings can be tuned for specific environments

## Components

### 1. Health Manager Service (`HealthManager`)

The core service responsible for maintaining engine health and availability.

**Key Features:**
- Continuous health monitoring with configurable intervals
- Automatic unhealthy engine replacement while maintaining service availability
- Gradual replacement strategy that never interrupts service
- Health-based provisioning that considers engine health, not just count

**Configuration:**
```bash
HEALTH_CHECK_INTERVAL_S=20              # How often to check engine health
HEALTH_FAILURE_THRESHOLD=3              # Consecutive failures to mark unhealthy
HEALTH_UNHEALTHY_GRACE_PERIOD_S=60      # Time before replacing unhealthy engines
HEALTH_REPLACEMENT_COOLDOWN_S=60        # Wait time between replacements
```

### 2. Circuit Breaker Pattern (`CircuitBreaker`)

Prevents rapid provisioning attempts when engines consistently fail, allowing time for underlying issues to be resolved.

**States:**
- **CLOSED** - Normal operation, operations allowed
- **OPEN** - Operations blocked due to failures
- **HALF_OPEN** - Testing recovery, limited operations allowed

**Configuration:**
```bash
CIRCUIT_BREAKER_FAILURE_THRESHOLD=5         # Failures before opening circuit
CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S=300      # Time before testing recovery
CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD=3     # Failures for replacement operations
CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S=180   # Recovery time for replacements
```

### 3. Enhanced Autoscaler

The autoscaler now considers engine health status when making provisioning decisions.

**Improvements:**
- Health-aware provisioning (considers healthy vs total engine count)
- Circuit breaker integration to prevent cascading failures
- Better error handling and retry logic

## API Endpoints

### Health Status
```bash
GET /health/status
```

Returns detailed health management information:
```json
{
  "total_engines": 5,
  "healthy_engines": 4,
  "unhealthy_engines": 1,
  "marked_for_replacement": 1,
  "minimum_required": 3,
  "health_check_interval": 20,
  "circuit_breakers": {
    "general": {
      "state": "closed",
      "failure_count": 0,
      "failure_threshold": 5,
      "last_failure_time": null,
      "last_success_time": "2023-09-27T10:30:00Z",
      "recovery_timeout": 300
    },
    "replacement": {
      "state": "closed",
      "failure_count": 0,
      "failure_threshold": 3,
      "recovery_timeout": 180
    }
  }
}
```

### Circuit Breaker Reset
```bash
POST /health/circuit-breaker/reset
POST /health/circuit-breaker/reset?operation_type=general
```

Manually reset circuit breakers (requires API key).

## Health Management Workflow

### 1. Health Detection
- Every `HEALTH_CHECK_INTERVAL_S` seconds, check all engines
- Track consecutive failures per engine
- Mark engines unhealthy after `HEALTH_FAILURE_THRESHOLD` failures

### 2. Replacement Decision
- Only replace engines after `HEALTH_UNHEALTHY_GRACE_PERIOD_S` grace period
- Ensure minimum healthy engines before starting replacement
- Wait `HEALTH_REPLACEMENT_COOLDOWN_S` between replacements

### 3. Safe Replacement Process
1. Start replacement engine first
2. Wait for new engine to become healthy
3. Verify sufficient healthy engines remain
4. Remove unhealthy engine
5. Update state and cleanup

### 4. Circuit Breaker Protection
- Monitor provisioning success/failure rates
- Open circuit after consecutive failures
- Prevent provisioning attempts during problem periods
- Automatically test recovery after timeout

## Benefits

### Service Continuity
- **Zero downtime replacements** - Healthy engines remain available
- **Minimum healthy guarantee** - Always maintain required capacity
- **Gradual replacement** - Never replace multiple engines simultaneously

### Automatic Recovery
- **Self-healing** - Failed engines automatically replaced
- **No manual intervention** - Fully automated health management
- **Proactive detection** - Issues caught before they affect service

### System Stability
- **Circuit breaker protection** - Prevents cascading failures
- **Configurable thresholds** - Tune behavior for your environment
- **Comprehensive monitoring** - Full visibility into health status

### Operational Excellence
- **API integration** - Monitor health via REST endpoints
- **Manual overrides** - Force reset circuit breakers when needed
- **Detailed logging** - Complete audit trail of health decisions

## Best Practices

### Configuration Tuning
1. **Health Check Interval** - Balance monitoring frequency vs resource usage
2. **Failure Threshold** - Avoid false positives while catching real issues
3. **Grace Periods** - Allow time for temporary issues to resolve
4. **Circuit Breaker Thresholds** - Prevent cascading failures without over-protection

### Monitoring
1. Monitor the `/health/status` endpoint regularly
2. Alert on high unhealthy engine counts
3. Track circuit breaker state changes
4. Monitor replacement frequency patterns

### Troubleshooting
1. Check circuit breaker status if provisioning stops
2. Review health check logs for engine failure patterns
3. Adjust thresholds based on environment characteristics
4. Use manual circuit breaker reset for emergency recovery

## Example Deployment

### High Availability Setup
```bash
# Aggressive health monitoring for critical services
HEALTH_CHECK_INTERVAL_S=10
HEALTH_FAILURE_THRESHOLD=2
HEALTH_UNHEALTHY_GRACE_PERIOD_S=30
MIN_REPLICAS=5
```

### Resource Constrained Setup
```bash
# Conservative settings for limited resources
HEALTH_CHECK_INTERVAL_S=60
HEALTH_FAILURE_THRESHOLD=5
HEALTH_UNHEALTHY_GRACE_PERIOD_S=300
CIRCUIT_BREAKER_FAILURE_THRESHOLD=10
```

### Development Setup
```bash
# Fast feedback for development
HEALTH_CHECK_INTERVAL_S=5
HEALTH_FAILURE_THRESHOLD=1
HEALTH_UNHEALTHY_GRACE_PERIOD_S=10
MIN_REPLICAS=1
```

## Migration Guide

### From Previous Version
1. The new health management system is backward compatible
2. Existing configurations will continue to work
3. New features are opt-in via configuration
4. Monitor the new health endpoints for additional insights

### Recommended Migration Steps
1. Deploy with default settings
2. Monitor health status for patterns
3. Tune thresholds based on your environment
4. Enable more aggressive settings as confidence builds