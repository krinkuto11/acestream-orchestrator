from unittest.mock import Mock, patch

from app.services.health_manager import HealthManager


def test_wait_logic_returns_false_when_no_target_vpn():
    manager = HealthManager()

    with patch("app.services.health_manager.cfg.VPN_MODE", "redundant"), \
         patch("app.services.health_manager.cfg.GLUETUN_CONTAINER_NAME_2", "gluetun2"), \
         patch.object(manager, "_get_target_vpn_for_provisioning", return_value=None):
        assert manager._should_wait_for_vpn_recovery([]) is False


def test_wait_logic_targets_only_selected_vpn_monitor():
    manager = HealthManager()
    target_monitor = Mock()
    target_monitor.is_in_recovery_stabilization_period.return_value = True

    with patch("app.services.health_manager.cfg.VPN_MODE", "redundant"), \
         patch("app.services.health_manager.cfg.GLUETUN_CONTAINER_NAME_2", "gluetun2"), \
         patch.object(manager, "_get_target_vpn_for_provisioning", return_value="gluetun"), \
         patch("app.services.gluetun.gluetun_monitor.get_vpn_monitor", return_value=target_monitor):
        assert manager._should_wait_for_vpn_recovery([]) is True
