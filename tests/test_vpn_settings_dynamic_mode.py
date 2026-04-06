import asyncio
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace


def _vpn_payload(main_module, *, enabled: bool, dynamic_vpn_management: bool):
    return main_module.VPNSettingsUpdate(
        enabled=enabled,
        dynamic_vpn_management=dynamic_vpn_management,
        preferred_engines_per_vpn=10,
        protocol="wireguard",
        provider="protonvpn",
        regions=[],
        credentials=[],
        api_port=8001,
        health_check_interval_s=5,
        port_cache_ttl_s=60,
        restart_engines_on_reconnect=True,
        unhealthy_restart_timeout_s=60,
    )


def test_get_vpn_settings_forces_dynamic_true():
    from app import main

    persisted = {
        "enabled": True,
        "dynamic_vpn_management": False,
        "preferred_engines_per_vpn": 10,
        "protocol": "wireguard",
        "provider": "protonvpn",
        "regions": [],
        "credentials": [],
        "api_port": 8001,
        "health_check_interval_s": 5,
        "port_cache_ttl_s": 60,
        "restart_engines_on_reconnect": True,
        "unhealthy_restart_timeout_s": 60,
    }

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value=persisted):
        response = main.get_vpn_settings()

    assert response["dynamic_vpn_management"] is True


def test_update_vpn_settings_enabled_always_uses_dynamic_controller():
    from app import main

    settings = _vpn_payload(main, enabled=True, dynamic_vpn_management=False)

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={}), \
         patch("app.services.settings_persistence.SettingsPersistence.save_vpn_config", return_value=True), \
         patch("app.main.credential_manager.configure", new=AsyncMock(return_value={
             "dynamic_vpn_management": True,
             "max_vpn_capacity": 1,
             "available": 1,
             "leased": 0,
         })) as configure_mock, \
         patch.object(main.vpn_controller, "is_running", return_value=False), \
         patch.object(main.vpn_controller, "start", new=AsyncMock()) as start_mock, \
         patch.object(main.vpn_controller, "stop", new=AsyncMock()) as stop_mock:
        response = asyncio.run(main.update_vpn_settings(settings))

    assert response["enabled"] is True
    assert response["dynamic_vpn_management"] is True
    assert configure_mock.call_args.kwargs["dynamic_vpn_management"] is True
    start_mock.assert_awaited_once()
    stop_mock.assert_not_awaited()


def test_update_vpn_settings_disabled_requests_cleanup_reconcile():
    from app import main

    settings = _vpn_payload(main, enabled=False, dynamic_vpn_management=False)

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={}), \
         patch("app.services.settings_persistence.SettingsPersistence.save_vpn_config", return_value=True), \
         patch("app.main.credential_manager.configure", new=AsyncMock(return_value={
             "dynamic_vpn_management": True,
             "max_vpn_capacity": 0,
             "available": 0,
             "leased": 0,
         })), \
         patch.object(main.vpn_controller, "is_running", return_value=True), \
         patch.object(main.vpn_controller, "start", new=AsyncMock()) as start_mock, \
         patch.object(main.vpn_controller, "request_reconcile") as request_reconcile, \
         patch.object(main.state, "set_desired_vpn_node_count") as set_desired:
        response = asyncio.run(main.update_vpn_settings(settings))

    assert response["enabled"] is False
    assert response["dynamic_vpn_management"] is True
    start_mock.assert_not_awaited()
    request_reconcile.assert_called_once_with(reason="vpn_disabled_cleanup")
    set_desired.assert_called_once_with(0)


