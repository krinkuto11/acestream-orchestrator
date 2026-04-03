from unittest.mock import patch

from app.services import gluetun


def test_single_vpn_status_skips_forwarded_port_until_control_ready():
    with patch("app.services.gluetun._state_node", return_value={"healthy": True}), \
         patch("app.services.gluetun._is_control_server_reachable_sync", return_value=False), \
         patch("app.services.gluetun.get_forwarded_port_sync") as forwarded_mock:
        status = gluetun._single_vpn_status("gluetun-dyn-test")

    assert status["connected"] is False
    assert status["forwarded_port"] is None
    forwarded_mock.assert_not_called()


def test_get_forwarded_port_sync_returns_none_when_control_not_ready_without_warning():
    with patch("app.services.gluetun._is_control_server_reachable_sync", return_value=False), \
         patch("app.services.gluetun.logger.warning") as warning_mock:
        port = gluetun.get_forwarded_port_sync("gluetun-dyn-test")

    assert port is None
    warning_mock.assert_not_called()
