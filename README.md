# AceStream Orchestrator

<img src="app/icons/favicon-96x96-dark.png" alt="AceStream Orchestrator Logo" width="96" height="96" />

On-demand AceStream engine orchestration with a built-in proxy, health management, VPN support, metrics, and a web panel.

> [!WARNING]
> **LEGAL DISCLAIMER: EDUCATIONAL USE ONLY**
> 
> This software is a proof-of-concept designed strictly for **educational and research purposes**. The primary intent of this project is to demonstrate the feasability of a high availability scenario for the AceStream protocol and should not be used for illegal acts.

## Quick Start

### Standalone

```bash
docker-compose up -d
```

### VPN (single)

```bash
docker-compose -f docker-compose.gluetun.yml up -d
```

### VPN (redundant)

```bash
docker-compose -f docker-compose.gluetun-redundant.yml up -d
```

Panel URL:

```text
http://localhost:8000/panel
```

Stream URL format:

```text
http://<host>:8000/ace/getstream?id=<acestream_id>
```

## Minimal Usage

1. Start containers with one of the compose files.
2. Open `http://<host>:8000/panel`.
3. Set API key in Settings if protected endpoints are enabled.
4. Use stream URL format in your player.

## Modify M3U Playlist

If your playlist contains AceStream IDs or direct AceStream engine URLs, rewrite each entry to use the orchestrator endpoint.

Target format:

```text
http://<host>:8000/ace/getstream?id=<acestream_id>
```

Typical replacements:

- `acestream://<id>` -> `http://<host>:8000/ace/getstream?id=<id>`
- `http://127.0.0.1:6878/ace/getstream?id=<id>` -> `http://<host>:8000/ace/getstream?id=<id>`

## Requirements

- Docker and Docker Compose
- Docker socket access for the orchestrator container
- VPN credentials if using Gluetun compose files

## Main Endpoints

```text
GET  /panel
GET  /ace/getstream?id=<id>
GET  /engines
GET  /streams?status=started
GET  /orchestrator/status
GET  /metrics
GET  /metrics/dashboard
POST /provision/acestream
```

Protected endpoints require `X-API-KEY`.

## Configuration

Use the panel Settings sections for runtime configuration:

- General
- Orchestrator
- VPN
- Proxy
- Loop detection
- Backup

Environment variables are still supported through container configuration. See docs for complete options.

## Documentation

- [docs/DEPLOY.md](docs/DEPLOY.md)
- [docs/API.md](docs/API.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/PANEL.md](docs/PANEL.md)
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md)
- [docs/GLUETUN_INTEGRATION.md](docs/GLUETUN_INTEGRATION.md)

## Development

Run tests:

```bash
python -m pytest tests/
```
