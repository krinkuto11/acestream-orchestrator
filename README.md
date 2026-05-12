# AceStream Orchestrator

<img src="app/icons/favicon-96x96-dark.png" alt="AceStream Orchestrator Logo" width="96" height="96" />

On-demand AceStream engine orchestration with a declarative control plane, built-in proxy, dynamic VPN lifecycle management, metrics, and a web panel.  

> [!WARNING]
> **LEGAL DISCLAIMER: EDUCATIONAL USE ONLY**
> 
> This software is a proof-of-concept designed strictly for **educational and research purposes**. The primary intent of this project is to demonstrate the feasibility of a high availability scenario for the AceStream protocol and should not be used for illegal acts.
<img width="1512" height="861" alt="Screenshot 2026-05-12 at 16 31 09" src="https://github.com/user-attachments/assets/a169ff3f-70f0-4d79-9e86-3f13fdae1e6d" />

## Quick Start


```bash
git clone https://github.com/krinkuto11/acestream-orchestrator.git && cd acestream-orchestrator
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


Stream URL format:

```text
http://<host>:8000/ace/getstream?id=<acestream_id>
```

## Minimal Usage

1. Start containers with `docker-compose.yml`.
2. Open `http://<host>:8000/panel`.
3. If VPN is required, add WireGuard credentials in Settings -> VPN and verify at least one lease is available.
4. Use the stream URL format in your player.

## Modify M3U Playlist

If your playlist contains AceStream IDs or direct AceStream engine URLs, rewrite each entry to use the orchestrator endpoint.

Orchestrator Endpoint (Returns the modified M3U):

```text
http://<host>:8000/api/v1/modify_m3u?host=<host>&port=8000&m3u_url=<url>
```

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
Docs are available at `/api/v1/docs`

```text
GET  /panel
GET  /ace/getstream?id=<id>
GET  /api/v1/engines
GET  /api/v1/streams?status=started
GET  /api/v1/orchestrator/status
GET  /api/v1/metrics
GET  /api/v1/metrics/dashboard
POST /api/v1/provision/acestream
```

Protected endpoints require:

```text
Authorization: Bearer <API_KEY>
```

Set `API_KEY` as an environment variable. If not set, no endpoint won't be protected.

## Configuration

Most settings can be changed at runtime in the **Settings** panel and are persisted to the SQLite database (`orchestrator.db` inside the config volume).
For initial deployment (especially VPN mode), use environment variables. See [docs/CONFIG.md](docs/CONFIG.md).

Key environment variables:

| Variable                    | Default       | Purpose                                   |
| --------------------------- | ------------- | ----------------------------------------- |
| `API_KEY`                   | *(none)*      | Bearer token for protected endpoints      |
| `MIN_REPLICAS`              | `2`           | Minimum engine containers to keep running |
| `MAX_REPLICAS`              | `6`           | Maximum concurrent engines                |
| `PORT_RANGE_HOST`           | `19000-19999` | Host ports mapped to engine containers    |
| `VPN_PROVIDER`              | `protonvpn`   | Default VPN provider for dynamic nodes    |
| `VPN_PROTOCOL`              | `wireguard`   | Default VPN protocol for dynamic nodes    |
| `PREFERRED_ENGINES_PER_VPN` | `10`          | Target engines per dynamic VPN node       |
| `DEBUG_MODE`                | `false`       | Verbose logging                           |

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

```bash
cd app/orchestrator && go test ./...
```
