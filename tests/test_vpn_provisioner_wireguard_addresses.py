from app.services.vpn_provisioner import VPNProvisioner
from unittest.mock import patch


def test_wireguard_addresses_prefers_ipv4_when_ipv6_first():
    env = {}
    credential = {
        "wireguard_private_key": "dummy-private-key",
        "wireguard_addresses": "2a07:b944::2:2/128,10.2.0.2/32",
    }

    VPNProvisioner._apply_credential_env(
        env=env,
        protocol="wireguard",
        credential=credential,
        allow_ipv6_wireguard=False,
    )

    assert env["WIREGUARD_PRIVATE_KEY"] == "dummy-private-key"
    assert env["WIREGUARD_ADDRESSES"] == "10.2.0.2/32"


def test_wireguard_addresses_handles_list_like_string_storage():
    env = {}
    credential = {
        "wireguard_private_key": "dummy-private-key",
        "wireguard_addresses": "['10.2.0.2/32', '2a07:b944::2:2/128']",
    }

    VPNProvisioner._apply_credential_env(
        env=env,
        protocol="wireguard",
        credential=credential,
        allow_ipv6_wireguard=False,
    )

    assert env["WIREGUARD_ADDRESSES"] == "10.2.0.2/32"


def test_wireguard_addresses_ipv6_only_raises_clear_error():
    env = {}
    credential = {
        "wireguard_private_key": "dummy-private-key",
        "wireguard_addresses": "2a07:b944::2:2/128",
    }

    try:
        VPNProvisioner._apply_credential_env(
            env=env,
            protocol="wireguard",
            credential=credential,
            allow_ipv6_wireguard=False,
        )
        assert False, "Expected ValueError for IPv6-only address without IPv6 support"
    except ValueError as exc:
        assert "IPv6-only" in str(exc)


def test_credential_port_forwarding_defaults_to_supported():
    assert VPNProvisioner.credential_supports_port_forwarding({}) is True


def test_apply_port_forwarding_env_disables_for_credential_opt_out():
    env = {}
    provisioner = VPNProvisioner()

    provisioner._apply_port_forwarding_env(
        env=env,
        provider="protonvpn",
        settings={"vpn_port_forwarding": True},
        credential={"id": "cred-1", "port_forwarding": False},
        port_forwarding_supported=True,
    )

    assert env["VPN_PORT_FORWARDING"] == "off"


def test_build_gluetun_env_clears_wireguard_endpoint_when_pf_enabled():
    provisioner = VPNProvisioner()
    with patch(
        "app.services.vpn_provisioner.vpn_reputation_manager.hostnames_support_port_forwarding",
        return_value=False,
    ):
        env = provisioner._build_gluetun_env(
            provider="protonvpn",
            protocol="wireguard",
            settings={"vpn_port_forwarding": True},
            credential={
                "wireguard_private_key": "dummy-private-key",
                "wireguard_endpoints": "node-es-05.protonvpn.net:51820",
            },
            regions=["spain"],
            port_forwarding_supported=True,
        )

    assert env["VPN_PORT_FORWARDING"] == "on"
    assert "WIREGUARD_ENDPOINTS" not in env


def test_build_gluetun_env_keeps_wireguard_endpoint_when_pf_enabled_and_compatible():
    provisioner = VPNProvisioner()
    with patch(
        "app.services.vpn_provisioner.vpn_reputation_manager.hostnames_support_port_forwarding",
        return_value=True,
    ):
        env = provisioner._build_gluetun_env(
            provider="protonvpn",
            protocol="wireguard",
            settings={"vpn_port_forwarding": True},
            credential={
                "wireguard_private_key": "dummy-private-key",
                "wireguard_endpoints": "af-03.protonvpn.net:51820",
            },
            regions=["afghanistan"],
            port_forwarding_supported=True,
        )

    assert env["VPN_PORT_FORWARDING"] == "on"
    assert env["WIREGUARD_ENDPOINTS"] == "af-03.protonvpn.net:51820"


def test_build_gluetun_env_keeps_wireguard_endpoint_when_pf_disabled():
    provisioner = VPNProvisioner()
    env = provisioner._build_gluetun_env(
        provider="protonvpn",
        protocol="wireguard",
        settings={"vpn_port_forwarding": False},
        credential={
            "wireguard_private_key": "dummy-private-key",
            "wireguard_endpoints": "node-es-05.protonvpn.net:51820",
        },
        regions=["spain"],
        port_forwarding_supported=True,
    )

    assert env["VPN_PORT_FORWARDING"] == "off"
    assert env["WIREGUARD_ENDPOINTS"] == "node-es-05.protonvpn.net:51820"
