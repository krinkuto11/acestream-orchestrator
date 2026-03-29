from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.legacy_stream_monitoring import LegacyStreamMonitoringService


class _FakeAceLegacyApiClient:
    def __init__(self, host, port, connect_timeout=10.0, response_timeout=10.0, product_key=None):
        self.host = host
        self.port = int(port)
        self._sample_idx = 0

    def connect(self):
        return None

    def authenticate(self):
        return None

    def resolve_content(self, content_id, session_id=None):
        return ({"status": 1, "infohash": content_id}, "infohash")

    def start_stream(self, content_id, mode, stream_type="output_format=http", file_indexes="0"):
        return {
            "playback_session_id": "test-session",
            "url": "http://127.0.0.1:6878/content/test/0.0",
        }

    def collect_status_samples(self, samples=1, interval_s=0.0, per_sample_timeout_s=1.0):
        self._sample_idx += 1
        return {
            "status_text": "dl",
            "status": "dl",
            "progress": self._sample_idx,
            "speed_down": 1000,
            "speed_up": 10,
            "peers": 2,
            "downloaded": self._sample_idx * 1000,
            "uploaded": self._sample_idx * 10,
            "livepos": {
                "pos": str(100 + self._sample_idx),
                "last_ts": str(1000 + self._sample_idx),
            },
        }

    def stop_stream(self):
        return None

    def shutdown(self):
        return None


class _FakeStaticLiveposClient(_FakeAceLegacyApiClient):
    def collect_status_samples(self, samples=1, interval_s=0.0, per_sample_timeout_s=1.0):
        self._sample_idx += 1
        return {
            "status_text": "dl",
            "status": "dl",
            "progress": self._sample_idx,
            "speed_down": 1000,
            "speed_up": 10,
            "peers": 2,
            "downloaded": 5000,
            "uploaded": self._sample_idx * 10,
            "livepos": {
                "pos": "100",
                "last_ts": "1000",
            },
        }


class _FakeTimeoutClient(_FakeAceLegacyApiClient):
    def collect_status_samples(self, samples=1, interval_s=0.0, per_sample_timeout_s=1.0):
        raise asyncio.TimeoutError("api timed out")


class _FakeFailoverClient(_FakeAceLegacyApiClient):
    def collect_status_samples(self, samples=1, interval_s=0.0, per_sample_timeout_s=1.0):
        if int(self.port) == 62062:
            raise asyncio.TimeoutError("api timed out on primary engine")
        return super().collect_status_samples(samples=samples, interval_s=interval_s, per_sample_timeout_s=per_sample_timeout_s)


@pytest.mark.asyncio
async def test_monitor_collects_status_samples(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeAceLegacyApiClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(
        content_id="abc123",
        stream_name="Test Channel",
        interval_s=0.5,
        run_seconds=0,
    )
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(1.2)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert current["stream_name"] == "Test Channel"
    assert current["sample_count"] >= 1
    assert current["latest_status"]["status_text"] == "dl"
    assert current["livepos_movement"]["is_moving"] is True
    assert current["livepos_movement"]["direction"] == "forward"
    assert current["livepos_movement"]["pos_delta"] is not None

    stopped = await service.stop_monitor(monitor_id)
    assert stopped is True

    final = await service.get_monitor(monitor_id)
    assert final is not None
    assert final["status"] == "stopped"


@pytest.mark.asyncio
async def test_monitor_rejects_missing_requested_engine(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])

    service = LegacyStreamMonitoringService()

    with pytest.raises(RuntimeError, match="not found"):
        await service.start_monitor(
            content_id="abc123",
            engine_container_id="engine-does-not-exist",
        )


@pytest.mark.asyncio
async def test_monitor_start_is_idempotent_for_same_monitor_id(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeAceLegacyApiClient)

    service = LegacyStreamMonitoringService()

    first = await service.start_monitor(
        monitor_id="fixed-monitor-id",
        content_id="abc123",
        interval_s=0.5,
        run_seconds=0,
    )

    second = await service.start_monitor(
        monitor_id="fixed-monitor-id",
        content_id="different-content-id",
        interval_s=0.5,
        run_seconds=0,
    )

    assert first["monitor_id"] == "fixed-monitor-id"
    assert second["monitor_id"] == "fixed-monitor-id"
    assert second["content_id"] == "abc123"

    monitors = await service.list_monitors()
    assert len(monitors) == 1

    await service.stop_all()


