# Architecture Comparison: Separate vs Merged

This document provides visual representations of the current architecture vs. a merged architecture, highlighting the benefits of keeping services separate.

## Current Architecture (Recommended)

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │
│  │ VLC #1  │  │ VLC #2  │  │ VLC #3  │  │Browser│              │
│  └────┬────┘  └────┬────┘  └────┬────┘  └───┬────┘             │
└───────┼────────────┼────────────┼────────────┼──────────────────┘
        │            │            │            │
        │  HTTP GET /ace/getstream?id=...     │
        │            │            │            │
        ▼            ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ACEXY PROXY LAYER (Go)                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • Fast HTTP Proxy (Go goroutines)                       │   │
│  │  • Stream Multiplexing (same stream → many clients)      │   │
│  │  • Automatic PID assignment per client                   │   │
│  │  • Load Balancing Logic (health-aware)                   │   │
│  │  • Graceful Fallback (if orchestrator down)              │   │
│  │  • Circuit Breaking (prevent cascade failures)           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  Performance: ~1ms added latency per proxy hop                   │
│  Scalability: Horizontal (multiple Acexy instances)              │
│  Fault Isolation: Failures don't affect orchestrator             │
└─────────────────────────────────────────────────────────────────┘
        │
        │  1. GET /engines (list available engines)
        │  2. GET /streams?container_id=X (check load)
        │  3. POST /provision/acestream (if needed)
        │  4. POST /events/stream_started (report)
        │  5. POST /events/stream_ended (report)
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│              ORCHESTRATOR API LAYER (Python/FastAPI)             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • Container Lifecycle Management                        │   │
│  │  • Health Monitoring (every 30s)                         │   │
│  │  • Usage Tracking & Statistics                           │   │
│  │  • VPN Integration (Gluetun)                             │   │
│  │  • Circuit Breaker (protect Docker)                      │   │
│  │  • Rate Limiting (prevent overload)                      │   │
│  │  • Modern Dashboard UI                                   │   │
│  │  • Prometheus Metrics                                    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  Performance: <10ms API response time                            │
│  Scalability: Vertical (single orchestrator is sufficient)       │
│  Fault Isolation: Can restart without affecting active streams   │
└─────────────────────────────────────────────────────────────────┘
        │
        │  Docker API calls:
        │  • docker run (provision containers)
        │  • docker stop (cleanup)
        │  • docker inspect (health checks)
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DOCKER ENGINE LAYER                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Docker Daemon (manages containers)                      │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                  ACESTREAM ENGINE LAYER                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Engine 1 │  │ Engine 2 │  │ Engine 3 │  │ Engine N │        │
│  │ Port:    │  │ Port:    │  │ Port:    │  │ Port:    │        │
│  │ 19001    │  │ 19002    │  │ 19003    │  │ 19XXX    │        │
│  │          │  │          │  │          │  │          │        │
│  │ Stream:  │  │ Stream:  │  │ Stream:  │  │ Stream:  │        │
│  │ Sport1   │  │ Sport2   │  │ (idle)   │  │ News     │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
│                                                                   │
│  Health: Monitored every 30s by Orchestrator                     │
│  Load: Tracked by Orchestrator, queried by Acexy                 │
└─────────────────────────────────────────────────────────────────┘
        │
        │  (Optional) VPN Connection
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                      VPN LAYER (Gluetun)                         │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • Secure VPN tunnel                                     │   │
│  │  • Port forwarding                                       │   │
│  │  • Health monitoring                                     │   │
│  │  • Integrated with Orchestrator                          │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Characteristics

**Communication Flow:**
1. Client requests stream from Acexy
2. Acexy queries Orchestrator for best engine (HTTP, ~5ms)
3. Acexy serves stream from selected engine
4. Acexy reports events to Orchestrator (async, non-blocking)
5. Orchestrator manages engine health and lifecycle

**Failure Modes:**
- **Acexy fails**: Orchestrator keeps running, containers unaffected, restart Acexy
- **Orchestrator fails**: Acexy falls back to static engine config, streams continue
- **Engine fails**: Orchestrator detects (health check), Acexy selects different engine
- **Docker fails**: Circuit breaker stops provisioning, existing engines continue

