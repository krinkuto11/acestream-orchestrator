# API

Auth: add `Authorization: Bearer <API_KEY>` in protected endpoints.

## Provisioning
### POST /provision
Creates container with generic parameters (not AceStream).
Body:
```json
{
  "image": "acestream/engine:latest",
  "env": {"CONF":"..."},
  "labels": {"stream_id":"123"},
  "ports": {"40001/tcp": 19001}
}
```
Response:
```json
{"container_id": "…"}
```
### POST /provision/acestream

Starts AceStream with dynamic ports and built `CONF`.
Body:
```json
{
  "image": "acestream/engine:latest",
  "labels": {"stream_id":"123"},
  "env": {"EXTRA_ENV":"X"},
  "host_port": null
}
```
Response:
```json
{
  "container_id":"…",
  "host_http_port":19023,
  "container_http_port":40117,
  "container_https_port":45109
}
```
## Events
### POST /events/stream_started
Body:
```json
{
  "container_id":"…",
  "engine":{"host":"127.0.0.1","port":19023},
  "stream":{"key_type":"infohash","key":"0a48..."},
  "session":{
    "playback_session_id":"e0d10c40…",
    "stat_url":"http://127.0.0.1:19023/ace/stat/…",
    "command_url":"http://127.0.0.1:19023/ace/cmd/…",
    "is_live":1
  },
  "labels":{"stream_id":"ch-42"}
}
```
Response: `StreamState`
### POST /events/stream_ended
Body:
```json
{"container_id":"…","stream_id":"ch-42","reason":"player_stopped"}
```
Response:
```json
{"updated": true, "stream": {…}}
```
### Read Operations

 - GET /engines → EngineState[]

 - GET /engines/{container_id} → {engine, streams[]}

 - GET /streams?status=started|ended&container_id= → StreamState[]

 - GET /streams/{stream_id}/stats?since=<ISO8601> → StreamStatSnapshot[]

 - GET /containers/{container_id} → Docker inspection

 - GET /by-label?key=stream_id&value=ch-42 (protected)

### Control
 - DELETE /containers/{container_id} (protected)
 - POST /gc (protected)
 - POST /scale/{demand:int} (protected)

### Metrics
 - GET /metrics Prometheus:
   - orch_events_started_total
   - orch_events_ended_total
   - orch_collect_errors_total
   - orch_streams_active
   - orch_provision_total{kind="generic|acestream"}

