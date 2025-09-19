# Integración con el Proxy

### 1) Provisionar engine (opcional on-demand)
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"labels":{"stream_id":"ch-42"}}' \
  http://localhost:8000/provision/acestream
# → host_http_port p.ej. 19023
```
### 2) Iniciar playback contra el engine
El proxy llama al engine con `format=json` para obtener URLs de control.
```bash
curl "http://127.0.0.1:19023/ace/manifest.m3u8?format=json&infohash=0a48..."
# response.playback_url, response.stat_url, response.command_url
```
### 3) Emitir `stream_started`
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "container_id":"<docker_id>",
    "engine":{"host":"127.0.0.1","port":19023},
    "stream":{"key_type":"infohash","key":"0a48..."},
    "session":{
      "playback_session_id":"…",
      "stat_url":"http://127.0.0.1:19023/ace/stat/…",
      "command_url":"http://127.0.0.1:19023/ace/cmd/…",
      "is_live":1
    },
    "labels":{"stream_id":"ch-42"}
  }' \
  http://localhost:8000/events/stream_started
```
### 4) Emitir `stream_ended`
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"container_id":"<docker_id>","stream_id":"ch-42","reason":"player_stopped"}' \
  http://localhost:8000/events/stream_ended
```
### 5) Consultar
 - `GET /streams?status=started`
 - `GET /streams/{id}/stats`
 - `GET /by-label?key=stream_id&value=ch-42` (protegido)
Notas:
 - `stream_id` en `labels` ayuda a correlacionar.
 - Si no envías `stream_id`, el orquestador generará uno con `key|playback_session_id`.