# AceStream Orchestrator

<img src="app/icons/favicon-96x96-dark.png" alt="AceStream Orchestrator Logo" width="96" height="96" />

On-demand AceStream engine orchestration with a built-in proxy, health management, VPN support, metrics, and a web panel.

> [!WARNING]
> **LEGAL DISCLAIMER: EDUCATIONAL USE ONLY**
> 
> This software is a proof-of-concept designed strictly for **educational and research purposes**. The primary intent of this project is to demonstrate the feasibility of a high availability scenario for the AceStream protocol and should not be used for illegal acts.

## Quick Start

### Standalone

```bash
docker-compose up -d
```

### VPN (orchestrator-managed)

```bash
docker-compose up -d
```

Then open the panel and configure VPN under Settings -> VPN. The orchestrator will provision and manage Gluetun instances dynamically.

> [!IMPORTANT]
> Legacy `docker-compose.gluetun.yml` and `docker-compose.gluetun-redundant.yml` are deprecated.
> Use `docker-compose.yml` and orchestrator-managed VPN provisioning.

Panel URL:

```text
http://localhost:8000/panel
```

Stream URL format:

```text
http://<host>:8000/ace/getstream?id=<acestream_id>
```

## Minimal Usage

1. Start containers with `docker-compose.yml`.
2. Open `http://<host>:8000/panel`.
3. Set API key in Settings if protected endpoints are enabled.
4. If VPN is needed, configure provider/protocol/credentials in Settings -> VPN.
5. Use stream URL format in your player.

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
- VPN credentials if enabling orchestrator-managed VPN nodes

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

Protected endpoints require:

```
Authorization: Bearer <API_KEY>
```

Set `API_KEY` as an environment variable or configure it in **Settings → Orchestrator** after startup.

## Configuration

Most settings can be changed at runtime in the **Settings** panel and are persisted to `app/config/*.json`.
For the initial deploy (especially VPN mode) use environment variables — see [`.env.example`](.env.example) for a full reference, or [docs/CONFIG.md](docs/CONFIG.md) for detailed descriptions.

Key environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `API_KEY` | *(none)* | Bearer token for protected endpoints |
| `MIN_REPLICAS` | `2` | Minimum engine containers to keep running |
| `MAX_REPLICAS` | `6` | Maximum concurrent engines |
| `PORT_RANGE_HOST` | `19000-19999` | Host ports mapped to engine containers |
| `DYNAMIC_VPN_MANAGEMENT` | `true` | Compatibility key (runtime forces dynamic orchestration on) |
| `VPN_PROVIDER` | `protonvpn` | Default VPN provider for dynamic nodes |
| `VPN_PROTOCOL` | `wireguard` | Default VPN protocol for dynamic nodes |
| `PREFERRED_ENGINES_PER_VPN` | `10` | Target engines per dynamic VPN node |
| `DEBUG_MODE` | `false` | Verbose logging |

## Documentation

- [docs/DEPLOY.md](docs/DEPLOY.md) — Deployment guide (standalone + orchestrator-managed VPN)
- [docs/CONFIG.md](docs/CONFIG.md) — Complete environment variable reference
- [docs/API.md](docs/API.md) — Full API reference
- [docs/SECURITY.md](docs/SECURITY.md) — Authentication, network exposure, TLS
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Internal architecture and database schema
- [docs/PANEL.md](docs/PANEL.md) — Web dashboard user guide
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) — VPN port allocation testing
- [docs/GLUETUN_INTEGRATION.md](docs/GLUETUN_INTEGRATION.md) — Gluetun VPN integration details

## Development

Run tests:

```bash
python -m pytest tests/
```
