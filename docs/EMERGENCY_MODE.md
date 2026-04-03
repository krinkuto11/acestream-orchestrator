# Emergency Mode (Dynamic VPN)

Emergency mode is the degraded-operation state used when at least one dynamic VPN node is unhealthy while other nodes are still serving traffic.

## Goals

- Keep service available on healthy VPN nodes.
- Prevent churn by avoiding immediate re-use of unhealthy nodes.
- Recover automatically once replacement/recovered nodes are Ready.

## Behavior

1. A dynamic VPN node transitions to NotReady/Down.
2. Engines assigned to that node are evicted via scaling intents.
3. Engine scheduling continues on remaining Ready nodes.
4. VPN controller heals/replaces unhealthy nodes.
5. Capacity is restored after nodes recover and reconciliations complete.

## Signals

Observe via:
- `/orchestrator/status`
- `/engines`
- VPN/node lifecycle logs

Typical indicators:
- Reduced active engine capacity while degraded.
- `vpn_not_ready` eviction intents.
- Recovery logs when new/recovered nodes become healthy.

## Recovery Stabilization

Recovered nodes are not immediately trusted for destructive actions. A grace period avoids destroy/recreate loops during startup and reconnect turbulence.

## Example Timeline

- T0: `gluetun-dyn-a` healthy, `gluetun-dyn-b` healthy.
- T1: `gluetun-dyn-b` fails health checks.
- T2: Engines on `gluetun-dyn-b` are evicted; traffic remains on `gluetun-dyn-a`.
- T3: Controller recreates or recovers `gluetun-dyn-b`.
- T4: `gluetun-dyn-b` reaches Ready; capacity and balancing normalize.

## Testing

Run:

```bash
python -m pytest tests/test_emergency_mode.py -v
```
