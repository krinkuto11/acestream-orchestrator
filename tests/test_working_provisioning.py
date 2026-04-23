from types import SimpleNamespace
from unittest.mock import patch

from app.services.provisioner import AceProvisionRequest, EngineSpec, start_acestream


def test_start_acestream_returns_ports_from_scheduler_spec():
    engine_spec = EngineSpec(
        vpn_container=None,
        forwarded=False,
        p2p_port=None,
        host_http_port=31000,
        container_http_port=6878,
        container_https_port=6879,
        host_api_port=31001,
        container_api_port=62062,
        host_https_port=31002,
        labels={"test": "true"},
        ports={"6878/tcp": 31000},
        volumes={},
        network_config={},
    )

    container = SimpleNamespace(id="abc123", attrs={"Name": "/engine-1"})

    with patch("app.services.provisioner.get_variant_config", return_value={
        "image": "ghcr.io/krinkuto11/acestream:latest-amd64",
        "config_type": "cmd",
        "base_cmd": ["python", "main.py"],
        "is_custom": False,
    }), \
         patch("app.services.provisioner.get_client"), \
         patch("app.services.provisioner.safe", return_value=container):
        response = start_acestream(AceProvisionRequest(labels={}, env={}), engine_spec=engine_spec)

    assert response.container_id == "abc123"
    assert response.host_http_port == 31000
    assert response.host_api_port == 31001