**Scaling:**
- **Acexy**: Add more instances behind load balancer
- **Orchestrator**: Single instance sufficient, can add active-passive HA
- **Engines**: Auto-scaled by Orchestrator based on demand

## Merged Architecture (Not Recommended)

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐            │
│  │ VLC #1  │  │ VLC #2  │  │ VLC #3  │  │Browser│              │
│  └────┬────┘  └────┬────┘  └────┬────┘  └───┬────┘             │
└───────┼────────────┼────────────┼────────────┼──────────────────┘
        │            │            │            │
        │  HTTP GET /ace/getstream?id=...     │
        │            │            │            │
        ▼            ▼            ▼            ▼
┌─────────────────────────────────────────────────────────────────┐
│            MERGED PROXY + ORCHESTRATOR (???)                     │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Option A: Python-only (Slow Streaming)                  │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │ • Stream Proxy (Python - SLOWER than Go)           │  │   │
│  │  │ • Container Management (Python - same as before)   │  │   │
│  │  │ • Shared Process (all failures are catastrophic)   │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  │                                                            │   │
│  │  Option B: Go-only (Lose Python Ecosystem)               │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │ • Stream Proxy (Go - fast as before)               │  │   │
│  │  │ • Container Management (Go - harder than Python)   │  │   │
│  │  │ • Rewrite all Python code to Go (weeks of work)    │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  │                                                            │   │
│  │  Option C: Hybrid (Complex Codebase)                      │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │ • Go proxy embedded in Python (CGO)                │  │   │
│  │  │ • Complex build process                            │  │   │
│  │  │ • Harder debugging                                 │  │   │
│  │  │ • Deployment complexity                            │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  Problems:                                                        │
│  ❌ Any crash takes down both proxy AND orchestrator             │
│  ❌ Cannot scale proxy independently from orchestrator           │
│  ❌ Updates require downtime for entire system                   │
│  ❌ Proxy bugs can crash container management                    │
│  ❌ More complex debugging (mixed concerns)                      │
│  ❌ Either slow streaming (Python) or complex rewrite (Go)       │
└─────────────────────────────────────────────────────────────────┘
        │
        │  Docker API (same as before, no benefit from merging)
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DOCKER ENGINE LAYER                         │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                  ACESTREAM ENGINE LAYER                          │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### Problems with Merged Architecture

**Single Point of Failure:**
```
Current:
  Acexy crash → Streams stop, but containers keep running
              → Restart Acexy, reconnect to engines
              → Recovery time: ~5 seconds

Merged:
  Any crash → Both streaming AND management down
           → All containers orphaned, state lost
           → Recovery time: ~30+ seconds, need to rebuild state
```

**Scaling Issues:**
```
Current:
  High stream load → Add more Acexy instances
                   → Orchestrator unchanged
                   → Linear scaling

Merged:
  High stream load → Must scale entire merged service
                   → Waste resources on unused orchestration capacity
                   → Coupling makes scaling inefficient
```

**Technology Mismatch:**
```
Current:
  Proxy: Go (best for HTTP streaming, goroutines, low latency)
  Orchestrator: Python (best for Docker SDK, FastAPI, rapid dev)

Merged:
  Must choose ONE language:
    Python → Slow streaming (10-50x slower than Go for this workload)
    Go → Harder Docker management, lose FastAPI benefits
    Both → Complex hybrid, harder to maintain
```

**Deployment Complexity:**
```
Current:
  docker-compose.yml:
    acexy: simple Go binary
    orchestrator: simple Python service
  
  Update either independently
  Roll back either independently

Merged:
  Single complex service
  Any update affects everything
  Harder rollback strategy
  More complex CI/CD
```

## Performance Comparison

### Stream Handling (1000 concurrent clients)

**Current (Separate):**
```
Acexy Performance:
  - Go goroutines: ~1KB per client
  - Context switching: < 1μs
  - Memory usage: ~10MB + 1MB per stream
  - CPU: ~2% per stream
  - Latency added: ~1ms

Total for 1000 clients:
  - Memory: ~1GB
  - CPU: ~20 cores (distributed across goroutines)
  - Achievable: ✅ Yes, with proper hardware
```

