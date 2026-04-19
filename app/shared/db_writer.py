import queue
import threading
import logging
from ..persistence.db import SessionLocal

logger = logging.getLogger(__name__)


class DbWriter:
    """Background thread that drains a task queue and writes to the database."""

    def __init__(self):
        self._db_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="state-db-worker"
        )
        self._thread.start()

    def enqueue(self, task):
        if self._stop_event.is_set():
            logger.debug("Skipping DB task enqueue because DB worker is stopping")
            return
        self._db_queue.put(task)

    def stop(self):
        self._stop_event.set()
        self._db_queue.put(None)

    def join(self, timeout: float = 5.0):
        self._thread.join(timeout=timeout)

    def _worker(self):
        while True:
            if self._stop_event.is_set() and self._db_queue.empty():
                break

            try:
                task = self._db_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                if task is None:
                    break

                with SessionLocal() as s:
                    try:
                        task(s)
                        s.commit()
                    except Exception as e:
                        logger.error(f"Background DB write failed: {e}")
                        s.rollback()
            except Exception as e:
                logger.error(f"DB worker loop error: {e}")
            finally:
                self._db_queue.task_done()
