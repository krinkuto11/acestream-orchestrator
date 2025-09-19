# Configuración (.env)

Variables y valores por defecto:

- `APP_PORT=8000`
- `DOCKER_NETWORK=` nombre de red Docker. Vacío → red por defecto.
- `TARGET_IMAGE=acestream/engine:latest`
- `MIN_REPLICAS=0` · `MAX_REPLICAS=20`
- `CONTAINER_LABEL=ondemand.app=myservice` etiqueta de gestión.
- `STARTUP_TIMEOUT_S=25` tiempo máx. de arranque contenedor.
- `IDLE_TTL_S=600` reservado para GC por inactividad.

Colector:
- `COLLECT_INTERVAL_S=5`
- `STATS_HISTORY_MAX=720` muestras guardadas en memoria por stream.

Puertos:
- `PORT_RANGE_HOST=19000-19999` puertos host disponibles.
- `ACE_HTTP_RANGE=40000-44999` puertos internos para `--http-port`.
- `ACE_HTTPS_RANGE=45000-49999` puertos internos para `--https-port`.
- `ACE_MAP_HTTPS=false` si `true` mapea también el HTTPS a host.

Seguridad:
- `API_KEY=...` API Bearer para `/provision/*` y `/events/*`.

Persistencia:
- `DB_URL=sqlite:///./orchestrator.db`

Auto-GC:
- `AUTO_DELETE=false` si `true`, borra contenedor al `stream_ended`.

Etiquetas en contenedores creados:
- `acestream.http_port=<int>`
- `acestream.https_port=<int>`
- `host.http_port=<int>`
- `host.https_port=<int>` opcional si `ACE_MAP_HTTPS=true`
- y `CONTAINER_LABEL` (clave=valor) para identificar los gestionados.
