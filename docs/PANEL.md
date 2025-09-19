# Panel

Route: `/panel`.

Functions:
- KPI of engines and streams.
- List of active engines and streams.
- Detail of a stream with `down/up/peers` chart in the last hour.
- Buttons: **Stop stream** (calls `command_url?method=stop` directly to the engine) and **Delete engine** (DELETE to the orchestrator).

Parameters:
- `orch` box: orchestrator base URL.
- `API key` box: Bearer for protected endpoints.
- Refresh interval: 2–30 s.

CORS:
- The panel is served from the same host. If you separate it, enable CORS in `main.py`.
