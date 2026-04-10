import json
from unittest.mock import patch

from app.services.vpn_reputation import VPNReputationManager


def test_hostnames_support_port_forwarding_uses_servers_catalog(tmp_path):
    catalog_path = tmp_path / "servers.json"
    catalog_path.write_text(
        json.dumps(
            {
                "version": 1,
                "protonvpn": {
                    "version": 4,
                    "timestamp": 1,
                    "servers": [
                        {
                            "vpn": "wireguard",
                            "country": "Afghanistan",
                            "city": "Kabul",
                            "hostname": "af-03.protonvpn.net",
                            "port_forward": True,
                        },
                        {
                            "vpn": "wireguard",
                            "country": "Spain",
                            "city": "Madrid",
                            "hostname": "node-es-05.protonvpn.net",
                            "secure_core": True,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    with patch.object(VPNReputationManager, "_servers_json_path", return_value=catalog_path):
        manager = VPNReputationManager()

        assert manager.hostnames_support_port_forwarding(
            provider="protonvpn",
            protocol="wireguard",
            hostnames=["af-03.protonvpn.net"],
            require_port_forwarding=True,
        ) is True

        assert manager.hostnames_support_port_forwarding(
            provider="protonvpn",
            protocol="wireguard",
            hostnames=["node-es-05.protonvpn.net"],
            require_port_forwarding=True,
        ) is False


def test_get_safe_hostname_prefers_servers_catalog_for_forwarding_filters(tmp_path):
    catalog_path = tmp_path / "servers.json"
    catalog_path.write_text(
        json.dumps(
            {
                "version": 1,
                "protonvpn": {
                    "version": 4,
                    "timestamp": 1,
                    "servers": [
                        {
                            "vpn": "wireguard",
                            "country": "Spain",
                            "city": "Madrid",
                            "hostname": "node-es-05.protonvpn.net",
                            "secure_core": True,
                        },
                        {
                            "vpn": "wireguard",
                            "country": "Spain",
                            "city": "Madrid",
                            "hostname": "node-es-11.protonvpn.net",
                            "port_forward": True,
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    with patch.object(VPNReputationManager, "_servers_json_path", return_value=catalog_path):
        manager = VPNReputationManager()
        selected = manager.get_safe_hostname(
            provider="protonvpn",
            regions=["spain"],
            protocol="wireguard",
            require_port_forwarding=True,
        )

    assert selected == "node-es-11.protonvpn.net"
