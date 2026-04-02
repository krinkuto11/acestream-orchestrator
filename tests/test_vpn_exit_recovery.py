from unittest.mock import patch

from app.services import gluetun


def test_get_vpn_status_keeps_compatibility_emergency_field_when_disabled():
    with patch("app.services.gluetun.cfg.GLUETUN_CONTAINER_NAME", None):
        status = gluetun.get_vpn_status()

    assert status["enabled"] is False
    assert "emergency_mode" in status
    assert status["emergency_mode"]["active"] is False


def test_get_vpn_status_keeps_compatibility_emergency_field_single_mode():
    with patch("app.services.gluetun.cfg.GLUETUN_CONTAINER_NAME", "gluetun"), \
         patch("app.services.gluetun.cfg.VPN_MODE", "single"), \
         patch("app.services.gluetun._get_single_vpn_status", return_value={
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
    assert "emergency_mode" in status
    assert status["emergency_mode"]["active"] is False
