from datetime import datetime

import pytest
from fastapi import HTTPException

from app.core.config import cfg
from app.models.schemas import EngineState
from app.services.engine_selection import select_best_engine
from app.services.state import state


def _make_engine(container_id: str, port: int, forwarded: bool = False) -> EngineState:
    now = datetime.now()
    return EngineState(
        container_id=container_id,
        container_name=f"engine-{container_id[:8]}",
        host="127.0.0.1",
        port=port,
        labels={},
        forwarded=forwarded,
        first_seen=now,
        last_seen=now,
        streams=[],
        health_status="unknown",
        last_health_check=None,
        last_stream_usage=None,
        vpn_container=None,
    )


def setup_function():
    state.engines.clear()
    state.streams.clear()
    state.monitor_sessions.clear()


def teardown_function():
    state.engines.clear()
    state.streams.clear()
    state.monitor_sessions.clear()


def test_active_monitor_load_counts_only_active_statuses():
    state.engines["eng-1"] = _make_engine("eng-1", 19000)

    state.upsert_monitor_session(
        "m1",
        {"status": "running", "engine": {"container_id": "eng-1"}},
    )
    state.upsert_monitor_session(
        "m2",
        {"status": "starting", "engine": {"container_id": "eng-1"}},
    )
    state.upsert_monitor_session(
        "m3",
        {"status": "ended", "engine": {"container_id": "eng-1"}},
    )

    loads = state.get_active_monitor_load_by_engine()

    assert loads == {"eng-1": 2}
    assert state.get_active_monitor_container_ids() == {"eng-1"}


def test_select_best_engine_counts_monitor_sessions_in_capacity():
    original_max_streams = cfg.MAX_STREAMS_PER_ENGINE

    try:
        cfg.MAX_STREAMS_PER_ENGINE = 1

        state.engines["eng-1"] = _make_engine("eng-1", 19000)
        state.engines["eng-2"] = _make_engine("eng-2", 19001)

        state.upsert_monitor_session(
            "m1",
            {"status": "running", "engine": {"container_id": "eng-1"}},
        )

        selected, current_load = select_best_engine()
        assert selected.container_id == "eng-2"
        assert current_load == 0

        state.upsert_monitor_session(
            "m2",
            {"status": "running", "engine": {"container_id": "eng-2"}},
        )

        with pytest.raises(HTTPException) as exc_info:
            select_best_engine()

        assert exc_info.value.status_code == 503
        assert "All engines at maximum capacity" in str(exc_info.value.detail)
    finally:
        cfg.MAX_STREAMS_PER_ENGINE = original_max_streams
