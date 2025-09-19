

# Visión general

Objetivo: levantar contenedores AceStream on-demand para servir streams que solicita un proxy. El orquestador:
- Provisiona contenedores con puertos internos y externos dinámicos.
- Recibe eventos `stream_started` y `stream_ended`.
- Recolecta estadísticas periódicas desde `stat_url`.
- Persiste engines, streams y estadísticas en SQLite.
- Expone un panel simple y métricas Prometheus.

Componentes:
- **Orchestrator API**: FastAPI sobre Uvicorn.
- **Docker host**: `docker:dind` en Compose o Docker del host vía `DOCKER_HOST`.
- **Panel**: HTML estático en `/panel`.
- **Proxy**: cliente que habla con el engine AceStream y con el orquestador.

Flujo típico:
1. Proxy pide `POST /provision/acestream` si no hay engine disponible.
2. Orquestador arranca contenedor con flags `--http-port`, `--https-port` y binding host.
3. Proxy inicia playback contra `http://<host>:<host_http_port>/ace/manifest.m3u8?...&format=json`.
4. Proxy obtiene `stat_url` y `command_url` del engine y envía `POST /events/stream_started`.
5. Orquestador recolecta stats periódicamente desde `stat_url`.
6. Al acabar, el proxy envía `POST /events/stream_ended`. Si `AUTO_DELETE=true`, el orquestador borra el contenedor.
