
# Contrato de eventos

## stream_started
- Crea o actualiza `EngineState`.
- Registra `StreamState` con `status="started"`.
- Persiste en SQLite.

Campos obligatorios:
- `engine.host`, `engine.port`
- `stream.key_type` ∈ {content_id, infohash, url, magnet}
- `stream.key`
- `session.playback_session_id`, `stat_url`, `command_url`

Recomendado:
- `labels.stream_id` estable para relacionar con tu sistema.

## stream_ended
- Marca `StreamState.status="ended"` y `ended_at`.
- Si `AUTO_DELETE=true` borra el contenedor. Backoff 1s, 2s, 3s.
- Fallbacks: busca por `labels.stream_id` o por `host.http_port` extraído de `stat_url`.

Idempotencia:
- Repetir `stream_started` con misma `labels.stream_id` sobrescribe estado.
- `stream_ended` sobre stream ya finalizado devuelve `updated:false`.

