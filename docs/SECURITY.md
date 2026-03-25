# Security

## Authentication

Protected API endpoints require a Bearer token:

```
Authorization: Bearer <API_KEY>
```

Set `API_KEY` as an environment variable (or in **Settings → Orchestrator**).  
When `API_KEY` is not set, all endpoints are accessible without authentication — do not expose the service publicly in this state.

**Protected endpoints include** (non-exhaustive):
- `POST /provision`, `POST /provision/acestream`
- `POST /events/stream_started`, `POST /events/stream_ended`
- `DELETE /containers/{id}`, `POST /gc`, `POST /scale/{demand}`
- `POST /custom-variant/config`, `POST /custom-variant/reprovision`
- `POST /events/cleanup`, `POST /cache/clear`
- `POST /ace/monitor/legacy/start`, `DELETE /ace/monitor/legacy/{id}`

**Public endpoints** (no auth required):
- `GET /engines`, `GET /streams`, `GET /orchestrator/status`
- `GET /metrics`, `GET /metrics/dashboard`
- `GET /vpn/status`, `GET /health`
- `GET /ace/getstream`, `GET /ace/preflight`
- `GET /panel` (dashboard UI)

## Network Exposure

- **Port 8000**: Orchestrator API and dashboard. Restrict to trusted networks or place behind a TLS-terminating reverse proxy (nginx, Traefik, Caddy).
- **Port 8001**: Gluetun HTTP control server. Must **not** be publicly accessible — it provides unauthenticated access to VPN controls and port-forwarding state.
- **Engine ports (`19000-19999`)**: AceStream HTTP ports forwarded through Gluetun. Restrict to the proxy or player that consumes them.
- **Docker socket (`/var/run/docker.sock`)**: The orchestrator requires Docker socket access. Do not bind the Docker daemon to a TCP socket on untrusted interfaces.

## TLS

The orchestrator does not terminate TLS. Use a reverse proxy for HTTPS:

```nginx
server {
    listen 443 ssl;
    server_name your.domain;

    ssl_certificate     /etc/ssl/certs/your.crt;
    ssl_certificate_key /etc/ssl/private/your.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## CORS

CORS is not enabled by default. If the panel or API is accessed from a different origin, configure the allowed origins at the reverse-proxy level rather than relaxing CORS globally.

## Data at Rest

- `orchestrator.db` — SQLite database containing engine URLs, stream records, and statistics. Restrict file permissions and back it up regularly.
- `app/config/*.json` — Persisted UI settings including the API key. Mount as a volume and protect accordingly.

## Docker Security

- Avoid running `docker:dind` on untrusted networks.
- Pin image versions in compose files instead of using `:latest` in production.
- Use `ENGINE_MEMORY_LIMIT` to cap per-engine resource consumption.
- Consider running the orchestrator container as a non-root user if your Docker setup permits it.

## Secrets Management

- Never commit `API_KEY` or VPN credentials to version control.
- Use Docker secrets or a secrets manager to inject sensitive values rather than plain environment variables in compose files.
- Rotate `API_KEY` by updating the environment variable and restarting the orchestrator.
