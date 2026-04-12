import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from ..models.db_models import StatRow, StreamRow
from .db import engine, get_session

logger = logging.getLogger(__name__)


class DatabaseMaintenanceService:
    """Prunes old telemetry rows and periodically runs VACUUM."""

    def __init__(self, retention_days: int = 7, interval_seconds: int = 24 * 60 * 60):
        self._retention_days = max(1, int(retention_days))
        self._interval_seconds = max(60, int(interval_seconds))
        self._task = None
        self._stop = asyncio.Event()

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "DB maintenance service started (retention=%s days, interval=%ss)",
            self._retention_days,
            self._interval_seconds,
        )

    async def stop(self):
        self._stop.set()
        if self._task:
            await self._task

    async def _run(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                await asyncio.to_thread(self._prune_and_vacuum)

    def _prune_and_vacuum(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)

        deleted_stats = 0
        deleted_streams = 0
        start_ts = time.monotonic()

        try:
            with get_session() as session:
                deleted_stats = session.query(StatRow).filter(StatRow.ts < cutoff).delete(synchronize_session=False)
                deleted_streams = (
                    session.query(StreamRow)
                    .filter(StreamRow.status == "ended", StreamRow.ended_at.is_not(None), StreamRow.ended_at < cutoff)
                    .delete(synchronize_session=False)
                )
                session.commit()

            logger.info(
                "DB maintenance pruning completed: deleted_stats=%s deleted_ended_streams=%s cutoff=%s",
                deleted_stats,
                deleted_streams,
                cutoff.isoformat(),
            )
        except Exception as e:
            logger.warning(f"DB maintenance pruning failed: {e}", exc_info=True)
            return

        vacuum_started = time.monotonic()
        logger.info("DB maintenance VACUUM started")
        raw_conn = None
        cursor = None
        try:
            raw_conn = engine.raw_connection()
            cursor = raw_conn.cursor()
            cursor.execute("VACUUM;")
            raw_conn.commit()
            logger.info(
                "DB maintenance VACUUM finished in %.2fs (total maintenance %.2fs)",
                time.monotonic() - vacuum_started,
                time.monotonic() - start_ts,
            )
        except Exception as e:
            logger.warning(f"DB maintenance VACUUM failed: {e}", exc_info=True)
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            if raw_conn is not None:
                try:
                    raw_conn.close()
                except Exception:
                    pass



def migrate_bitrate_column():
    \"\"\"Ensure the streams table has the bitrate column.\"\"\"
    import sqlalchemy
    from sqlalchemy import text
    
    try:
        with engine.connect() as conn:
            inspector = sqlalchemy.inspect(engine)
            columns = [c['name'] for c in inspector.get_columns(\"streams\")]
            
            if \"bitrate\" not in columns:
                logger.info(\"Migrating database: Adding 'bitrate' column to 'streams' table\")
                conn.execute(text(\"ALTER TABLE streams ADD COLUMN bitrate INTEGER\"))
                conn.commit()
                logger.info(\"Migration successful: 'bitrate' column added\")
    except Exception as e:
        logger.error(f\"Database migration failed: {e}\", exc_info=True)


# Global maintenance service instance
# Runs once per day and permanently removes telemetry older than 7 days.
db_maintenance_service = DatabaseMaintenanceService(retention_days=7, interval_seconds=24 * 60 * 60)
