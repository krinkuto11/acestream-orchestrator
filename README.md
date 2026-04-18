# AceStream Orchestrator

<img src="app/icons/favicon-96x96-dark.png" alt="AceStream Orchestrator Logo" width="96" height="96" />

On-demand AceStream engine orchestration with a declarative control plane, built-in proxy, dynamic VPN lifecycle management, metrics, and a web panel. 

> [!WARNING]
> **LEGAL DISCLAIMER: EDUCATIONAL USE ONLY**
>
> This software is a proof-of-concept designed strictly for educational and research purposes. The project demonstrates high availability patterns for AceStream workflows and must not be used for unlawful activity.

## Quick Start

### Standalone

```bash
docker-compose up -d
```

### VPN (Orchestrator-Managed, Dynamic)

```bash
docker-compose up -d
```

Then open the panel:

```text
http://localhost:8000/panel
```

> [!IMPORTANT]
> **Dynamic VPN prerequisite**
>
> Before VPN-protected provisioning can succeed, you must add valid VPN credentials to the pool in **Settings -> VPN -> Credentials**.
>
> If no reusable credential leases exist, the scheduler will block provisioning by design.

> [!WARNING]
> Legacy static Gluetun compose topologies are deprecated. Use orchestrator-managed dynamic VPN provisioning.

Stream URL format:

```text
http://<host>:8000/ace/getstream?id=<acestream_id>
```

## Minimal Usage

1. Start containers with `docker-compose.yml`.
2. Open `http://<host>:8000/panel`.
3. Set API key in Settings if protected endpoints are enabled.
4. If VPN is required, add WireGuard credentials in Settings -> VPN and verify at least one lease is available.
5. Use the stream URL format in your player.

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

```text
Authorization: Bearer <API_KEY>
```

Set `API_KEY` as an environment variable or configure it in **Settings -> Orchestrator** after startup.

## Configuration

Most settings can be changed at runtime in the **Settings** panel and are persisted to `app/config/*.json`.
For initial deployment (especially VPN mode), use environment variables. See [`.env.example`](.env.example) and [docs/CONFIG.md](docs/CONFIG.md).

Key environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `API_KEY` | *(none)* | Bearer token for protected endpoints |
| `MIN_REPLICAS` | `2` | Minimum engine containers to keep running |
| `MAX_REPLICAS` | `6` | Maximum concurrent engines |
| `PORT_RANGE_HOST` | `19000-19999` | Host ports mapped to engine containers |
| `VPN_PROVIDER` | `protonvpn` | Default VPN provider for dynamic nodes |
| `VPN_PROTOCOL` | `wireguard` | Default VPN protocol for dynamic nodes |
| `PREFERRED_ENGINES_PER_VPN` | `10` | Target engines per dynamic VPN node |
| `DEBUG_MODE` | `false` | Verbose logging |

## Documentation

- [docs/DEPLOY.md](docs/DEPLOY.md) - Deployment guide
- [docs/CONFIG.md](docs/CONFIG.md) - Environment variable reference
- [docs/API.md](docs/API.md) - API reference
- [docs/SECURITY.md](docs/SECURITY.md) - Authentication, network exposure, TLS
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Control/data plane architecture and runtime mechanics
- [docs/DYNAMIC_VPN_MANAGEMENT.md](docs/DYNAMIC_VPN_MANAGEMENT.md) - Dynamic VPN leases, draining, failover behavior
- [docs/PANEL.md](docs/PANEL.md) - Dashboard user guide
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) - Testing procedures

## Development

Run tests:

```bash
python -m pytest tests/
```
