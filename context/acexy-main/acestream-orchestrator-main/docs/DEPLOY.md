# Deployment

## Development
```bash
cp .env.example .env
docker compose up --build
```

## Production

 - Set API_KEY.

 - Limit port ranges to those allowed by your firewall.

 - Mount volume for orchestrator.db.

 - Reverse proxy in front of Uvicorn if you need TLS.

 - If not using docker:dind, point DOCKER_HOST to host Docker and remove docker service from compose.

## Minimum variables

 - `TARGET_IMAGE`
 - `CONTAINER_LABEL`
 - `PORT_RANGE_HOST`, `ACE_HTTP_RANGE`, `ACE_HTTPS_RANGE`
 - `API_KEY`