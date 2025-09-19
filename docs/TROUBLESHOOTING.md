# Troubleshooting

## 1) 409/500 when provisioning
Cause: no free ports.
Action: adjust `PORT_RANGE_HOST` and `ACE_*` ranges.

## 2) Engine starts but doesn't serve HLS
- Verify the proxy calls `.../ace/manifest.m3u8?...&format=json`.
- Check the container has `CONF` with `--http-port` and `--bind-all`.

## 3) Panel can't stop the stream
- The "Stop" button calls the engine's `command_url`. May fail due to CORS if accessing from another origin. Use the panel from the same host or a reverse proxy that allows passthrough.

## 4) 401/403 on `/provision/*` or `/events/*`
- Add `Authorization: Bearer <API_KEY>`.
- Verify `API_KEY` is defined in orchestrator's `.env`.

## 5) Orchestrator doesn't see Docker
- In compose, `DOCKER_HOST=tcp://docker:2375` and `docker` service in dind mode.
- If using host Docker: export `DOCKER_HOST=unix:///var/run/docker.sock` and mount the socket.

## 6) Reindex doesn't reflect ports
- Ensure managed containers have labels `acestream.http_port` and `host.http_port`.

Logs:
- Uvicorn on STDOUT. Docker events on Docker host.