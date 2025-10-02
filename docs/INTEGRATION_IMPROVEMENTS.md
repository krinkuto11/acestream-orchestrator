# Acexy-Orchestrator Integration Improvements

This document provides practical improvements to enhance the reliability and performance of the Acexy-Orchestrator integration **without merging** the two services.

## Quick Wins (Implement First)

### 1. Health/Readiness Endpoint

Add a readiness check endpoint for Acexy to verify Orchestrator health before making requests.

**File: `app/main.py`**

```python
@app.get("/health/ready")
def health_ready():
    """
    Readiness check for external services like Acexy.
    Returns ready status and current system state.
    """
    from .services.circuit_breaker import circuit_breaker
    
    ready = not circuit_breaker.state == CircuitState.OPEN
    
    return {
        "ready": ready,
        "engines": len(state.engines),
        "active_streams": sum(1 for e in state.engines.values() for s in e.streams),
        "circuit_state": circuit_breaker.state.value,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
```

**Benefits:**
- Acexy can check Orchestrator health before provisioning
- Prevents unnecessary provision requests when system is overloaded
- Provides visibility into system state

### 2. Enhanced Timeout Configuration

**File: `.env.example`**

Add these environment variables:

```bash
# Orchestrator API timeouts
API_REQUEST_TIMEOUT_S=30
API_LONG_OPERATION_TIMEOUT_S=60

# Provisioning timeouts
PROVISION_TIMEOUT_S=120
PROVISION_STARTUP_GRACE_S=30

# Communication resilience
MAX_RETRIES=3
RETRY_BACKOFF_S=2
```

**File: `app/core/config.py`**

```python
class Config:
    # ... existing config ...
    
    # API timeouts
    API_REQUEST_TIMEOUT_S: int = int(os.getenv("API_REQUEST_TIMEOUT_S", "30"))
    API_LONG_OPERATION_TIMEOUT_S: int = int(os.getenv("API_LONG_OPERATION_TIMEOUT_S", "60"))
    
    # Provisioning timeouts
    PROVISION_TIMEOUT_S: int = int(os.getenv("PROVISION_TIMEOUT_S", "120"))
    PROVISION_STARTUP_GRACE_S: int = int(os.getenv("PROVISION_STARTUP_GRACE_S", "30"))
    
    # Retry configuration
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_BACKOFF_S: int = int(os.getenv("RETRY_BACKOFF_S", "2"))
```

**Benefits:**
- Configurable timeouts for different operations
- Allows tuning based on actual system performance
- Prevents premature timeout failures

### 3. Request Metrics and Monitoring

**File: `app/services/metrics.py`**

Add new metrics:

```python
from prometheus_client import Counter, Gauge, Histogram

# Existing metrics...
orch_provision_total = Counter(...)

# NEW: Communication metrics
orch_api_requests = Counter(
    "orch_api_requests_total",
    "Total API requests received",
    ["endpoint", "method", "status"]
)

orch_api_duration = Histogram(
    "orch_api_duration_seconds",
    "API request duration",
    ["endpoint", "method"]
)

orch_provision_duration = Histogram(
    "orch_provision_duration_seconds",
    "Engine provisioning duration",
    ["success"]
)

orch_communication_errors = Counter(
    "orch_communication_errors_total",
    "Communication errors with external services",
    ["source", "error_type"]
)
```

**File: `app/main.py`**

Add middleware to track metrics:

```python
from fastapi import Request
import time

@app.middleware("http")
async def track_metrics(request: Request, call_next):
    start_time = time.time()
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    
    orch_api_requests.labels(
        endpoint=request.url.path,
        method=request.method,
        status=response.status_code
    ).inc()
    
    orch_api_duration.labels(
        endpoint=request.url.path,
        method=request.method
    ).observe(duration)
    
    return response
```

**Benefits:**
- Visibility into API performance
- Identify slow endpoints
- Track error patterns
- Correlate with Acexy metrics

### 4. Rate Limiting Headers

Help Acexy understand Orchestrator load and back off when needed.

