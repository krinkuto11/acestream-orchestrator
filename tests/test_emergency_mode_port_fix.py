import asyncio
from unittest.mock import Mock, patch

from app.services.gluetun import GluetunMonitor


def test_handle_vpn_failure_requests_reconcile_and_resets_port_tracking():
    monitor = GluetunMonitor()
    mock_vpn_monitor = Mock()
    monitor._vpn_monitors = {"gluetun": mock_vpn_monitor}

    with patch("app.services.autoscaler.engine_controller.request_reconcile") as request_reconcile:
        asyncio.run(monitor._handle_vpn_failure("gluetun"))

    mock_vpn_monitor.reset_port_tracking.assert_called_once()
    request_reconcile.assert_called_once_with(reason="vpn_failure:gluetun")


def test_handle_vpn_recovery_requests_reconcile():
    monitor = GluetunMonitor()

    with patch("app.services.autoscaler.engine_controller.request_reconcile") as request_reconcile:
        asyncio.run(monitor._handle_vpn_recovery("gluetun"))

    request_reconcile.assert_called_once_with(reason="vpn_recovery:gluetun")
