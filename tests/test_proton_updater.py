import json

import pytest

from app.services.proton_updater import ProtonServerUpdater


class FakeTwoFARequired(Exception):
    pass


class FakeAuthRequired(Exception):
    pass


def _build_fake_session(payload, *, require_2fa=False, accepted_code="123456"):
    class FakeSession:
        def __init__(self, appversion=None, user_agent=None):
            self.appversion = appversion
            self.user_agent = user_agent
            self.authenticated = False
            self.validated_2fa = False
            self.last_code = None
            self.logged_out = False

        async def async_authenticate(self, username, password):
            self.authenticated = bool(username and password)
            return self.authenticated

        async def async_api_request(self, endpoint):
            if require_2fa and not self.validated_2fa:
                raise FakeTwoFARequired("2FA required")
            assert endpoint.startswith("/vpn/v1/logicals")
            return payload

        async def async_validate_2fa_code(self, code):
            self.last_code = code
            self.validated_2fa = code == accepted_code
            return self.validated_2fa

        async def async_logout(self):
            self.logged_out = True

    return FakeSession


def _payload_with_wireguard_server():
    return {
        "LogicalServers": [
            {
                "Name": "ES#11",
                "Features": 4,
                "Tier": 2,
                "ExitCountry": "Spain",
                "City": "Madrid",
                "Servers": [
                    {
                        "Domain": "node-es-11.protonvpn.net",
                        "EntryIP": "198.51.100.10",
                        "EntryIPv6": "2001:db8::10",
                        "X25519PublicKey": "fakepubkey",
                    }
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_update_merges_proton_provider_into_servers_json(tmp_path, monkeypatch):
    existing_servers = {
        "version": 1,
        "mullvad": {"version": 1, "timestamp": 1, "servers": []},
    }
    (tmp_path / "servers.json").write_text(json.dumps(existing_servers), encoding="utf-8")

    fake_session = _build_fake_session(_payload_with_wireguard_server())
    monkeypatch.setattr(
        ProtonServerUpdater,
        "_import_proton_types",
        staticmethod(lambda: (fake_session, (FakeTwoFARequired, FakeAuthRequired))),
    )

    updater = ProtonServerUpdater(storage_path=str(tmp_path))
    result = await updater.update(
        proton_username="user",
        proton_password="pass",
        gluetun_json_mode="update",
    )

    merged = json.loads((tmp_path / "servers.json").read_text(encoding="utf-8"))
    proton = merged.get("protonvpn")

    assert result["stats"]["output_servers"] == 2
    assert result["totp_used"] is False
    assert isinstance(proton, dict)
    assert "mullvad" in merged
    assert proton["servers"][0]["hostname"] == "node-es-11.protonvpn.net"
    assert proton["servers"][0].get("port_forward") is True


@pytest.mark.asyncio
async def test_update_handles_two_factor_token(tmp_path, monkeypatch):
    fake_session = _build_fake_session(_payload_with_wireguard_server(), require_2fa=True, accepted_code="654321")
    monkeypatch.setattr(
        ProtonServerUpdater,
        "_import_proton_types",
        staticmethod(lambda: (fake_session, (FakeTwoFARequired, FakeAuthRequired))),
    )

    updater = ProtonServerUpdater(storage_path=str(tmp_path))
    result = await updater.update(
        proton_username="user",
        proton_password="pass",
        proton_totp_code="654321",
        gluetun_json_mode="none",
    )

    assert result["totp_used"] is True
    assert (tmp_path / "servers-proton.json").exists()
    assert not (tmp_path / "servers.json").exists()


@pytest.mark.asyncio
async def test_update_requires_token_when_two_factor_is_needed(tmp_path, monkeypatch):
    fake_session = _build_fake_session(_payload_with_wireguard_server(), require_2fa=True)
    monkeypatch.setattr(
        ProtonServerUpdater,
        "_import_proton_types",
        staticmethod(lambda: (fake_session, (FakeTwoFARequired, FakeAuthRequired))),
    )

    updater = ProtonServerUpdater(storage_path=str(tmp_path))

    with pytest.raises(RuntimeError, match="2FA is required"):
        await updater.update(
            proton_username="user",
            proton_password="pass",
            gluetun_json_mode="none",
        )