**Merged (Python):**
```
Python Proxy Performance:
  - Async/await: ~10KB per client
  - Context switching: ~10-100μs
  - Memory usage: ~10MB + 5MB per stream
  - CPU: ~5% per stream (GIL contention)
  - Latency added: ~10-50ms

Total for 1000 clients:
  - Memory: ~5GB
  - CPU: ~50 cores (but GIL limits parallelism)
  - Achievable: ⚠️ Difficult, needs special tuning
```

### API Call Overhead

**Current HTTP Overhead:**
```
Per stream request:
  1. GET /engines: ~5ms (once per stream)
  2. GET /streams?container_id=X: ~3ms (once per stream)
  3. POST /events/stream_started: ~2ms (async, non-blocking)
  4. POST /events/stream_ended: ~2ms (async, non-blocking)

Total overhead: ~12ms one-time cost per stream
Ongoing overhead: ~0ms (events are async)

For 100 streams/minute:
  Total API time: ~1.2 seconds/minute
  Orchestrator CPU: < 1%
```

**Merged Internal Calls:**
```
Same operations, but internal function calls:
  1. engine_list(): ~0.1ms
  2. stream_count(id): ~0.1ms
  3. report_started(evt): ~0.1ms
  4. report_ended(evt): ~0.1ms

Savings: ~11.6ms per stream

BUT:
  - Lose fault isolation
  - Lose independent scaling
  - Lose deployment flexibility
  - Lose technology optimization

Is 11.6ms per stream worth it? NO.
```

## Communication Patterns Under Load

### Scenario: 100 streams started in 10 seconds

**Current Architecture:**
```
Timeline:
T=0s:    10 streams request → Acexy
         Acexy queries orchestrator (10 x 5ms = 50ms)
         Orchestrator responds with engines
         Acexy starts streaming (ongoing)
         Acexy reports events async (10 x 2ms = 20ms, non-blocking)

T=1s:    10 more streams... (repeat)

T=10s:   100 streams active
         Total HTTP overhead: 500ms for queries + 200ms for events
         Actual time: spread over 10 seconds → 70ms/second
         Orchestrator load: < 1% CPU

Result: ✅ Handles load easily with proper configuration
```

**Merged Architecture:**
```
Timeline:
T=0s:    10 streams request → Merged Service
         Internal function calls (fast, ~1ms total)
         Starts streaming (ongoing)
         Records events internally (~1ms total)

T=1s:    10 more streams...

T=10s:   100 streams active
         Saved: ~70ms/second of HTTP overhead

BUT:
  - All 100 streams share same process
  - Any bug in proxy affects orchestration
  - Any bug in orchestration affects streams
  - Cannot scale independently
  - More complex failure scenarios

Result: ⚠️ Saved 70ms/sec but lost flexibility, reliability, scalability
```

### Scenario: Orchestrator becomes unavailable

**Current (Fault Isolated):**
```
1. Acexy attempts to query orchestrator
2. Request times out (3s default)
3. Acexy falls back to static engine config
4. Streams continue with fallback engine
5. When orchestrator returns, Acexy resumes normal operation

Downtime: 0 seconds (graceful degradation)
Impact: Reduced flexibility (no load balancing) but streams work
Recovery: Automatic when orchestrator comes back
```

**Merged (Catastrophic):**
```
1. Process crashes (orchestrator bug, proxy bug, or memory issue)
2. All streams stop immediately
3. All container management stops
4. Must restart entire merged service
5. Must rebuild state from database
6. Must reconnect all clients

Downtime: 30+ seconds (full restart)
Impact: All streams down, all clients disconnected
Recovery: Manual intervention required
```

## High Availability Comparison

### Current Architecture (Separate)

