# SQLite Schema (SQLAlchemy)

Tables:
- **engines**
  - engine_key (PK), container_id, host, port, labels JSON, first_seen, last_seen
- **streams**
  - id (PK), engine_key (logical FK), key_type, key, playback_session_id,
    stat_url, command_url, is_live, started_at, ended_at, status
- **stream_stats**
  - id (PK), stream_id (idx), ts (idx), peers, speed_down, speed_up, downloaded, uploaded, status

Initial load:
- On `startup` creates tables and rehydrates in-memory state.
- `reindex` adds live engines to memory reading labels to avoid port reuse.

