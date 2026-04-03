import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.models.schemas import EngineState
from app.services.health_manager import HealthManager


def _engine(container_id: str, vpn_container: str = "gluetun") -> EngineState:
    now = datetime.now(timezone.utc)
    return EngineState(
        container_id=container_id,
        container_name=container_id,
        host="127.0.0.1",
        port=6878,
        labels={},
        forwarded=False,
        first_seen=now,
        last_seen=now,
        streams=[],
        health_status="unknown",
        vpn_container=vpn_container,
    )


def test_get_target_vpn_prefers_ready_node_state():
    manager = HealthManager()

    with patch("app.services.health_manager.state.list_vpn_nodes", return_value=[
             {"container_name": "gluetun", "healthy": False, "condition": "notready"},
             {"container_name": "gluetun2", "healthy": True, "condition": "ready"},
         ]), \
         patch("app.services.health_manager.state.get_engines_by_vpn", side_effect=lambda vpn: []):
        target = manager._get_target_vpn_for_provisioning()

    assert target == "gluetun2"


def test_waits_only_if_target_vpn_is_notready():
    manager = HealthManager()

    with patch("app.services.health_manager.state.list_vpn_nodes", return_value=[
         {"container_name": "gluetun", "healthy": True, "condition": "notready"},
    ]), \
         patch.object(manager, "_get_target_vpn_for_provisioning", return_value="gluetun"), \
         patch("app.services.health_manager.cfg.MIN_REPLICAS", 2):
        assert manager._should_wait_for_vpn_recovery([]) is True

    with patch("app.services.health_manager.state.list_vpn_nodes", return_value=[
         {"container_name": "gluetun2", "healthy": True, "condition": "ready"},
    ]), \
         patch.object(manager, "_get_target_vpn_for_provisioning", return_value="gluetun2"), \
         patch("app.services.health_manager.cfg.MIN_REPLICAS", 2):
        assert manager._should_wait_for_vpn_recovery([]) is False


def test_manual_mode_tracks_health_but_skips_replacements():
    manager = HealthManager()
    engines = [_engine("e-1")]

    with patch("app.services.settings_persistence.SettingsPersistence.load_engine_settings", return_value={"manual_mode": True}), \
         patch("app.services.health_manager.state.list_engines", return_value=engines), \
         patch("app.services.health_manager.state.update_engine_health"), \
         patch("app.services.health_manager.check_acestream_health", return_value="unhealthy"), \
         patch.object(manager, "_replace_unhealthy_engines", new=AsyncMock()) as replace_unhealthy:
        asyncio.run(manager._check_and_manage_health())

    replace_unhealthy.assert_not_called()
