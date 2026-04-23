import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.services.gluetun import VpnContainerMonitor


def test_recovery_stabilization_period_detection():
    monitor = VpnContainerMonitor("gluetun")
    assert monitor.is_in_recovery_stabilization_period() is False

    monitor._last_recovery_time = datetime.now(timezone.utc)
    assert monitor.is_in_recovery_stabilization_period() is True

    monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=monitor._recovery_stabilization_period_s + 1)
    assert monitor.is_in_recovery_stabilization_period() is False


def test_check_port_change_skips_during_recovery_stabilization():
    monitor = VpnContainerMonitor("gluetun")
    monitor._last_health_status = True
    monitor._last_recovery_time = datetime.now(timezone.utc)

    result = asyncio.run(monitor.check_port_change())
    assert result is None


def test_check_port_change_detects_change_after_stabilization():
    monitor = VpnContainerMonitor("gluetun")
    monitor._last_health_status = True
    monitor._last_recovery_time = datetime.now(timezone.utc) - timedelta(seconds=monitor._recovery_stabilization_period_s + 1)
    monitor._last_stable_forwarded_port = 1000
    monitor._last_port_check_time = None

    monitor._fetch_and_cache_port = AsyncMock(return_value=2000)

    result = asyncio.run(monitor.check_port_change())
    assert result == (1000, 2000)
