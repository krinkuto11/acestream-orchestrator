import asyncio
import time

import pytest

from app.services.hls_segmenter import HLSSegmenterService, SegmenterSession


class _DummyProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stderr = None


class _DummyControlClient:
    def __init__(self):
        self.stop_called = 0
        self.shutdown_called = 0

    def stop_stream(self):
        self.stop_called += 1

    def shutdown(self):
        self.shutdown_called += 1


@pytest.mark.asyncio
async def test_start_segmenter_reuses_warming_session_without_restart(tmp_path, monkeypatch):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-1"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"

    existing_source = "http://old-source"
    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url=existing_source,
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=None),
        started_at=time.time(),
        last_activity=time.time(),
    )

    wait_calls = {"count": 0}
    stop_calls = {"count": 0}

    async def fake_wait(monitor_key: str, timeout_s: float = 15.0):
        wait_calls["count"] += 1
        session = service._sessions[monitor_key]
        session.manifest_path.write_text("#EXTM3U\n", encoding="utf-8")

    async def fake_stop(_key: str, emit_stream_ended: bool = True):
        stop_calls["count"] += 1
        return True

    async def fail_spawn(*_args, **_kwargs):
        raise AssertionError("start_segmenter should not spawn ffmpeg when a session is already warming")

    monkeypatch.setattr(service, "_wait_for_manifest", fake_wait)
    monkeypatch.setattr(service, "_stop_locked", fake_stop)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_spawn)

    path = await service.start_segmenter(monitor_id, existing_source)

    assert path == manifest_path
    assert wait_calls["count"] == 1
    assert stop_calls["count"] == 0
    assert service._sessions[monitor_id].source_mpegts_url == existing_source


@pytest.mark.asyncio
async def test_get_or_wait_manifest_returns_none_when_missing(tmp_path):
    service = HLSSegmenterService(base_dir=str(tmp_path))

    result = await service.get_or_wait_manifest("missing-stream", timeout_s=0.1)

    assert result is None


@pytest.mark.asyncio
async def test_get_or_wait_manifest_waits_for_running_session(tmp_path, monkeypatch):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-2"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"

    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://source",
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=None),
        started_at=time.time(),
        last_activity=time.time(),
    )

    async def fake_wait(monitor_key: str, timeout_s: float = 15.0):
        session = service._sessions[monitor_key]
        session.manifest_path.write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(service, "_wait_for_manifest", fake_wait)

    result = await service.get_or_wait_manifest(monitor_id, timeout_s=0.1)

    assert result == manifest_path
    assert manifest_path.exists()


@pytest.mark.asyncio
async def test_record_and_list_clients_for_segmenter_session(tmp_path):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-3"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"

    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://source",
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=None),
        started_at=time.time(),
        last_activity=time.time(),
    )

    service.record_client_activity(monitor_id, "client-1", "10.0.0.1", "UA/1.0", now=1000.0)
    service.record_client_activity(monitor_id, "client-2", "10.0.0.2", "UA/2.0", now=1010.0)

    clients = service.list_clients(monitor_id, max_idle_seconds=0)

    assert len(clients) == 2
    assert clients[0]["client_id"] == "client-2"
    assert clients[1]["client_id"] == "client-1"


@pytest.mark.asyncio
async def test_list_clients_prunes_stale_entries(tmp_path):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-stale"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"

    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://source",
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=None),
        started_at=time.time(),
        last_activity=time.time(),
    )

    now = time.time()
    service.record_client_activity(monitor_id, "old-client", "10.0.0.1", "UA/1.0", now=now - 120.0)
    service.record_client_activity(monitor_id, "new-client", "10.0.0.2", "UA/2.0", now=now - 10.0)

    clients = service.list_clients(monitor_id, max_idle_seconds=60)

    assert len(clients) == 1
    assert clients[0]["client_id"] == "new-client"


@pytest.mark.asyncio
async def test_record_client_activity_tracks_request_count(tmp_path):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-requests"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"

    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://source",
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=None),
        started_at=time.time(),
        last_activity=time.time(),
    )

    service.record_client_activity(monitor_id, "client-1", "10.0.0.1", "UA/1.0", request_kind="manifest", now=1000.0)
    service.record_client_activity(monitor_id, "client-1", "10.0.0.1", "UA/1.0", request_kind="segment", now=1001.0)

    clients = service.list_clients(monitor_id, max_idle_seconds=0)

    assert len(clients) == 1
    assert clients[0]["client_id"] == "client-1"
    assert clients[0]["requests_total"] == 2
    assert clients[0]["last_request_kind"] == "segment"


@pytest.mark.asyncio
async def test_stop_segmenter_closes_control_client_and_emits_stream_end(tmp_path, monkeypatch):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-4"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"
    control_client = _DummyControlClient()

    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://source",
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=0),
        started_at=time.time(),
        last_activity=time.time(),
        stream_id="stream-id-123",
        container_id="container-123",
        control_client=control_client,
    )

    captured = {"count": 0, "stream_id": ""}

    def fake_handle_stream_ended(event):
        captured["count"] += 1
        captured["stream_id"] = event.stream_id
        return None

    monkeypatch.setattr("app.services.internal_events.handle_stream_ended", fake_handle_stream_ended)

    stopped = await service.stop_segmenter(monitor_id, emit_stream_ended=True)

    assert stopped is True
    assert control_client.stop_called == 1
    assert control_client.shutdown_called == 1
    assert captured["count"] == 1
    assert captured["stream_id"] == "stream-id-123"
