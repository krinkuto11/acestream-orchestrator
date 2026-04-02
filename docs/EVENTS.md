
# Event Contract

This document includes both external API events and internal runtime events used by the control plane.

## External API Events

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

## Internal Runtime Events (Docker Informer)

The Docker event watcher subscribes to container lifecycle events and applies state transitions immediately.

Watched actions:
- `start`
- `die`
- `destroy`
- `health_status: healthy`
- `health_status: unhealthy`

Scope:
- Managed AceStream engines (identified by orchestrator container label)
- Gluetun containers used for VPN routing

Behavior:
- `start`: engine is inserted/updated in state and persisted
- `die`: engine health marked unhealthy
- `destroy`: engine removed from state
- `health_status:*`: engine or VPN node health updated

Notes:
- Provision responses can precede `start` event application; this is expected.
- Runtime state is authoritative after event processing.

## Internal Scaling Intents

The engine controller emits scaling intents during reconciliation.

Intent types:
- `create_request`
- `terminate_request`

Statuses:
- `pending`
- `applied`
- `failed`
- `blocked`

Intent records are retained in bounded in-memory history for observability and troubleshooting.

