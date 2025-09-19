# Troubleshooting

## 1) 409/500 al provisionar
Causa: sin puertos libres.
Acción: ajusta `PORT_RANGE_HOST` y rangos `ACE_*`.

## 2) Engine arranca pero no sirve HLS
- Verifica que el proxy llama `.../ace/manifest.m3u8?...&format=json`.
- Comprueba que el contenedor tiene `CONF` con `--http-port` y `--bind-all`.

## 3) El panel no puede parar el stream
- El botón “Stop” llama al `command_url` del engine. Puede fallar por CORS si accedes desde otro origen. Usa el panel desde el mismo host o un reverse proxy que permita el paso.

## 4) 401/403 en `/provision/*` o `/events/*`
- Añade `Authorization: Bearer <API_KEY>`.
- Verifica que `API_KEY` está definido en `.env` del orquestador.

## 5) El orquestador no ve Docker
- En compose, `DOCKER_HOST=tcp://docker:2375` y servicio `docker` en modo dind.
- Si usas Docker del host: exporta `DOCKER_HOST=unix:///var/run/docker.sock` y monta el socket.

## 6) Reindex no refleja puertos
- Asegúrate de que los contenedores gestionados tienen labels `acestream.http_port` y `host.http_port`.

Logs:
- Uvicorn en STDOUT. Docker events en el host Docker.
