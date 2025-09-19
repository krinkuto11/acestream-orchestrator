# Esquema SQLite (SQLAlchemy)

Tablas:
- **engines**
  - engine_key (PK), container_id, host, port, labels JSON, first_seen, last_seen
- **streams**
  - id (PK), engine_key (FK lógico), key_type, key, playback_session_id,
    stat_url, command_url, is_live, started_at, ended_at, status
- **stream_stats**
  - id (PK), stream_id (idx), ts (idx), peers, speed_down, speed_up, downloaded, uploaded, status

Carga inicial:
- En `startup` se crean tablas y se rehidrata estado en memoria.
- `reindex` añade a memoria engines vivos leyendo labels para no reusar puertos.

