# Engine Provisioning and Load Balancing Strategy

## Overview

The AceStream Orchestrator uses a **layer-based filling strategy with lookahead provisioning** to efficiently manage engine capacity and ensure high availability. This document explains how streams are assigned to engines and when new engines are provisioned.

## Provisioning Architecture (Current)

Provisioning is now split into declarative and execution phases:

1. **Desired state computation**: autoscaler computes desired replicas.
2. **Reconciliation**: engine controller creates or terminates engines to match desired state.
3. **Scheduling**: resource scheduler atomically selects VPN node, forwarded role, and host/container ports, producing an `EngineSpec`.
4. **Execution**: provisioner performs Docker create using `EngineSpec`.
5. **Lifecycle confirmation**: Docker informer updates engine state on lifecycle events (`start`, `die`, `destroy`, health changes).

Important behavior:
- Provisioner no longer blocks waiting for running status.
- Runtime truth comes from Docker events, not post-create polling loops.
- Port reservations and VPN pending counters are rolled back on create failure.

## Key Concepts

### Layer-Based Filling (Round-Robin)

Instead of filling one engine to capacity before using the next, the orchestrator fills all engines evenly in "layers":

- **Layer 1**: All engines get 1 stream before any engine gets a 2nd stream
- **Layer 2**: All engines get 2 streams before any engine gets a 3rd stream
- **Layer N**: All engines get N streams before any engine gets N+1 streams

This continues until layer `(MAX_STREAMS_PER_ENGINE - 1)` is reached.

### Lookahead Provisioning

To prevent capacity gaps, the orchestrator uses **lookahead provisioning**:

1. **Trigger Early**: When the **first** engine reaches layer `(MAX_STREAMS - 1)`, start provisioning a new engine
2. **Buffer Time**: This gives the system time to spin up the new engine while other engines continue filling
3. **Reserve Slot**: Layer `MAX_STREAMS` acts as an overflow buffer during provisioning
4. **Ready State**: Once the new engine is ready, new streams prefer it (back to layer 1)

## Stream Assignment Priority

When a new stream request arrives, the orchestrator selects an engine using this priority:

1. **Available Capacity**: Only consider engines not at `MAX_STREAMS_PER_ENGINE`
2. **Lowest Load First**: Select the engine with the **fewest** streams
3. **Forwarded Priority**: When multiple engines have equal load, prefer forwarded engines
4. **Round-Robin Result**: This naturally creates layer-based filling

## Configuration

The behavior is controlled by these environment variables:

```bash
# Maximum streams per engine (default: 3)
MAX_STREAMS_PER_ENGINE=5

# Minimum initial engines on startup (default: 2)
MIN_REPLICAS=2

# Minimum free engines for lookahead buffer check (default: 1)
# Note: This does NOT trigger provisioning, only prevents duplicate provisioning
# when lookahead is triggered but free engines already exist
MIN_FREE_REPLICAS=1

# Maximum total engines when using VPN (default: 6)
MAX_REPLICAS=6
```

## Example Scenario

With `MAX_STREAMS_PER_ENGINE=5` and 3 initial engines:

### Stream Assignment Sequence

```
Layer 1 (streams 1-3):
  Stream 1 → engine_0 (forwarded)  [1/5]
  Stream 2 → engine_1              [1/5]
  Stream 3 → engine_2              [1/5]
  
Layer 2 (streams 4-6):
  Stream 4 → engine_0 (forwarded)  [2/5]
  Stream 5 → engine_1              [2/5]
  Stream 6 → engine_2              [2/5]

Layer 3 (streams 7-9):
  Stream 7 → engine_0              [3/5]
  Stream 8 → engine_1              [3/5]
  Stream 9 → engine_2              [3/5]

Layer 4 (streams 10-12) - LOOKAHEAD TRIGGER:
  Stream 10 → engine_0             [4/5] ⚠️ PROVISIONING STARTED
  Stream 11 → engine_1             [4/5]
  Stream 12 → engine_2             [4/5]
  
Layer 5 (streams 13-15) - Overflow buffer:
  Stream 13 → engine_0             [5/5] (overflow while engine_3 provisions)
  Stream 14 → engine_1             [5/5]
  Stream 15 → engine_2             [5/5]
  
New engine ready (engine_3):
  Stream 16 → engine_3             [1/5] ✓ Prefers new engine
```

### Provisioning Timeline

```
Time   Event
────   ─────────────────────────────────────────────────────
T+0    First stream arrives
T+10   Layer 1 complete (all engines: 1/5)
T+20   Layer 2 complete (all engines: 2/5)
T+30   Layer 3 complete (all engines: 3/5)
T+40   Layer 4 starts
T+41   🔔 LOOKAHEAD TRIGGER: First engine reaches 4/5
       → Start provisioning engine_3
T+50   Layer 4 complete (all engines: 4/5)
       → engine_3 still provisioning
T+60   Layer 5 starts (overflow buffer)
T+70   Layer 5 complete (all engines: 5/5)
T+75   ✅ engine_3 ready and available
T+76   New stream → engine_3 (back to layer 1)
```

## Benefits

### 1. Balanced Load Distribution
- All engines share the load evenly
- No single engine becomes a bottleneck
- Better resource utilization

### 2. High Availability
- Lookahead provisioning prevents capacity gaps
- Reserve slot provides overflow buffer
- Handles predictable linear growth

