<!-- Copilot / AI agent instructions for the AceStream Orchestrator repo -->
# AceStream Orchestrator — AI Coding Instructions

Brief, actionable guidance to help an AI agent become productive in this repository.

## Big picture
- **Purpose**: Orchestrates AceStream engine containers on-demand with optional VPN (gluetun) integration, health monitoring, and a React dashboard.
- **Major components**:
  - `app/main.py`: FastAPI entrypoint and lifecycle orchestration (startup ordering, metrics, API surface).
  - `app/core/config.py`: Central config (`cfg`) built from environment variables; validates critical invariants (port ranges, VPN mode, replica counts).
  - `app/services/`: Business logic (provisioner, autoscaler, health, gluetun integration, metrics, monitors).
  - `app/models/` and `app/services/db.py`: state persistence (default SQLite `orchestrator.db`).
  - `app/static/panel`: React dashboard build (served via FastAPI when present).
  - `docs/`: authoritative docs (deploy, testing, VPN integration, architecture, and troubleshooting).

## Key workflows & commands
- Start standalone (no VPN): `cp .env.example .env` -> edit -> `docker-compose up -d`
- Start with single VPN: `docker-compose -f docker-compose.gluetun.yml up -d`
- Start with redundant VPN: `docker-compose -f docker-compose.gluetun-redundant.yml up -d`
- Run tests: `python -m pytest tests/`
- Useful endpoints during development:
  - `GET /metrics` — Prometheus metrics
  - `GET /orchestrator/status` — overall status used by proxies
  - `POST /provision/acestream` — provision an engine (requires `API_KEY`)

Example: call protected endpoint with API key
```
curl -H "X-API-KEY: $API_KEY" -X POST http://localhost:8000/provision/acestream -d '{...}'
```

## Project-specific conventions & patterns
- **Config-first invariants**: Many runtime validations live in `app/core/config.py` and raise on invalid env values — change behavior by editing env vars, not code.
- **Startup ordering matters**: `app/main.py` intentionally starts the `gluetun_monitor` and waits for Gluetun health before provisioning to avoid slow per-engine timeouts. Do not reorder startup steps lightly.
- **State vs. Docker reality**: `/engines` response is built from in-memory `state` but cross-checked with Docker-managed containers and gluetun health. Some endpoints prefer returning best-effort state (see `get_engines`).
- **Provisioning guards**: Provisioning can be blocked by VPN connectivity or circuit-breaker state; code returns structured `detail` objects indicating `can_retry`, `should_wait`, and `recovery_eta_seconds`.
- **Port allocation when using Gluetun**: Redundant VPN mode uses `GLUETUN_PORT_RANGE_1/2` and `PORT_RANGE_HOST`. Tests and docs assume these env vars map to compose port mappings exactly.

## Important files to inspect for changes
- `app/main.py` — API surface and lifecycle
- `app/core/config.py` — env-driven config and validation
- `app/services/provisioner.py` and `app/services/gluetun.py` — provisioning and VPN integration logic
- `docs/GLUETUN_INTEGRATION.md`, `docs/TESTING_GUIDE.md`, `docs/DEPLOY.md` — workflow expectations and manual test commands
- `docker-compose*.yml` — runtime modes (standalone, single VPN, redundant VPN)
- `tests/` — many integration and unit tests; use to infer intended behavior and edge cases

## Common pitfalls and how to handle them
- If a test or local run fails due to port allocation: verify `.env` and `GLUETUN_PORT_RANGE_*` match `docker-compose` port mappings (see `docs/TESTING_GUIDE.md`).
- If provisioning is failing: check `/orchestrator/status` and logs for circuit-breaker state or VPN connectivity issues. The code adds structured reasons for blocked provisioning.
- Avoid changing replica startup ordering — health monitoring, gluetun_monitor, and `ensure_minimum()` sequencing prevents race conditions.

## Editing & testing guidance for AI agents
- Prefer environment-driven changes (add env vars to `.env` or docker-compose files) for behavior changes.
- When adding or modifying endpoints, update `docs/API.md` and `docs/ARCHITECTURE.md` if behavior affects external integrations.
- Run `python -m pytest tests/` after changes; for VPN-related tests follow `docs/TESTING_GUIDE.md` steps to set `GLUETUN_PORT_RANGE_*` and run `docker-compose` variants.

## Short examples to reference
- Validate VPN health before provisioning (pattern): see `app/main.py` lines that call `gluetun_monitor.is_healthy()` and wait with timeout.
- Defensive reindexing after provisioning: `reindex_existing()` is called after starting engines so they appear immediately in `/engines`.

If anything here is unclear or you want me to expand a specific section (API examples, service boundaries, or test-run templates), tell me which part and I'll refine it.