**File: `app/main.py`**

```python
from .services.circuit_breaker import circuit_breaker

@app.middleware("http")
async def add_rate_limit_headers(request: Request, call_next):
    response = await call_next(request)
    
    # Add headers about current system state
    response.headers["X-RateLimit-Provisioning-Limit"] = str(cfg.MAX_CONCURRENT_PROVISIONS)
    response.headers["X-RateLimit-Provisioning-Remaining"] = str(
        cfg.MAX_CONCURRENT_PROVISIONS - len(state.provisioning_queue)
    )
    
    if circuit_breaker.state != CircuitState.CLOSED:
        response.headers["X-Circuit-State"] = circuit_breaker.state.value
        response.headers["Retry-After"] = str(circuit_breaker.recovery_timeout)
    
    return response
```

**Benefits:**
- Acexy can adapt to Orchestrator load
- Graceful backoff when system is stressed
- Standard HTTP headers for rate limiting

## Medium-Term Improvements

### 5. Event Batching (Orchestrator Side)

Reduce overhead by accepting batched events.

**File: `app/models/schemas.py`**

```python
class EventBatch(BaseModel):
    started: List[StreamStartedEvent] = []
    ended: List[StreamEndedEvent] = []
```

**File: `app/main.py`**

```python
@app.post("/events/batch", dependencies=[Depends(require_api_key)])
def ev_batch(batch: EventBatch, bg: BackgroundTasks):
    """
    Accept batched stream events for better performance.
    """
    results = {
        "started": [],
        "ended": []
    }
    
    # Process started events
    for evt in batch.started:
        try:
            stream = state.on_stream_started(evt)
            results["started"].append({"success": True, "stream_id": stream.id})
            orch_events_started.inc()
            orch_streams_active.inc()
        except Exception as e:
            logger.error(f"Failed to process stream_started event: {e}")
            results["started"].append({"success": False, "error": str(e)})
    
    # Process ended events
    for evt in batch.ended:
        try:
            stream = state.on_stream_ended(evt)
            if stream:
                results["ended"].append({"success": True, "stream_id": stream.id})
                orch_events_ended.inc()
                orch_streams_active.dec()
            else:
                results["ended"].append({"success": False, "error": "stream not found"})
        except Exception as e:
            logger.error(f"Failed to process stream_ended event: {e}")
            results["ended"].append({"success": False, "error": str(e)})
    
    return results
```

**Benefits:**
- Reduce HTTP request overhead for high-frequency events
- Better performance under high load
- Backward compatible (single events still work)

### 6. Engine State Caching

Add caching headers to reduce unnecessary queries.

**File: `app/main.py`**

```python
from fastapi.responses import JSONResponse
import hashlib
import json

_engine_cache = {
    "etag": None,
    "data": None,
    "timestamp": None
}

@app.get("/engines")
def list_engines(request: Request):
    engines = state.list_engines()
    
    # Calculate ETag based on engine state
    engine_data = json.dumps(engines, default=str, sort_keys=True)
    etag = hashlib.md5(engine_data.encode()).hexdigest()
    
    # Check If-None-Match header
    if request.headers.get("If-None-Match") == etag:
        return JSONResponse(status_code=304, content=None)
    
    response = JSONResponse(content=engines)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "max-age=5"  # Cache for 5 seconds
    
    return response
```

**Benefits:**
- Reduce bandwidth for unchanged engine state
- Standard HTTP caching semantics
- Acexy can cache engine list efficiently

### 7. Async Event Processing

Make event endpoints fully async to prevent blocking.

**File: `app/main.py`**

```python
@app.post("/events/stream_started", response_model=StreamState, dependencies=[Depends(require_api_key)])
async def ev_stream_started(evt: StreamStartedEvent, bg: BackgroundTasks):
    """
    Async stream started event handler.
    """
    # Quick validation
    if not evt.engine or not evt.stream:
        raise HTTPException(status_code=400, detail="Invalid event data")
    
    # Process in background to avoid blocking
    def process_event():
        try:
            stream = state.on_stream_started(evt)
            orch_events_started.inc()
            orch_streams_active.inc()
            return stream
        except Exception as e:
            logger.error(f"Failed to process stream_started event: {e}")
            raise
    
    bg.add_task(process_event)
    
    # Return immediately with accepted status
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "stream_id": f"{evt.stream.key}|{evt.session.playback_session_id}"}
    )
```

