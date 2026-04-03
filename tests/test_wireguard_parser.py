from app.utils.wireguard_parser import parse_wireguard_conf


def test_parse_wireguard_conf_sets_port_forwarding_default_true():
    payload = """
[Interface]
PrivateKey = test-private-key
Address = 10.2.0.2/32

[Peer]
Endpoint = us.example.net:51820
""".strip()

    parsed = parse_wireguard_conf(payload)

    assert parsed["is_valid"] is True
    assert parsed["port_forwarding"] is True
