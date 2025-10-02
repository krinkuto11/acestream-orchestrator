

# Overview

Objective: launch AceStream containers on-demand to serve streams requested by a proxy. The orchestrator:
- Provisions containers with dynamic internal and external ports.
- Receives `stream_started` and `stream_ended` events.
- Collects periodic statistics from `stat_url`.
- Persists engines, streams and statistics in SQLite.
- Exposes a simple panel and Prometheus metrics.

Components:
- **Orchestrator API**: FastAPI over Uvicorn.
- **Docker host**: `docker:dind` in Compose or host Docker via `DOCKER_HOST`.
- **Panel**: Static HTML at `/panel`.
- **Proxy**: client that talks to the AceStream engine and the orchestrator.

Typical flow:
1. Proxy requests `POST /provision/acestream` if no engine is available.
2. Orchestrator starts container with `--http-port`, `--https-port` flags and host binding.
3. Proxy initiates playback against `http://<host>:<host_http_port>/ace/manifest.m3u8?...&format=json`.
4. Proxy obtains `stat_url` and `command_url` from engine and sends `POST /events/stream_started`.
5. Orchestrator collects stats periodically from `stat_url`.
6. When finished, proxy sends `POST /events/stream_ended`. If `AUTO_DELETE=true`, orchestrator deletes the container.