**Benefits:**
- Non-blocking event processing
- Faster response to Acexy
- Better throughput under high load

## Long-Term Enhancements

### 8. Connection Pooling

**File: Add new file `app/services/http_client.py`**

```python
import httpx
from typing import Optional

class HTTPClientPool:
    """
    Shared HTTP client pool for efficient connections.
    """
    _client: Optional[httpx.AsyncClient] = None
    
    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None:
            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=100
                )
            )
        return cls._client
    
    @classmethod
    async def close(cls):
        if cls._client:
            await cls._client.aclose()
            cls._client = None
```

**Benefits:**
- Reuse HTTP connections
- Reduce connection overhead
- Better performance for high-frequency API calls

### 9. Structured Logging

**File: `app/utils/logging.py`**

Add structured logging for better analysis:

```python
import structlog

def setup_structured_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

**Benefits:**
- Machine-readable logs
- Better integration with log aggregation tools
- Easier debugging of distributed issues

### 10. Health-Based Load Balancing

Expose more granular health information for smarter load balancing.

**File: `app/main.py`**

```python
@app.get("/engines/health-score")
def engine_health_scores():
    """
    Return health scores for each engine to help with load balancing.
    """
    scores = []
    
    for engine_id, engine in state.engines.items():
        # Calculate health score based on multiple factors
        score = 100  # Perfect score
        
        # Reduce score based on active streams
        stream_count = len(engine.streams)
        score -= min(stream_count * 20, 60)  # -20 per stream, max -60
        
        # Reduce score if unhealthy
        if engine.health_status == "unhealthy":
            score -= 50
        elif engine.health_status == "unknown":
            score -= 25
        
        # Bonus for recently used (warm engine)
        if engine.last_stream_usage:
            idle_seconds = (datetime.now(timezone.utc) - engine.last_stream_usage).total_seconds()
            if idle_seconds < 300:  # Recently used (< 5 min)
                score += 10
        
        scores.append({
            "container_id": engine_id,
            "host": engine.host,
            "port": engine.port,
            "health_score": max(0, score),
            "stream_count": stream_count,
            "health_status": engine.health_status
        })
    
    # Sort by score descending
    scores.sort(key=lambda x: x["health_score"], reverse=True)
    
    return scores
```

**Benefits:**
- More intelligent load balancing in Acexy
- Considers multiple factors (health, load, warmth)
- Sorted by preference for easy selection

## Configuration Tuning Guide

### For High Load (Many Streams)

```bash
# Increase provisioning capacity
MAX_CONCURRENT_PROVISIONS=10
MIN_PROVISION_INTERVAL_S=0.2

# More generous timeouts
API_REQUEST_TIMEOUT_S=60
PROVISION_TIMEOUT_S=180

# Faster health checks
HEALTH_CHECK_INTERVAL_S=15

# More retries
MAX_RETRIES=5
RETRY_BACKOFF_S=3
```

### For Resource-Constrained Environments

```bash
# Conservative provisioning
MAX_CONCURRENT_PROVISIONS=3
MIN_PROVISION_INTERVAL_S=1.0

# Longer timeouts to avoid false failures
API_REQUEST_TIMEOUT_S=90
PROVISION_TIMEOUT_S=240

# Less frequent health checks
HEALTH_CHECK_INTERVAL_S=60

# Circuit breaker protection
CIRCUIT_BREAKER_THRESHOLD=3
CIRCUIT_BREAKER_TIMEOUT_S=600
```

### For High Availability

```bash
# Quick detection of issues
HEALTH_CHECK_INTERVAL_S=10
MONITOR_INTERVAL_S=5

