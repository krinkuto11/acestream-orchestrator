
# Operations

Startup:
- Creates tables, reindexes existing containers, launches collector.

Autoscaling:
- `ensure_minimum()` guarantees `MIN_REPLICAS`.
- `POST /scale/{demand}` to set demand.

Stats collection:
- Every `COLLECT_INTERVAL_S` GET to `stat_url`.
- Data is saved in memory and SQLite.

GC:
- `AUTO_DELETE=true`: on `stream_ended` deletes container with backoff 1/2/3 s.
- `POST /gc`: hook for inactivity GC (placeholder).

Backups:
- Copy `orchestrator.db` with your host's rotation policy.
