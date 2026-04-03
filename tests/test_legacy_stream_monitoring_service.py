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

    def start_stream(self, content_id, mode, stream_type="output_format=http", file_indexes="0", live_delay=0):
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

    def consume_download_stopped_event(self):
        return None

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


class _FakeRelayStickyFailoverClient(_FakeAceLegacyApiClient):
    _calls_by_port = {}

    def collect_status_samples(self, samples=1, interval_s=0.0, per_sample_timeout_s=1.0):
        port = int(self.port)
        call_count = int(self._calls_by_port.get(port, 0)) + 1
        self._calls_by_port[port] = call_count

        if port == 62062 and call_count >= 2:
            raise asyncio.TimeoutError("api timed out on primary engine")

        return super().collect_status_samples(samples=samples, interval_s=interval_s, per_sample_timeout_s=per_sample_timeout_s)


class _FakeDownloadStoppedClient(_FakeAceLegacyApiClient):
    def __init__(self, host, port, connect_timeout=10.0, response_timeout=10.0, product_key=None):
        super().__init__(host, port, connect_timeout=connect_timeout, response_timeout=response_timeout, product_key=product_key)
        self._download_stopped_sent = False

    def consume_download_stopped_event(self):
        if self._download_stopped_sent:
            return None
        self._download_stopped_sent = True
        return {
            "event": "download_stopped",
            "reason": "No seeds available",
        }


class _FakeDownloadStoppedFailoverClient(_FakeAceLegacyApiClient):
    def __init__(self, host, port, connect_timeout=10.0, response_timeout=10.0, product_key=None):
        super().__init__(host, port, connect_timeout=connect_timeout, response_timeout=response_timeout, product_key=product_key)
        self._emit_download_stopped = int(self.port) == 62062

    def consume_download_stopped_event(self):
        if not self._emit_download_stopped:
            return None
        self._emit_download_stopped = False
        return {
            "event": "download_stopped",
            "reason": "Primary engine stopped download",
        }


