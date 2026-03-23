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

    def start_stream(self, content_id, mode, stream_type="output_format=http"):
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

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.5, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(1.2)

    current = await service.get_monitor(monitor_id)
    assert current is not None
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