# Aggressive provisioning
MIN_REPLICAS=5  # Always keep 5 free engines
MAX_CONCURRENT_PROVISIONS=8

# Short grace periods
ENGINE_GRACE_PERIOD_S=15

# Many retries
MAX_RETRIES=10
RETRY_BACKOFF_S=2
```

## Monitoring Setup

### Grafana Dashboard Queries

**Engine Health Over Time:**
```promql
orch_engines_total{health_status="healthy"}
```

**API Request Latency:**
```promql
histogram_quantile(0.95, rate(orch_api_duration_seconds_bucket[5m]))
```

**Provisioning Success Rate:**
```promql
rate(orch_provision_total{kind="acestream"}[5m])
```

**Communication Error Rate:**
```promql
rate(orch_communication_errors_total[5m])
```

### Alerting Rules

**High API Latency:**
```yaml
- alert: HighAPILatency
  expr: histogram_quantile(0.95, rate(orch_api_duration_seconds_bucket[5m])) > 1.0
  for: 5m
  annotations:
    summary: "High API latency detected"
```

**Circuit Breaker Open:**
```yaml
- alert: CircuitBreakerOpen
  expr: orch_circuit_state{state="open"} == 1
  for: 1m
  annotations:
    summary: "Circuit breaker is open, provisioning disabled"
```

**No Healthy Engines:**
```yaml
- alert: NoHealthyEngines
  expr: orch_engines_total{health_status="healthy"} == 0
  for: 2m
  annotations:
    summary: "No healthy engines available"
```

## Implementation Priority

**Week 1 (Critical):**
1. ✅ Health/readiness endpoint
2. ✅ Enhanced timeout configuration
3. ✅ Request metrics and monitoring
4. ✅ Rate limiting headers

**Week 2 (Important):**
5. ✅ Event batching endpoint
6. ✅ Engine state caching with ETags
7. ✅ Async event processing
8. ✅ Structured logging

**Week 3 (Enhancement):**
9. ✅ Connection pooling
10. ✅ Health-based load balancing
11. ✅ Comprehensive monitoring dashboard
12. ✅ Alert rules and runbooks

## Testing Strategy

### Load Testing

```bash
# Test Acexy-Orchestrator integration under load
# Requires: Apache Bench (ab), jq

# 1. Provision multiple engines
for i in {1..10}; do
  curl -X POST -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    http://localhost:8000/provision/acestream
done

# 2. Simulate stream requests
ab -n 1000 -c 50 -H "Authorization: Bearer $API_KEY" \
  http://localhost:8000/engines

# 3. Monitor metrics
curl http://localhost:8000/metrics | grep orch_api_duration
```

### Integration Testing

```python
# tests/test_acexy_integration.py
import pytest
import httpx

class TestAcexyIntegration:
    
    @pytest.mark.asyncio
    async def test_health_ready_endpoint(self):
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/health/ready")
            assert response.status_code == 200
            assert "ready" in response.json()
    
    @pytest.mark.asyncio
    async def test_event_batching(self):
        batch = {
            "started": [
                {
                    "engine": {"host": "localhost", "port": 19001},
                    "stream": {"key_type": "infohash", "key": "abc123"},
                    "session": {
                        "playback_session_id": "sess1",
                        "stat_url": "http://localhost:19001/stat",
                        "command_url": "http://localhost:19001/cmd",
                        "is_live": 1
                    }
                }
            ],
            "ended": []
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8000/events/batch",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json=batch
            )
            assert response.status_code == 200
            results = response.json()
            assert len(results["started"]) == 1
            assert results["started"][0]["success"] is True
```

## Summary

These improvements enhance the Acexy-Orchestrator integration **without merging** the services, addressing:

✅ **Reliability**: Health checks, timeouts, retries, circuit breaking
✅ **Performance**: Caching, batching, async processing, connection pooling
✅ **Observability**: Metrics, structured logging, monitoring
✅ **Scalability**: Rate limiting, load balancing, efficient resource usage

The architecture remains **decoupled** and **flexible** while gaining significant reliability and performance benefits.
