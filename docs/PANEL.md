# Panel User Guide

Route:

```text
/panel
```

## Open The Panel

1. Start the orchestrator.
2. Open `http://<host>:8000/panel`.
3. If protected endpoints are enabled, go to Settings and set the API key.

## Navigation

- Overview: global status summary
- Engines: engine status and engine configuration
- Streams: active streams and actions
- Events: event log and filters
- Health: health checks and circuit breaker state
- VPN: VPN status (single or redundant mode)
- Dashboard: metrics and historical trends
- Settings: runtime configuration

## Common Tasks

### Configure API key

1. Open Settings > General.
2. Set API key.
3. Save.

### Change refresh interval

1. Open Settings > General.
2. Select refresh interval.
3. Save.

### Configure engines

1. Open Engines > Engine Configuration.
2. Set replica and provisioning options.
3. Save or Save and Reprovision.

### Stop a stream

1. Open Streams.
2. Select stream.
3. Stop stream.

### Delete an engine

1. Open Engines.
2. Select engine.
3. Delete engine.

### Set dashboard metrics window

1. Open Dashboard.
2. Select a window (for example 5m, 15m, 1h, 24h).
3. Charts and window totals update to the selected range.

## Dashboard Metrics Notes

- Throughput charts are rates in Mbps.
- Global ingress and egress totals are window-scoped totals in bytes for the selected window.
- Error rates include upstream proxy failures.

## Troubleshooting

### No data in panel

- Check orchestrator is running.
- Check browser can reach `http://<host>:8000`.
- Check API key in Settings if endpoints are protected.

### Engines page actions fail

- Confirm API key is valid.
- Confirm Docker socket is mounted in the orchestrator container.

### VPN page empty or disconnected

- Confirm VPN compose profile is running.
- Check container names configured in Settings > VPN.

## Build The Panel Locally

```bash
cd app/static/panel-react
npm install
npm run build
```

Built files are served from `app/static/panel/` by FastAPI.