def test_update_vpn_settings_trigger_migration_marks_non_vpn_engines_draining():
    from app import main

    settings = main.VPNSettingsUpdate(
        enabled=True,
        dynamic_vpn_management=False,
        preferred_engines_per_vpn=10,
        protocol="wireguard",
        provider="protonvpn",
        regions=[],
        credentials=[],
        api_port=8001,
        health_check_interval_s=5,
        port_cache_ttl_s=60,
        restart_engines_on_reconnect=True,
        unhealthy_restart_timeout_s=60,
        trigger_migration=True,
    )

    engines = [
        SimpleNamespace(container_id="engine-non-vpn", vpn_container=None),
        SimpleNamespace(container_id="engine-vpn", vpn_container="gluetun-vpn-1"),
    ]

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={"enabled": False}), \
         patch("app.services.settings_persistence.SettingsPersistence.save_vpn_config", return_value=True), \
         patch("app.main.credential_manager.configure", new=AsyncMock(return_value={
             "dynamic_vpn_management": True,
             "max_vpn_capacity": 1,
             "available": 1,
             "leased": 0,
         })), \
         patch.object(main.vpn_controller, "is_running", return_value=False), \
         patch.object(main.vpn_controller, "start", new=AsyncMock()), \
         patch.object(main.state, "list_engines", return_value=engines), \
         patch.object(main.state, "mark_engine_draining", return_value=True) as mark_draining:
        response = asyncio.run(main.update_vpn_settings(settings))

    assert response["migration_requested"] is True
    assert response["migration_marked_engines"] == 1
    mark_draining.assert_called_once_with("engine-non-vpn", reason="vpn_enable_migration")


def test_update_vpn_settings_trigger_migration_ignored_when_already_enabled():
    from app import main

    settings = main.VPNSettingsUpdate(
        enabled=True,
        dynamic_vpn_management=False,
        preferred_engines_per_vpn=10,
        protocol="wireguard",
        provider="protonvpn",
        regions=[],
        credentials=[],
        api_port=8001,
        health_check_interval_s=5,
        port_cache_ttl_s=60,
        restart_engines_on_reconnect=True,
        unhealthy_restart_timeout_s=60,
        trigger_migration=True,
    )

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={"enabled": True}), \
         patch("app.services.settings_persistence.SettingsPersistence.save_vpn_config", return_value=True), \
         patch("app.main.credential_manager.configure", new=AsyncMock(return_value={
             "dynamic_vpn_management": True,
             "max_vpn_capacity": 1,
             "available": 1,
             "leased": 0,
         })), \
         patch.object(main.vpn_controller, "is_running", return_value=True), \
         patch.object(main.state, "list_engines", return_value=[]), \
         patch.object(main.state, "mark_engine_draining", return_value=True) as mark_draining:
        response = asyncio.run(main.update_vpn_settings(settings))

    assert response["migration_requested"] is True
    assert response["migration_marked_engines"] == 0
    mark_draining.assert_not_called()


def test_update_vpn_settings_trigger_migration_on_disable_marks_vpn_engines_draining():
    from app import main

    settings = main.VPNSettingsUpdate(
        enabled=False,
        dynamic_vpn_management=False,
        preferred_engines_per_vpn=10,
        protocol="wireguard",
        provider="protonvpn",
        regions=[],
        credentials=[],
        api_port=8001,
        health_check_interval_s=5,
        port_cache_ttl_s=60,
        restart_engines_on_reconnect=True,
        unhealthy_restart_timeout_s=60,
        trigger_migration=True,
    )

    engines = [
        SimpleNamespace(container_id="engine-vpn", vpn_container="gluetun-vpn-1"),
        SimpleNamespace(container_id="engine-non-vpn", vpn_container=None),
    ]

    with patch("app.services.settings_persistence.SettingsPersistence.load_vpn_config", return_value={"enabled": True}), \
         patch("app.services.settings_persistence.SettingsPersistence.save_vpn_config", return_value=True), \
         patch("app.main.credential_manager.configure", new=AsyncMock(return_value={
             "dynamic_vpn_management": True,
             "max_vpn_capacity": 1,
             "available": 1,
             "leased": 0,
         })), \
         patch.object(main.vpn_controller, "is_running", return_value=True), \
         patch.object(main.vpn_controller, "request_reconcile") as request_reconcile, \
         patch.object(main.state, "set_desired_vpn_node_count") as set_desired, \
         patch.object(main.state, "list_engines", return_value=engines), \
         patch.object(main.state, "mark_engine_draining", return_value=True) as mark_draining:
        response = asyncio.run(main.update_vpn_settings(settings))

    assert response["migration_requested"] is True
    assert response["migration_marked_engines"] == 1
    set_desired.assert_called_once_with(0)
    request_reconcile.assert_called_once_with(reason="vpn_disabled_cleanup")
    mark_draining.assert_called_once_with("engine-vpn", reason="vpn_enable_migration")
