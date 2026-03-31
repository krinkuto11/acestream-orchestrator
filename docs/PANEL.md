# Panel User Guide

Route:

```text
/panel
```

## Open The Panel

1. Start the orchestrator.
2. Open `http://<host>:8000/panel`.
3. If protected endpoints are enabled, open Settings > General and set API key.

## Navigation

- Overview: global status summary
- Engines: engine state and provisioning controls
- Streams: active and ended streams, diagnostics, actions
- Events: event log and filters
- Health: health checks and circuit breaker state
- VPN: VPN health and forwarding state (single or redundant)
- Dashboard: metrics and historical trends
- Settings: runtime configuration

## Proxy Settings Workflows

### Select stream/control behavior

1. Open Settings > Proxy.
2. Choose Stream Mode:
	- `TS`: MPEG-TS output
	- `HLS`: HLS manifest + segments
3. Choose Engine Control Mode:
	- `http`: default `/ace/getstream` control flow
	- `api`: socket control flow (`HELLOBG/READY/LOADASYNC/START`)

Notes:
- `HLS` is supported in both control modes.
- Legacy values (`LEGACY_HTTP`, `LEGACY_API`) are still accepted and normalized.

### Run preflight diagnostics from GUI

1. Open Settings > Proxy > Preflight Diagnostics.
2. Enter content identifier (infohash, PID, or magnet URI).
3. Select tier:
	- `light`: resolve/canonicalize only
	- `deep`: resolve + START + STATUS/livepos sample + STOP
4. Run preflight and inspect:
	- Availability
	- Resolved infohash
	- Control mode used
	- Raw JSON payload

Use `light` for quick checks and `deep` for startup or buffering investigation.

## Streams Page Updates

Expanded stream details now include:
- Control mode labels (`proxy.control_mode`) when present
- Resolved canonical infohash (`stream.resolved_infohash`) when available
- Conditional action links for `stat_url` and `command_url`

In `api` mode, direct `stat_url` or `command_url` can be unavailable for some sessions. The panel now renders this as informational text instead of broken links.

## Common Tasks

### Configure API key

1. Open Settings > General.
2. Set API key.
3. Save.

### Configure engines

1. Open Engines > Engine Configuration.
2. Set replica and provisioning options.
3. Save or Save and Reprovision.

### Stop stream(s)

1. Open Streams.
2. Select one stream and stop it, or multi-select and use batch stop.

### Set dashboard metrics window

1. Open Dashboard.
2. Select a window (for example 5m, 15m, 1h, 24h).
3. Charts and window totals update to the selected range.

## Troubleshooting

### No data in panel

- Confirm orchestrator is running.
- Confirm browser reachability to `http://<host>:8000`.
- Confirm API key is set in Settings when protected endpoints are enabled.

### Preflight fails in deep tier

- Check selected control mode in Settings > Proxy.
- Confirm engine availability in Engines page.
- Retry in `light` tier to separate resolve issues from startup/status issues.

### Streams show unavailable command/stat URLs

- This is expected in some `api` flows.
- Use labels and diagnostics fields in expanded stream view.

## Build The Panel Locally

```bash
cd app/static/panel-react
npm install
npm run build
```

Built files are served from `app/static/panel/` by FastAPI.