@pytest.mark.asyncio
async def test_monitor_start_deduplicates_active_session_for_same_content_id(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeAceLegacyApiClient)

    service = LegacyStreamMonitoringService()

    first = await service.start_monitor(
        content_id="abc123",
        interval_s=0.5,
        run_seconds=0,
    )

    second = await service.start_monitor(
        content_id="abc123",
        interval_s=0.5,
        run_seconds=0,
        monitor_id="another-monitor-id",
    )

    assert first["monitor_id"] == second["monitor_id"]

    monitors = await service.list_monitors()
    assert len(monitors) == 1

    await service.stop_all()


@pytest.mark.asyncio
async def test_monitor_auto_stops_after_runtime_limit(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeAceLegacyApiClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.5, run_seconds=1)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(1.8)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert current["status"] == "stopped"
    assert current["sample_count"] >= 1


@pytest.mark.asyncio
async def test_monitor_delete_removes_entry(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeAceLegacyApiClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.5, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(0.6)

    deleted = await service.delete_monitor(monitor_id)
    assert deleted is True

    current = await service.get_monitor(monitor_id)
    assert current is None


def test_stuck_detection_requires_about_20s_without_movement():
    service = LegacyStreamMonitoringService()

    sample = {
        "livepos": {"pos": "100", "last_ts": "1000"},
        "downloaded": 5000,
    }

    raw_just_under_threshold = {
        "interval_s": 1.0,
        "recent_status": [dict(sample) for _ in range(20)],
    }
    assert service._is_session_stuck(raw_just_under_threshold) is False

    raw_at_threshold = {
        "interval_s": 1.0,
        "recent_status": [dict(sample) for _ in range(21)],
    }
    assert service._is_session_stuck(raw_at_threshold) is True


@pytest.mark.asyncio
async def test_stuck_stream_is_not_marked_dead(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeStaticLiveposClient)

    service = LegacyStreamMonitoringService()
    service._stuck_no_movement_seconds = 1.0

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.5, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(2.1)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert current["status"] == "stuck"
    assert current["dead_reason"] is None
    assert current["sample_count"] >= 3

    stopped = await service.stop_monitor(monitor_id)
    assert stopped is True


@pytest.mark.asyncio
async def test_api_timeout_marks_stream_dead_and_stops(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeTimeoutClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.5, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(0.8)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert current["status"] == "dead"
    assert current["dead_reason"] == "timeout_or_connect_error"


@pytest.mark.asyncio
async def test_dead_monitor_retries_once_on_different_engine(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine_1 = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=True,
    )
    engine_2 = SimpleNamespace(
        container_id="engine-2",
        host="127.0.0.1",
        port=6879,
        api_port=62063,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine_1, engine_2])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeFailoverClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.5, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(1.6)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert current["status"] in {"running", "stuck"}
    assert current["reconnect_attempts"] == 1
    assert (current.get("engine") or {}).get("container_id") == "engine-2"

    await service.stop_all()


@pytest.mark.asyncio
async def test_monitor_balancing_spreads_bulk_sessions_across_engines(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    engine_1 = SimpleNamespace(
        container_id="engine-1",
        host="127.0.0.1",
        port=6878,
        api_port=62062,
        forwarded=True,
    )
    engine_2 = SimpleNamespace(
        container_id="engine-2",
        host="127.0.0.1",
        port=6879,
        api_port=62063,
        forwarded=False,
    )

    monkeypatch.setattr(module.state, "list_engines", lambda: [engine_1, engine_2])
    monkeypatch.setattr(module.state, "list_streams", lambda status=None: [])
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeAceLegacyApiClient)

    service = LegacyStreamMonitoringService()

    first = await service.start_monitor(content_id="aaa", interval_s=1.0, run_seconds=0)
    second = await service.start_monitor(content_id="bbb", interval_s=1.0, run_seconds=0)

    first_engine = (first.get("engine") or {}).get("container_id")
    second_engine = (second.get("engine") or {}).get("container_id")

    assert first_engine is not None
    assert second_engine is not None
    assert first_engine != second_engine

    await service.stop_all()
