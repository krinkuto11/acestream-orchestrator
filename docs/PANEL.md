# Panel User Guide

Route:

```text
/panel
```

## Open The Panel

1. Start the orchestrator.
2. Open `http://<host>:8000/panel`.
3. If protected endpoints are enabled, open Settings -> General and set API key.

## Navigation

- Overview: global status summary
- Engines: engine state, lifecycle, and provisioning controls
- Streams: active and ended streams, diagnostics, actions
- Events: event log and filters
- Health: health checks and circuit-breaker status
- VPN: dynamic node health, leases, forwarding capability
- Dashboard: metrics and historical trends
- Settings: runtime configuration

## Engines View Updates

The Engines table reflects dynamic orchestration state.

### Lifecycle visibility

- `Active`: engine can accept new assignments.
- `Draining`: engine is still serving existing sessions but excluded from new placement.

### Forwarding leadership indicators

- Engines on forwarding-capable VPN nodes can be elected as forwarding leaders.
- The UI highlights forwarding leadership with badge-level visibility to simplify troubleshooting and placement validation.

## VPN Settings: Credential Management

Settings -> VPN now includes credential-pool lifecycle controls.

### What you can manage

- Add WireGuard credentials to the lease pool.
- Enable or disable credentials without deleting history.
- Set per-credential `port_forwarding` capability.

### Why per-credential forwarding matters

Scheduler placement now respects credential capabilities:

- Only credentials with `port_forwarding=true` are eligible for forwarding-leader workloads.
- Non-forwarding credentials remain valid for standard non-leader placement.

> [!IMPORTANT]
> If no valid credential lease is available, VPN-protected provisioning is intentionally blocked.

## Proxy Settings Workflows

### Select stream/control behavior

1. Open Settings -> Proxy.
2. Choose Stream Mode:
   - `TS`: MPEG-TS output
   - `HLS`: HLS manifest + segments
3. Choose Engine Control Mode:
   - `http`: default `/ace/getstream` flow
   - `api`: socket control flow (`HELLOBG/READY/LOADASYNC/START`)

Notes:

- `HLS` is supported in both control modes.
- Legacy values (`LEGACY_HTTP`, `LEGACY_API`) are accepted and normalized.

### Run preflight diagnostics from GUI

1. Open Settings -> Proxy -> Preflight Diagnostics.
2. Enter content identifier (infohash, PID, or magnet URI).
3. Select tier:
   - `light`: resolve/canonicalize only
   - `deep`: resolve + START + STATUS/livepos sample + STOP
4. Run preflight and inspect:
   - Availability
   - Resolved infohash
   - Control mode used
   - Raw JSON payload

Use `light` for quick checks and `deep` for startup/buffering investigation.

## Streams Page Notes

Expanded stream details include:

- Control mode labels (`proxy.control_mode`)
- Resolved canonical infohash (`stream.resolved_infohash`)
- Conditional action links for `stat_url` and `command_url`

In `api` mode, direct `stat_url` or `command_url` can be absent for some sessions. The panel renders this as informational text.

## Common Tasks

### Configure API key

1. Open Settings -> General.
2. Set API key.
3. Save.

### Configure engine policy

1. Open Engines -> Engine Configuration.
2. Set replica and provisioning options.
3. Save or Save and Reprovision.

### Stop stream(s)

1. Open Streams.
2. Select one stream and stop it, or multi-select and use batch stop.

### Configure dashboard metrics window

1. Open Dashboard.
2. Select a time window (for example 5m, 15m, 1h, 24h).
3. Charts and totals update to the selected range.

## Build The Panel Locally

```bash
cd app/static/panel-react
npm install
npm run build
```

Built files are served from `app/static/panel/` by FastAPI.
