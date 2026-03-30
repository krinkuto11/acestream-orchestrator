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


class _DummyStatusProbeClient:
    def __init__(self):
        self.calls = 0

    def collect_status_samples(self, samples=1, interval_s=0.0, per_sample_timeout_s=1.0):
        self.calls += 1
        return {
            "status_text": "dl",
            "peers": 3,
            "speed_down": 4096,
            "downloaded": 1234,
        }


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
async def test_record_client_activity_accumulates_transfer_stats(tmp_path):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-transfer-stats"
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

    service.record_client_activity(
        monitor_id,
        "client-1",
        "10.0.0.1",
        "UA/1.0",
        request_kind="manifest",
        bytes_sent=220,
        now=1000.0,
    )
    service.record_client_activity(
        monitor_id,
        "client-1",
        "10.0.0.1",
        "UA/1.0",
        request_kind="segment",
        bytes_sent=1200,
        chunks_sent=1,
        now=1001.0,
    )
    service.record_client_activity(
        monitor_id,
        "client-1",
        "10.0.0.1",
        "UA/1.0",
        request_kind="segment",
        bytes_sent=800,
        chunks_sent=1,
        now=1002.0,
    )

    clients = service.list_clients(monitor_id, max_idle_seconds=0)

    assert len(clients) == 1
    assert clients[0]["client_id"] == "client-1"
    assert clients[0]["requests_total"] == 3
    assert clients[0]["bytes_sent"] == 2220.0
    assert clients[0]["chunks_sent"] == 2
    assert clients[0]["stats_updated_at"] == 1002.0


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


@pytest.mark.asyncio
async def test_collect_legacy_stats_probe_uses_cache(tmp_path):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-probe-cache"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"
    status_client = _DummyStatusProbeClient()

    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://source",
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=None),
        started_at=time.time(),
        last_activity=time.time(),
        control_client=status_client,
    )

    first = service.collect_legacy_stats_probe(monitor_id, samples=1, per_sample_timeout_s=1.0)
    second = service.collect_legacy_stats_probe(monitor_id, samples=1, per_sample_timeout_s=1.0)
    forced = service.collect_legacy_stats_probe(monitor_id, samples=1, per_sample_timeout_s=1.0, force=True)

    assert first is not None
    assert second is not None
    assert forced is not None
    assert status_client.calls == 2


@pytest.mark.asyncio
async def test_start_segmenter_uses_low_latency_hls_defaults(tmp_path, monkeypatch):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-cmd-defaults"

    captured = {"cmd": []}

    async def fake_spawn(*cmd, **_kwargs):
        captured["cmd"] = list(cmd)
        return _DummyProcess(returncode=None)

    async def fake_wait(monitor_key: str, timeout_s: float = 15.0):
        session = service._sessions[monitor_key]
        session.manifest_path.write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(service, "_wait_for_manifest", fake_wait)

    await service.start_segmenter(monitor_id, "http://source")

    cmd = captured["cmd"]
    assert cmd
    assert "-hls_time" in cmd
    assert cmd[cmd.index("-hls_time") + 1] == "3.0"
    assert "-hls_list_size" in cmd
    assert cmd[cmd.index("-hls_list_size") + 1] == "5"
    assert "-hls_flags" in cmd
    assert "split_by_time" in cmd[cmd.index("-hls_flags") + 1]


