
# Event Contract

## stream_started
- Creates or updates `EngineState`.
- Records `StreamState` with `status="started"`.
- Persists in SQLite.

Required fields:
- `engine.host`, `engine.port`
- `stream.key_type` ∈ {content_id, infohash, url, magnet}
- `stream.key`
- `session.playback_session_id`, `stat_url`, `command_url`

Recommended:
- `labels.stream_id` stable to relate to your system.

## stream_ended
- Marks `StreamState.status="ended"` and `ended_at`.
- If `AUTO_DELETE=true` deletes the container. Backoff 1s, 2s, 3s.
- Fallbacks: search by `labels.stream_id` or by `host.http_port` extracted from `stat_url`.

Idempotency:
- Repeating `stream_started` with same `labels.stream_id` overwrites state.
- `stream_ended` on already finished stream returns `updated:false`.

