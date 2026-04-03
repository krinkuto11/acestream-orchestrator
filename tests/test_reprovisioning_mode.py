import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

from app.models.schemas import EngineState
from app.services.autoscaler import EngineController
from app.services.state import state


def _engine(container_id: str, config_hash: str) -> EngineState:
    now = datetime.now(timezone.utc)
    return EngineState(
        container_id=container_id,
        container_name=container_id,
        host="127.0.0.1",
        port=6878,
        labels={"acestream.config_hash": config_hash},
        forwarded=False,
        first_seen=now,
        last_seen=now,
        streams=[],
        health_status="healthy",
    )


def setup_function():
    state.clear_state()


def teardown_function():
    state.clear_state()


def test_target_engine_config_generation_increments_only_on_hash_change():
    first = state.set_target_engine_config("hash-a")
    same = state.set_target_engine_config("hash-a")
    changed = state.set_target_engine_config("hash-b")

    assert first["generation"] == 1
    assert first["changed"] is True
    assert same["generation"] == 1
    assert same["changed"] is False
    assert changed["generation"] == 2
    assert changed["changed"] is True


def test_controller_enqueues_eviction_for_outdated_hash():
    state.set_target_engine_config("new-hash")
    state.engines["e-old"] = _engine("e-old", "old-hash")

    controller = EngineController()

    with patch.object(state, "list_pending_scaling_intents", return_value=[]), \
         patch.object(state, "emit_scaling_intent") as emit_intent:
        asyncio.run(controller._enqueue_outdated_engine_termination_intents())

    emit_intent.assert_called_once()
    details = emit_intent.call_args.kwargs["details"]
    assert details["eviction_reason"] == "config_hash_mismatch"
    assert details["container_id"] == "e-old"


def test_custom_variant_reprovision_endpoint_triggers_rolling_update_payload():
    from app import main

    with patch("app.main._trigger_engine_generation_rollout", return_value={"changed": True, "generation": 5, "config_hash": "abc123"}), \
         patch.object(main.state, "list_engines", return_value=[object(), object()]):
        response = asyncio.run(main.reprovision_all_engines())

    assert response["rolling_update"]["changed"] is True
    assert response["rolling_update"]["target_generation"] == 5
    assert response["rolling_update"]["target_hash"] == "abc123"


def test_custom_variant_reprovision_status_is_computed_declaratively():
    from app import main

    engines = [
        _engine("e-1", "target-hash"),
        _engine("e-2", "old-hash"),
        _engine("e-3", "target-hash"),
        _engine("e-4", "old-hash"),
    ]

    with patch.object(main.state, "get_target_engine_config", return_value={"config_hash": "target-hash", "generation": 7}), \
         patch.object(main.state, "get_desired_replica_count", return_value=3), \
         patch.object(main.state, "list_engines", return_value=engines):
        status = main.get_reprovision_status()

    assert status["in_progress"] is True
    assert status["status"] == "in_progress"
    assert status["total_engines"] == 3
    assert status["engines_provisioned"] == 2
    assert status["target_generation"] == 7
    assert status["current_phase"] == "stopping"


def test_update_engine_settings_no_state_shadowing_and_scale_up_to_min():
    from app import main

    settings = main.EngineSettingsUpdate(min_replicas=10, max_replicas=20, manual_mode=False)

    with patch("app.services.custom_variant_config.detect_platform", return_value="amd64"), \
            patch.object(main.cfg, "MIN_REPLICAS", 2), \
            patch.object(main.cfg, "MAX_REPLICAS", 6), \
         patch("app.services.settings_persistence.SettingsPersistence.load_engine_settings", return_value={
             "min_replicas": 2,
             "max_replicas": 6,
             "auto_delete": True,
             "engine_variant": "AceServe-amd64",
             "use_custom_variant": False,
             "platform": "amd64",
             "manual_mode": False,
             "manual_engines": [],
         }), \
         patch("app.services.settings_persistence.SettingsPersistence.save_engine_settings", return_value=True), \
         patch("app.main._trigger_engine_generation_rollout", return_value={"changed": False, "generation": 1, "config_hash": "h"}), \
         patch.object(main.state, "get_desired_replica_count", return_value=2), \
         patch.object(main.state, "set_desired_replica_count") as set_desired, \
         patch.object(main.engine_controller, "request_reconcile"):
        response = asyncio.run(main.update_engine_settings(settings))

    assert response["min_replicas"] == 10
    assert response["max_replicas"] == 20
    set_desired.assert_called_once_with(10)


def test_update_engine_settings_clamps_desired_down_to_new_max():
    from app import main

    settings = main.EngineSettingsUpdate(max_replicas=8)

    with patch("app.services.custom_variant_config.detect_platform", return_value="amd64"), \
            patch.object(main.cfg, "MIN_REPLICAS", 2), \
            patch.object(main.cfg, "MAX_REPLICAS", 20), \
         patch("app.services.settings_persistence.SettingsPersistence.load_engine_settings", return_value={
             "min_replicas": 2,
             "max_replicas": 20,
             "auto_delete": True,
             "engine_variant": "AceServe-amd64",
             "use_custom_variant": False,
             "platform": "amd64",
             "manual_mode": False,
             "manual_engines": [],
         }), \
         patch("app.services.settings_persistence.SettingsPersistence.save_engine_settings", return_value=True), \
         patch("app.main._trigger_engine_generation_rollout", return_value={"changed": False, "generation": 1, "config_hash": "h"}), \
         patch.object(main.state, "get_desired_replica_count", return_value=15), \
         patch.object(main.state, "set_desired_replica_count") as set_desired, \
         patch.object(main.engine_controller, "request_reconcile"):
        response = asyncio.run(main.update_engine_settings(settings))

    assert response["max_replicas"] == 8
    set_desired.assert_called_once_with(8)
