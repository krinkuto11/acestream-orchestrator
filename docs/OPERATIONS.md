
# Operación

Arranque:
- Crea tablas, reindexa contenedores existentes, lanza colector.

Autoescalado:
- `ensure_minimum()` garantiza `MIN_REPLICAS`.
- `POST /scale/{demand}` para fijar demanda.

Recolección de stats:
- Cada `COLLECT_INTERVAL_S` GET a `stat_url`.
- Los datos se guardan en memoria y en SQLite.

GC:
- `AUTO_DELETE=true`: al `stream_ended` borra contenedor con backoff 1/2/3 s.
- `POST /gc`: gancho para GC por inactividad (placeholder).

Backups:
- Copia `orchestrator.db` con política de rotación de tu host.
