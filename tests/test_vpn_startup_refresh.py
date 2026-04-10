import asyncio
from unittest.mock import AsyncMock, patch


def test_startup_refresh_skipped_when_vpn_controller_disabled():
    from app import main

    with patch.object(main.vpn_servers_refresh_service, "refresh_now", new=AsyncMock()) as refresh_mock:
        asyncio.run(main._refresh_vpn_servers_before_vpn_provision(False))

    refresh_mock.assert_not_awaited()


def test_startup_refresh_uses_configured_source_first_and_stops_on_success():
    from app import main

    with patch(
        "app.services.settings_persistence.SettingsPersistence.get_cached_setting",
        return_value="proton_paid",
    ), patch.object(
        main.vpn_servers_refresh_service,
        "refresh_now",
        new=AsyncMock(return_value={"ok": True, "source": "proton_paid", "duration_s": 0.1}),
    ) as refresh_mock:
        asyncio.run(main._refresh_vpn_servers_before_vpn_provision(True))

    refresh_mock.assert_awaited_once()
    kwargs = refresh_mock.await_args.kwargs
    assert kwargs.get("reason") == "startup-preprovision"


def test_startup_refresh_falls_back_to_official_source_when_primary_fails():
    from app import main

    side_effects = [
        RuntimeError("primary source unavailable"),
        {"ok": True, "source": "gluetun_official", "duration_s": 0.2},
    ]

    with patch(
        "app.services.settings_persistence.SettingsPersistence.get_cached_setting",
        return_value="proton_paid",
    ), patch.object(
        main.vpn_servers_refresh_service,
        "refresh_now",
        new=AsyncMock(side_effect=side_effects),
    ) as refresh_mock:
        asyncio.run(main._refresh_vpn_servers_before_vpn_provision(True))

    assert refresh_mock.await_count == 2
    first_kwargs = refresh_mock.await_args_list[0].kwargs
    second_kwargs = refresh_mock.await_args_list[1].kwargs
    assert first_kwargs.get("reason") == "startup-preprovision"
    assert second_kwargs.get("reason") == "startup-fallback-gluetun"
    assert second_kwargs.get("overrides") == {"vpn_servers_refresh_source": "gluetun_official"}
