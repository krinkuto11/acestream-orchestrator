from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest

from app.services.db import engine, set_sqlite_pragma
from app.services.db_maintenance import DatabaseMaintenanceService


def test_set_sqlite_pragma_applies_wal_settings_for_sqlite():
    if engine.url.get_backend_name() != "sqlite":
        pytest.skip("SQLite-specific pragma test")

    dbapi_connection = MagicMock()
    cursor = MagicMock()
    dbapi_connection.cursor.return_value = cursor

    set_sqlite_pragma(dbapi_connection, None)

    cursor.execute.assert_has_calls(
        [
            call("PRAGMA journal_mode=WAL;"),
            call("PRAGMA synchronous=NORMAL;"),
            call("PRAGMA cache_size=-64000;"),
        ]
    )
    cursor.close.assert_called_once()


def test_db_maintenance_prunes_and_vacuums():
    service = DatabaseMaintenanceService(retention_days=7, interval_seconds=86400)

    session = MagicMock()
    stats_query = MagicMock()
    streams_query = MagicMock()

    session.query.side_effect = [stats_query, streams_query]

    stats_query.filter.return_value = stats_query
    stats_query.delete.return_value = 11

    streams_query.filter.return_value = streams_query
    streams_query.delete.return_value = 4

    @contextmanager
    def _fake_session_ctx():
        yield session

    raw_conn = MagicMock()
    cursor = MagicMock()
    raw_conn.cursor.return_value = cursor

    with patch("app.services.db_maintenance.get_session", return_value=_fake_session_ctx()), \
         patch("app.services.db_maintenance.engine.raw_connection", return_value=raw_conn):
        service._prune_and_vacuum()

    assert session.commit.call_count == 1
    cursor.execute.assert_called_once_with("VACUUM;")
    raw_conn.commit.assert_called_once()
    cursor.close.assert_called_once()
    raw_conn.close.assert_called_once()
