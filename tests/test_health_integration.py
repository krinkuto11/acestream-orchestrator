import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.services.health_manager import EngineHealthStatus, HealthManager


def test_replace_engine_evicts_unhealthy_container():
    manager = HealthManager()
    engine = SimpleNamespace(container_id="e-1")
    manager._engine_health[engine.container_id] = EngineHealthStatus(engine.container_id)

    with patch("app.services.health_manager.stop_container") as stop_container, \
         patch("app.services.health_manager.state.remove_engine") as remove_engine:
        asyncio.run(manager._replace_engine(engine))

    stop_container.assert_called_once_with("e-1")
    remove_engine.assert_called_once_with("e-1")
    assert "e-1" not in manager._engine_health


def test_replace_engine_resets_flags_on_evict_error():
    manager = HealthManager()
    engine = SimpleNamespace(container_id="e-2")
    manager._engine_health[engine.container_id] = EngineHealthStatus(engine.container_id)
    manager._engine_health[engine.container_id].marked_for_replacement = True

    with patch("app.services.health_manager.stop_container", side_effect=RuntimeError("boom")), \
         patch("app.services.health_manager.state.remove_engine"):
        asyncio.run(manager._replace_engine(engine))

    health = manager._engine_health[engine.container_id]
    assert health.marked_for_replacement is False
    assert health.replacement_started is False
