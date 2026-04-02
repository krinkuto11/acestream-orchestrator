from unittest.mock import patch

import pytest

from app.services.provisioner import ResourceScheduler


def test_scheduler_filters_notready_vpn_nodes():
    scheduler = ResourceScheduler()

    with patch("app.services.provisioner.cfg.GLUETUN_CONTAINER_NAME", "gluetun"), \
         patch("app.services.provisioner.cfg.GLUETUN_CONTAINER_NAME_2", "gluetun2"), \
         patch("app.services.provisioner.cfg.VPN_MODE", "redundant"), \
         patch("app.services.state.state.list_vpn_nodes", return_value=[
             {"container_name": "gluetun", "healthy": False},
             {"container_name": "gluetun2", "healthy": True},
         ]), \
         patch("app.services.state.state.get_engines_by_vpn", side_effect=lambda vpn: []):
        target = scheduler._select_vpn_container_locked()

    assert target == "gluetun2"


def test_scheduler_raises_when_all_vpn_nodes_notready():
    scheduler = ResourceScheduler()

    with patch("app.services.provisioner.cfg.GLUETUN_CONTAINER_NAME", "gluetun"), \
         patch("app.services.provisioner.cfg.GLUETUN_CONTAINER_NAME_2", "gluetun2"), \
         patch("app.services.provisioner.cfg.VPN_MODE", "redundant"), \
         patch("app.services.state.state.list_vpn_nodes", return_value=[
             {"container_name": "gluetun", "healthy": False},
             {"container_name": "gluetun2", "healthy": False},
         ]), \
         patch("app.services.state.state.get_engines_by_vpn", side_effect=lambda vpn: []):
        with pytest.raises(RuntimeError, match="Both VPN containers are unhealthy"):
            scheduler._select_vpn_container_locked()
