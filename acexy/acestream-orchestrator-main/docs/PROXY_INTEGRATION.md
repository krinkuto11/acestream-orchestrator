# Proxy Integration

### 1) Provision engine (optional on-demand)
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"labels":{"stream_id":"ch-42"}}' \
  http://localhost:8000/provision/acestream
# → host_http_port e.g. 19023
```
### 2) Start playback against the engine
The proxy calls the engine with `format=json` to get control URLs.
```bash
curl "http://127.0.0.1:19023/ace/manifest.m3u8?format=json&infohash=0a48..."
# response.playback_url, response.stat_url, response.command_url
```
### 3) Emit `stream_started`
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
### 4) Emit `stream_ended`
```bash
curl -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"container_id":"<docker_id>","stream_id":"ch-42","reason":"player_stopped"}' \
  http://localhost:8000/events/stream_ended
```
### 5) Query
 - `GET /streams?status=started`
 - `GET /streams/{id}/stats`
 - `GET /by-label?key=stream_id&value=ch-42` (protected)
Notes:
 - `stream_id` in `labels` helps correlate.
 - If you don't send `stream_id`, the orchestrator will generate one with `key|playback_session_id`.