**Multi-Instance Acexy:**
```yaml
services:
  acexy-1:
    image: acexy
    environment:
      ACEXY_ORCH_URL: http://orchestrator:8000
  
  acexy-2:
    image: acexy
    environment:
      ACEXY_ORCH_URL: http://orchestrator:8000
  
  nginx:
    image: nginx
    # Round-robin load balancing
    # If one Acexy fails, others handle traffic
```

**Single Orchestrator (Sufficient):**
```yaml
services:
  orchestrator:
    image: orchestrator
    restart: on-failure
    # One instance sufficient for most workloads
    # Can add active-passive HA if needed
```

**Benefits:**
- ✅ Scale Acexy horizontally for traffic
- ✅ Orchestrator restarts automatically
- ✅ Acexy can survive orchestrator downtime
- ✅ Easy to add more capacity

### Merged Architecture

**Would Still Need Multiple Instances:**
```yaml
services:
  merged-1:
    image: merged
    # Full proxy + orchestrator
  
  merged-2:
    image: merged
    # Another full proxy + orchestrator
  
  # Problem: Two orchestrators trying to manage same containers!
  # Need: Complex state synchronization, leader election, consensus
```

**Problems:**
- ❌ Multiple orchestrators need coordination (etcd/consul)
- ❌ Risk of split-brain scenarios
- ❌ Waste resources (duplicate orchestration capacity)
- ❌ More complex deployment and monitoring

## Cost Analysis

### Development Cost

**Keeping Separate:**
- Maintenance: Existing, stable code
- New features: Can optimize each service independently
- Debugging: Clear boundaries, easier to isolate issues
- Cost: Low (incremental improvements)

**Merging:**
- Option A (Python): Rewrite Acexy in Python
  - Time: 2-3 weeks for basic functionality
  - Performance: Significant degradation
  - Testing: Extensive load testing required
  - Risk: High

- Option B (Go): Rewrite Orchestrator in Go
  - Time: 4-6 weeks (Docker SDK, FastAPI, dashboard)
  - Benefits: Minimal (Docker SDK less mature in Go)
  - Testing: Extensive integration testing
  - Risk: High

- Option C (Hybrid): Mix Go and Python
  - Time: 3-4 weeks (CGO, build system)
  - Complexity: High (multiple languages, complex builds)
  - Maintenance: Harder (fewer people know both)
  - Risk: Very High

### Operational Cost

**Keeping Separate:**
- Monitoring: Two services, clear metrics
- Deployment: Simple docker-compose
- Scaling: Independent, cost-effective
- Debugging: Easier (separate logs)
- Cost: Low

**Merging:**
- Monitoring: Complex (mixed metrics)
- Deployment: More complex (hybrid or full rewrite)
- Scaling: Coupled, wasteful
- Debugging: Harder (mixed concerns)
- Cost: Higher

### Infrastructure Cost

**Keeping Separate:**
- Acexy: ~100MB RAM, 0.5 CPU per instance
- Orchestrator: ~200MB RAM, 1 CPU
- Total base: ~300MB RAM, 1.5 CPU
- Scaling: Add Acexy instances as needed (~100MB each)

**Merging:**
- Merged: ~500MB RAM, 2 CPU per instance
- Must run multiple for HA: 2x overhead
- Cannot scale proxy independently
- Total: Higher resource usage for same capacity

## Conclusion

The comparison clearly shows that **keeping Acexy and Orchestrator separate** is the superior architecture:

### Separate Services Win On:
1. ✅ **Performance**: Go proxy is faster than Python
2. ✅ **Scalability**: Independent scaling is more efficient
3. ✅ **Reliability**: Fault isolation prevents cascade failures
4. ✅ **Maintainability**: Clear boundaries, easier debugging
5. ✅ **Flexibility**: Can deploy and update independently
6. ✅ **Cost**: Lower development and operational costs
7. ✅ **Technology**: Each service uses optimal language/framework

### Merging Would Only Win On:
1. ~**HTTP Overhead**: Save ~10-50ms per stream (negligible)

### The Trade-Off is Clear:
- Save: ~10-50ms per stream (one-time cost)
- Lose: Performance, scalability, reliability, flexibility, maintainability

**Verdict: Keep services separate. The current architecture is optimal.**
