# Despliegue

## Desarrollo
```bash
cp .env.example .env
docker compose up --build
```

## Producción

 - Fija API_KEY.

 - Limita rangos de puertos a los permitidos por tu firewall.

 - Monta volumen para orchestrator.db.

 - Reverse proxy delante de Uvicorn si necesitas TLS.

 - Si no usas docker:dind, apunta DOCKER_HOST al Docker del host y elimina el servicio docker del compose.

## Variables mínimas

 - `TARGET_IMAGE`
 - `CONTAINER_LABEL`
 - `PORT_RANGE_HOST`, `ACE_HTTP_RANGE`, `ACE_HTTPS_RANGE`
 - `API_KEY`