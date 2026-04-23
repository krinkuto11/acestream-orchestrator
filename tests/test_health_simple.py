from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from app.services.health_manager import EngineHealthStatus, HealthManager


def test_engine_health_status_uses_threshold_and_grace_period():
    with patch("app.services.health_manager.cfg.HEALTH_FAILURE_THRESHOLD", 2), \
         patch("app.services.health_manager.cfg.HEALTH_UNHEALTHY_GRACE_PERIOD_S", 30):
        health = EngineHealthStatus("engine-1")
        health.consecutive_failures = 1
        assert health.is_considered_unhealthy() is False

        health.consecutive_failures = 2
        health.first_failure_time = datetime.now(timezone.utc)
        assert health.is_considered_unhealthy() is True
        assert health.should_be_replaced() is False

        health.first_failure_time = datetime.now(timezone.utc) - timedelta(seconds=31)
        assert health.should_be_replaced() is True


def test_health_summary_has_expected_shape():
    manager = HealthManager()

    with patch("app.services.health_manager.state.list_engines", return_value=[]), \
         patch("app.services.health_manager.cfg.MIN_REPLICAS", 3), \
         patch("app.services.health_manager.circuit_breaker_manager.get_status", return_value={"general": {"state": "closed"}}):
        summary = manager.get_health_summary()

    assert "total_engines" in summary
    assert "healthy_engines" in summary
    assert "unhealthy_engines" in summary
    assert "minimum_required" in summary
    assert summary["minimum_required"] == 3