@pytest.fixture(autouse=True)
def _stub_engine_selection(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    def _select_best_engine_stub(
        requested_container_id=None,
        additional_load_by_engine=None,
        reserve_pending=False,
        not_found_error="engine_not_found",
    ):
        del reserve_pending

        engines = [
            e for e in module.state.list_engines()
            if not module.state.is_engine_draining(e.container_id)
        ]
        if not engines:
            raise RuntimeError("No non-draining engines available")

        if requested_container_id:
            selected = next((e for e in engines if e.container_id == requested_container_id), None)
            if not selected:
                raise RuntimeError(not_found_error)
            return selected, 0

        loads = {}
        for engine in engines:
            base = 0
            if additional_load_by_engine:
                base = int(additional_load_by_engine.get(engine.container_id, 0) or 0)
            loads[engine.container_id] = max(0, base)

        selected = sorted(
            engines,
            key=lambda e: (loads.get(e.container_id, 0), not e.forwarded),
        )[0]

        return selected, int(loads.get(selected.container_id, 0) or 0)

    monkeypatch.setattr(module, "select_best_engine", _select_best_engine_stub)
    monkeypatch.setattr(module.state, "get_active_monitor_load_by_engine", lambda: {})
    monkeypatch.setattr(module.state, "is_engine_draining", lambda _cid: False)


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
async def test_download_stopped_marks_stream_dead_immediately(monkeypatch):
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
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeDownloadStoppedClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=2.0, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(0.7)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert current["status"] == "dead"
    assert current["dead_reason"] == "download_stopped"
    assert current["last_error"] == "No seeds available"


@pytest.mark.asyncio
async def test_download_stopped_triggers_immediate_failover(monkeypatch):
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
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeDownloadStoppedFailoverClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=2.0, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(1.2)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert current["status"] in {"running", "stuck", "starting", "reconnecting"}
    assert current["reconnect_attempts"] == 1
    assert (current.get("engine") or {}).get("container_id") == "engine-2"

    await service.stop_all()


@pytest.mark.asyncio
async def test_relay_url_is_persistent_across_failover(monkeypatch):
    from app.services import legacy_stream_monitoring as module

    _FakeRelayStickyFailoverClient._calls_by_port = {}

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
    monkeypatch.setattr(module.state, "is_engine_draining", lambda _cid: False)
    monkeypatch.setattr(module, "AceLegacyApiClient", _FakeRelayStickyFailoverClient)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.2, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(0.45)
    before = await service.get_monitor(monitor_id)
    assert before is not None
    relay_before = str((before.get("session") or {}).get("playback_url") or "")
    assert relay_before.startswith("http://127.0.0.2:") or relay_before.startswith("http://127.0.0.1:")

    await asyncio.sleep(1.1)
    after = await service.get_monitor(monitor_id)
    assert after is not None
    assert (after.get("engine") or {}).get("container_id") == "engine-2"
    assert int(after.get("reconnect_attempts") or 0) >= 1
    relay_after = str((after.get("session") or {}).get("playback_url") or "")
    assert relay_after == relay_before

    await service.stop_all()


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
async def test_failover_retry_not_limited_to_single_attempt(monkeypatch):
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
    monkeypatch.setattr(module.state, "get_active_monitor_load_by_engine", lambda: {})

    service = LegacyStreamMonitoringService()
    service._sessions = {
        "monitor-1": {
            "status": "running",
            "reconnect_attempts": 5,
            "engine": {
                "container_id": "engine-1",
                "host": "127.0.0.1",
                "port": 6878,
                "api_port": 62062,
            },
            "session": {},
            "latest_status": {},
            "recent_status": [],
        }
    }

    did_failover = await service._failover_retry_dead_monitor("monitor-1", "timeout", "simulated")

    assert did_failover is True
    current = service._sessions["monitor-1"]
    assert current["reconnect_attempts"] == 6
    assert current["status"] == "reconnecting"
    assert (current.get("engine") or {}).get("container_id") == "engine-2"


@pytest.mark.asyncio
async def test_monitor_preemptively_fails_over_on_draining_engine(monkeypatch):
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

    draining_state = {"enabled": False}

    def _is_engine_draining(cid: str) -> bool:
        return bool(draining_state["enabled"] and cid == "engine-1")

    monkeypatch.setattr(module.state, "is_engine_draining", _is_engine_draining)

    service = LegacyStreamMonitoringService()

    monitor = await service.start_monitor(content_id="abc123", interval_s=0.25, run_seconds=0)
    monitor_id = monitor["monitor_id"]

    await asyncio.sleep(0.45)
    draining_state["enabled"] = True
    await asyncio.sleep(0.8)

    current = await service.get_monitor(monitor_id)
    assert current is not None
    assert int(current.get("reconnect_attempts") or 0) >= 1
    assert (current.get("engine") or {}).get("container_id") == "engine-2"
    assert current.get("status") in {"running", "stuck", "starting", "reconnecting"}

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


@pytest.mark.asyncio
async def test_reusable_session_skips_starting_monitor_state():
    service = LegacyStreamMonitoringService()

    service._sessions = {
        "monitor-starting": {
            "content_id": "abc123",
            "status": "starting",
            "last_collected_at": "2026-03-30T10:00:00+00:00",
            "engine": {"container_id": "engine-1", "host": "127.0.0.1", "port": 6878, "api_port": 62062},
            "session": {"playback_url": "http://127.0.0.1:6878/content/starting"},
            "latest_status": {},
        },
        "monitor-running": {
            "content_id": "abc123",
            "status": "running",
            "last_collected_at": "2026-03-30T10:00:01+00:00",
            "engine": {"container_id": "engine-2", "host": "127.0.0.1", "port": 6879, "api_port": 62063},
            "session": {"playback_url": "http://127.0.0.1:6879/content/running"},
            "latest_status": {},
        },
    }

    reusable = await service.get_reusable_session_for_content("abc123")

    assert reusable is not None
    assert reusable["monitor_id"] == "monitor-running"
