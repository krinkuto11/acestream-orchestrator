# Architecture

This document describes the current declarative architecture of AceStream Orchestrator.

## System Model

AceStream Orchestrator is a Kubernetes-lite control plane built on Docker primitives.

The runtime is driven by reconciliation loops instead of imperative bulk create/delete scripts:

1. Desired state is computed from policy and demand.
2. Actual state is observed from Docker events and health probes.
3. Controllers continuously reconcile desired vs actual.

## Control Plane

### Autoscaler

The Autoscaler computes desired engine capacity from load, policy, and safety guardrails.

- Inputs: stream demand, min/max replicas, configured limits.
- Output: desired replica target and scaling intents.
- Responsibility: decide "how many" engines should exist.

### Informer

The Informer subscribes to Docker lifecycle and health events.

- Watches: `start`, `die`, `destroy`, and health transitions.
- Updates: in-memory runtime state with minimal delay.
- Responsibility: provide authoritative "what currently exists" observations.

### Controller

The Engine Controller reconciles desired engine count against actual running containers.

- Applies create/terminate intents safely.
- Handles retries and blocked states.
- Responsibility: converge engine population to policy.

### Scheduler

The Scheduler performs placement and resource assignment before provisioning.

- Selects target VPN node and host networking resources.
- Allocates ports atomically.
- Applies placement constraints, including forwarding capability.
- Responsibility: decide "where and how" an engine should run.

### Provisioner

The Provisioner executes container creation/destruction from fully resolved specs.

- Applies container labels and runtime wiring.
- Rolls back reserved resources on failures.
- Defers running-state truth to Informer events.
- Responsibility: perform side effects against Docker.

## Dynamic VPN Management

Static fixed-topology VPN compose stacks are deprecated for primary operations.

The Dynamic VPN Controller manages a pool of orchestrator-created Gluetun nodes using **credential leases**.

### Credential Lease Model

- Credentials are stored in the panel and treated as finite reusable leases.
- Each active VPN node holds one lease.
- Desired VPN node count is bounded by both demand and available credentials.

### Blue/Green Predictive Pre-Warming

When a VPN node degrades or approaches replacement criteria:

1. Current node is marked `draining` (Blue).
2. Replacement node is provisioned immediately (Green) from spare lease capacity.
3. New placements target Green nodes only.
4. Blue node is retired once migration and reconciliation complete.

This minimizes hard-cutover risk and reduces failover latency.

For detailed lifecycle rules, see [DYNAMIC_VPN_MANAGEMENT.md](DYNAMIC_VPN_MANAGEMENT.md).

## Data Plane And High Availability

### Stateful Stream Migration

The proxy performs hot-swap migration for active streams when source engines become unhealthy or draining.

- HLS and TS sessions rebind to healthy engines.
- Session continuity is preserved through proxy-managed state, including Redis-backed buffering/indexing paths.
- Objective: keep client HTTP sockets alive while backend engine ownership changes.

### Legacy Monitor Persistent Relay

Legacy API monitored streams use a persistent micro-relay endpoint.

- Relay server binds once per monitor lifecycle (`127.0.0.2:0`, with loopback fallback when unavailable).
- Backend engine consumer tasks can restart independently.
- Proxy-side clients keep stable relay endpoints across engine failovers.

This removes relay-port churn and improves monitored stream HA behavior.

## State Management

Runtime state is centralized in `state.py` for fast reads and coordinated writes across controllers.

### Current Characteristics

- Uses an `RLock` to synchronize multi-threaded access paths.
- Maintains engine, stream, VPN-node, and intent state snapshots.
- Persists canonical records to SQLite for restart durability.

> [!WARNING]
> **Architectural debt note**
>
> `state.py` currently combines synchronization and persistence orchestration behind the same state lifecycle. The long-term design goal is to decouple SQLite persistence from the `RLock`-guarded critical path to reduce async event-loop blocking risk under high churn.

## Operational Flow

```text
Demand -> Autoscaler -> Desired replicas
               |
               v
        Engine Controller <-> Informer (Docker events)
               |
               v
           Scheduler -> Provisioner -> Docker
```

Dynamic VPN management follows the same loop with VPN-specific desired/actual reconciliation.

## Related Docs

- [API.md](API.md)
- [CONFIG.md](CONFIG.md)
- [DEPLOY.md](DEPLOY.md)
- [DYNAMIC_VPN_MANAGEMENT.md](DYNAMIC_VPN_MANAGEMENT.md)
- [HEALTH_MONITORING.md](HEALTH_MONITORING.md)
- [PANEL.md](PANEL.md)