@pytest.mark.asyncio
async def test_start_segmenter_respects_env_tuning(tmp_path, monkeypatch):
    monkeypatch.setenv("API_HLS_SEGMENT_TIME_S", "2.5")
    monkeypatch.setenv("API_HLS_LIST_SIZE", "7")
    monkeypatch.setenv("API_HLS_SPLIT_BY_TIME", "0")

    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-cmd-env"

    captured = {"cmd": []}

    async def fake_spawn(*cmd, **_kwargs):
        captured["cmd"] = list(cmd)
        return _DummyProcess(returncode=None)

    async def fake_wait(monitor_key: str, timeout_s: float = 15.0):
        session = service._sessions[monitor_key]
        session.manifest_path.write_text("#EXTM3U\n", encoding="utf-8")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(service, "_wait_for_manifest", fake_wait)

    await service.start_segmenter(monitor_id, "http://source")

    cmd = captured["cmd"]
    assert cmd
    assert "-hls_time" in cmd
    assert cmd[cmd.index("-hls_time") + 1] == "2.5"
    assert "-hls_list_size" in cmd
    assert cmd[cmd.index("-hls_list_size") + 1] == "7"
    assert "-hls_flags" in cmd
    assert "split_by_time" not in cmd[cmd.index("-hls_flags") + 1]


@pytest.mark.asyncio
async def test_api_hls_client_ttl_defaults_to_fast_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_CLIENT_TTL", "60")
    monkeypatch.delenv("API_HLS_CLIENT_TTL", raising=False)

    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-client-ttl-default"
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

    assert service._client_record_ttl_s == 20

    now = time.time()
    service.record_client_activity(monitor_id, "old-client", "10.0.0.1", "UA/1.0", now=now - 25.0)
    service.record_client_activity(monitor_id, "new-client", "10.0.0.2", "UA/2.0", now=now - 5.0)

    clients = service.list_clients(monitor_id)

    assert len(clients) == 1
    assert clients[0]["client_id"] == "new-client"


@pytest.mark.asyncio
async def test_api_hls_client_ttl_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_CLIENT_TTL", "60")
    monkeypatch.setenv("API_HLS_CLIENT_TTL", "12")

    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-client-ttl-override"
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

    assert service._client_record_ttl_s == 12

    now = time.time()
    service.record_client_activity(monitor_id, "old-client", "10.0.0.1", "UA/1.0", now=now - 15.0)
    service.record_client_activity(monitor_id, "new-client", "10.0.0.2", "UA/2.0", now=now - 3.0)

    clients = service.list_clients(monitor_id)

    assert len(clients) == 1
    assert clients[0]["client_id"] == "new-client"


@pytest.mark.asyncio
async def test_record_client_activity_emits_connect_metric_for_new_client(tmp_path, monkeypatch):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-metrics-connect"
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

    connect_calls = {"count": 0}

    def _fake_connect(mode: str):
        assert mode == "HLS"
        connect_calls["count"] += 1

    monkeypatch.setattr("app.services.metrics.observe_proxy_client_connect", _fake_connect)

    service.record_client_activity(monitor_id, "client-1", "10.0.0.1", "UA/1.0", now=1000.0)
    service.record_client_activity(monitor_id, "client-1", "10.0.0.1", "UA/1.0", now=1001.0)

    assert connect_calls["count"] == 1


@pytest.mark.asyncio
async def test_count_active_clients_emits_disconnect_metrics_for_stale_clients(tmp_path, monkeypatch):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-metrics-disconnect"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"

    session = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://source",
        output_dir=out_dir,
        manifest_path=manifest_path,
        process=_DummyProcess(returncode=None),
        started_at=time.time(),
        last_activity=time.time(),
    )
    now = time.time()
    session.clients = {
        "stale": {
            "client_id": "stale",
            "ip_address": "10.0.0.1",
            "last_active": now - 30.0,
        },
        "active": {
            "client_id": "active",
            "ip_address": "10.0.0.2",
            "last_active": now - 2.0,
        },
    }
    service._sessions[monitor_id] = session

    disconnect_calls = {"count": 0}

    def _fake_disconnect(mode: str):
        assert mode == "HLS"
        disconnect_calls["count"] += 1

    monkeypatch.setattr("app.services.metrics.observe_proxy_client_disconnect", _fake_disconnect)

    total = service.count_active_clients(max_idle_seconds=10)

    assert total == 1
    assert disconnect_calls["count"] == 1
