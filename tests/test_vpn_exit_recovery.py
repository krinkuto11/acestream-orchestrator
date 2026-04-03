from unittest.mock import patch

from app.services import gluetun


def test_get_vpn_status_returns_disabled_shape_when_vpn_not_configured():
    with patch("app.services.gluetun.cfg.GLUETUN_CONTAINER_NAME", None):
        status = gluetun.get_vpn_status()

    assert status["enabled"] is False
    assert status["mode"] == "disabled"
    assert status["connected"] is False


def test_get_vpn_status_reports_single_mode_when_primary_vpn_is_ready():
    with patch("app.services.gluetun.cfg.GLUETUN_CONTAINER_NAME", "gluetun"), \
         patch("app.services.gluetun._single_vpn_status", return_value={
             "enabled": True,
             "connected": True,
             "status": "running",
             "container_name": "gluetun",
             "container": "gluetun",
             "health": "healthy",
             "forwarded_port": 12345,
         }):
        status = gluetun.get_vpn_status()

    assert status["mode"] == "single"
    assert status["enabled"] is True
    assert status["connected"] is True