### 3. Efficient Resource Usage
- Engines are only provisioned when needed
- Layer-based filling maximizes existing capacity
- Autoscaler removes idle engines after grace period

### 4. Forwarded Engine Priority
- P2P-enabled (forwarded) engines get priority at equal load
- Better performance for torrents that benefit from port forwarding
- Automatic in redundant VPN mode

## Trade-offs and Limitations

### ✓ Strengths
- **Excellent for predictable growth**: Layer-based filling handles steady stream increases well
- **Buffer time**: Lookahead gives time for provisioning before overflow
- **Balanced**: Even load distribution prevents hotspots

### ⚠️ Limitations
- **Flash crowds**: Sudden spikes may still overwhelm if growth exceeds provisioning speed
- **Linear assumption**: Assumes streams arrive at manageable rate
- **Provisioning time**: Buffer is limited by time to fill remaining layer slots

### Mitigation Strategies
1. **Lower `MAX_STREAMS_PER_ENGINE`**: Triggers lookahead provisioning earlier and more frequently
2. **Increase `MIN_REPLICAS`**: Start with more engines on initial startup
3. **Faster provisioning**: Optimize container startup time to reduce lag between trigger and availability
4. **Note on `MIN_FREE_REPLICAS`**: This setting only affects the lookahead buffer check and does NOT trigger provisioning on its own
4. **Health monitoring**: Quickly detect and replace unhealthy engines

## Implementation Details

### Engine Selection Logic

```python
# Filter out engines at max capacity
max_streams = cfg.MAX_STREAMS_PER_ENGINE
available_engines = [
    e for e in engines 
    if engine_loads.get(e.container_id, 0) < max_streams
]

# Sort: (load, not forwarded) - LEAST load first, then forwarded
engines_sorted = sorted(available_engines, key=lambda e: (
    engine_loads.get(e.container_id, 0),  # Ascending (least first)
    not e.forwarded  # Forwarded preferred at equal load
))
selected_engine = engines_sorted[0]
```

### Autoscaler Lookahead Logic

```python
# Check if ANY engine has reached threshold (lookahead trigger)
max_streams_threshold = cfg.MAX_STREAMS_PER_ENGINE - 1
any_engine_near_capacity = any(
    count >= max_streams_threshold 
    for _, count in engines_with_stream_counts
)

if any_engine_near_capacity:
    # Start provisioning new engine
    # Provides buffer time before overflow
    provision_new_engine()
```

### Scheduler and Provisioner Split

```python
# Scheduler resolves a complete spec before runtime execution
engine_spec = resource_scheduler.schedule(req, engine_variant_name)

# Provisioner executes container creation only
response = start_acestream(req, engine_spec=engine_spec)

# Running state is confirmed asynchronously by Docker events
```

### Atomic Port and VPN Decisions

Scheduling performs these decisions atomically under lock:
- VPN target selection (including redundant-mode balancing and health checks)
- forwarded-engine election per VPN
- HTTP/HTTPS/API container and host port reservation with rollback on failure

## Monitoring

### Key Metrics

Track these metrics to monitor the provisioning strategy:

- **Engine load distribution**: Verify all engines have similar stream counts
- **Provisioning lag**: Time between trigger and new engine ready
- **Overflow events**: Count of times layer MAX was used
- **Engine utilization**: Percentage of time at each layer
- **Desired vs actual replicas**: Controller convergence health
- **Scaling intent outcomes**: applied/failed/blocked counts
- **Event convergence delay**: time between create and start event

### API Endpoints

```bash
# Check engine status and stream distribution
GET /engines

# Monitor autoscaler decisions
GET /metrics

# View provisioning events
GET /events?type=system&category=scaling
```

## Best Practices

1. **Set appropriate `MAX_STREAMS_PER_ENGINE`**: Balance between granular control and provisioning overhead
2. **Monitor provisioning time**: Ensure new engines become ready before overflow
3. **Use MIN_FREE_REPLICAS**: Keep buffer engines for sudden spikes
4. **Test under load**: Validate behavior matches your traffic patterns
5. **Adjust for your use case**: Different content types may need different settings

## Troubleshooting

### Problem: Engines not filling evenly

**Check:**
- Are all engines healthy? (`GET /engines`)
- Is VPN healthy in redundant mode? (`GET /orchestrator/status`)
- Check logs for selection logic decisions

### Problem: New engines not provisioning in time

**Solutions:**
- Decrease `MAX_STREAMS_PER_ENGINE` for earlier triggers
- Increase `MIN_FREE_REPLICAS` to keep idle engines ready
- Optimize container startup time

### Problem: Provision API returns quickly but engine not immediately visible

This is expected in the event-driven model:
- Container create succeeds first
- Engine appears in runtime state after Docker `start` event is processed

If delay is prolonged:
- Check Docker event watcher logs
- Verify Docker socket availability
- Check for blocked/restarting Docker daemon

### Problem: Too many idle engines

**Solutions:**
- Increase `ENGINE_GRACE_PERIOD_S` to keep engines longer
- Adjust `MIN_FREE_REPLICAS` to match your traffic pattern
- Review `AUTO_DELETE` setting

## See Also

- [Architecture Documentation](ARCHITECTURE.md)
- [Testing Guide](TESTING_GUIDE.md)
- [Deployment Guide](DEPLOY.md)
