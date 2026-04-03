# Gluetun VPN Integration

This project uses orchestrator-managed dynamic Gluetun nodes.

Static or externally managed Gluetun container binding is no longer supported in runtime behavior. VPN nodes are provisioned, monitored, healed, and recycled by the orchestrator controller.

## How Dynamic VPN Works

1. Enable VPN in the Settings panel.
2. Configure provider, protocol, and credentials.
3. The VPN controller provisions dynamic Gluetun nodes based on desired engine demand and `PREFERRED_ENGINES_PER_VPN`.
4. Engines are scheduled onto healthy dynamic VPN nodes only.
5. Docker events and controller reconciliation maintain node and engine health.

## Required Configuration

Use `docker-compose.yml` and configure VPN through Settings. Relevant env keys:

```bash
DYNAMIC_VPN_MANAGEMENT=true
VPN_PROVIDER=protonvpn
VPN_PROTOCOL=wireguard
PREFERRED_ENGINES_PER_VPN=10
GLUETUN_API_PORT=8001
GLUETUN_PORT_RANGE_1=19000-19499
GLUETUN_PORT_RANGE_2=19500-19999
PORT_RANGE_HOST=19000-19999
```

Notes:
- `DYNAMIC_VPN_MANAGEMENT` is treated as compatibility input and runtime enforces dynamic behavior.
- `GLUETUN_PORT_RANGE_1` and `GLUETUN_PORT_RANGE_2` are optional dynamic range slots used for host-side distribution.

## Operational Semantics

- VPN node readiness requires control API reachability and healthy/running status.
- Engine scheduling fails fast with structured blocked behavior if no ready dynamic node exists.
- Forwarded-engine election is per dynamic VPN node.
- Shutdown cleanup removes managed engines and managed dynamic VPN nodes.

## Troubleshooting

### No engines are scheduled with VPN enabled

- Check `/orchestrator/status` and `/engines`.
- Verify at least one dynamic node is Ready.
- Confirm VPN credentials are valid and leases are available.

### No forwarded port available

- Some VPN providers or credentials do not support forwarding.
- The orchestrator will continue with non-forwarded placement when forwarding is unavailable.

### Port allocation errors

- Ensure `PORT_RANGE_HOST` matches your compose-exposed host range.
- If using dynamic range slots, verify `GLUETUN_PORT_RANGE_1/2` are non-overlapping and within `PORT_RANGE_HOST`.

## Related Docs

- [docs/DEPLOY.md](DEPLOY.md)
- [docs/CONFIG.md](CONFIG.md)
- [docs/GLUETUN_FAILURE_RECOVERY.md](GLUETUN_FAILURE_RECOVERY.md)
- [docs/TESTING_GUIDE.md](TESTING_GUIDE.md)
