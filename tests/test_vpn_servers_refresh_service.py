import json

import pytest

from app.services.vpn_servers_refresh import VPNServersRefreshService


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        return _FakeResponse(self._payload)


@pytest.mark.asyncio
async def test_refresh_now_supports_official_gluetun_source(tmp_path, monkeypatch):
    payload = {
        "version": 1,
        "protonvpn": {"version": 4, "timestamp": 1, "servers": []},
        "mullvad": {"version": 1, "timestamp": 1, "servers": []},
    }

    monkeypatch.setattr(
        "app.services.settings_persistence.SettingsPersistence.load_vpn_config",
        lambda: {
            "vpn_servers_refresh_source": "gluetun_official",
            "vpn_servers_gluetun_json_mode": "replace",
            "vpn_servers_storage_path": str(tmp_path),
            "vpn_servers_official_url": "https://example.invalid/servers.json",
        },
    )
    monkeypatch.setattr(
        "app.services.vpn_servers_refresh.httpx.AsyncClient",
        lambda timeout=60.0: _FakeAsyncClient(payload),
    )

    service = VPNServersRefreshService()
    result = await service.refresh_now(reason="test")

    assert result["ok"] is True
    assert result["source"] == "gluetun_official"
    assert (tmp_path / "servers-official.json").exists()

    merged = json.loads((tmp_path / "servers.json").read_text(encoding="utf-8"))
    assert "protonvpn" in merged
    assert "mullvad" in merged


@pytest.mark.asyncio
async def test_refresh_now_proton_env_credentials_use_configured_env_names(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.settings_persistence.SettingsPersistence.load_vpn_config",
        lambda: {
            "vpn_servers_refresh_source": "proton_paid",
            "vpn_servers_gluetun_json_mode": "none",
            "vpn_servers_storage_path": str(tmp_path),
            "vpn_servers_proton_credentials_source": "env",
            "vpn_servers_proton_username_env": "MY_PROTON_USER",
            "vpn_servers_proton_password_env": "MY_PROTON_PASS",
            "vpn_servers_proton_totp_code_env": "MY_PROTON_TOTP",
            "vpn_servers_proton_totp_secret_env": "MY_PROTON_TOTP_SECRET",
            "vpn_servers_filter_ipv6": "exclude",
            "vpn_servers_filter_secure_core": "include",
            "vpn_servers_filter_tor": "include",
            "vpn_servers_filter_free_tier": "include",
        },
    )

    monkeypatch.setenv("MY_PROTON_USER", "env-user")
    monkeypatch.setenv("MY_PROTON_PASS", "env-pass")
    monkeypatch.setenv("MY_PROTON_TOTP", "123456")

    captured = {}

    async def _fake_update(self, **kwargs):
        captured.update(kwargs)
        return {
            "storage_path": str(tmp_path),
            "servers_proton_file": str(tmp_path / "servers-proton.json"),
            "servers_file": str(tmp_path / "servers.json"),
            "gluetun_json_mode": "none",
            "stats": {"output_servers": 0},
            "totp_used": True,
        }

    monkeypatch.setattr("app.services.vpn_servers_refresh.ProtonServerUpdater.update", _fake_update)

    service = VPNServersRefreshService()
    result = await service.refresh_now(reason="test")

    assert result["ok"] is True
    assert result["source"] == "proton_paid"
    assert result["credentials_source"] == "env"
    assert captured["proton_username"] == "env-user"
    assert captured["proton_password"] == "env-pass"
    assert captured["proton_totp_code"] == "123456"
