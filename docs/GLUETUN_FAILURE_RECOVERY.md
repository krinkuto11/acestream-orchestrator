# Gluetun Failure Recovery (Dynamic VPN)

This document describes failure and recovery behavior for orchestrator-managed dynamic VPN nodes.

## Failure Detection

A node is considered unavailable when:
- Docker status/health indicates unhealthy/down, or
- control API reachability fails during readiness checks.

## Immediate Response

1. Node status transitions to NotReady/Down in state.
2. Engines bound to that node receive eviction intents.
3. New engine placements avoid the unhealthy node.

## Healing

The VPN controller applies a grace period for newly unstable nodes, then:
- drains node workloads,
- destroys/reprovisions unhealthy nodes,
- restores readiness through normal reconciliation.

## Engine Continuity

- Existing traffic on healthy nodes continues.
- Capacity may temporarily decrease while a node is replaced.
- Capacity returns as soon as replacement nodes become Ready and engines are rebalanced.

## Recommended Configuration

```bash
DYNAMIC_VPN_MANAGEMENT=true
PREFERRED_ENGINES_PER_VPN=10
VPN_NOTREADY_HEAL_GRACE_S=45
VPN_UNHEALTHY_RESTART_TIMEOUT_S=60
GLUETUN_PORT_RANGE_1=19000-19499
GLUETUN_PORT_RANGE_2=19500-19999
PORT_RANGE_HOST=19000-19999
```

## Operational Checks

- `GET /orchestrator/status`
- `GET /engines`
- controller/event logs for `vpn_not_ready` and recovery transitions

## Troubleshooting

- Repeated node churn: increase grace period and verify provider stability.
- No replacement nodes: validate credential availability and provider/protocol settings.
- Placement blocked: confirm at least one node is Ready and control API reachable.
