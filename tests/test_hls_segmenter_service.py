import asyncio
import time

import pytest

from app.services.hls_segmenter import HLSSegmenterService, SegmenterSession


class _DummyProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stderr = None


@pytest.mark.asyncio
async def test_start_segmenter_reuses_warming_session_without_restart(tmp_path, monkeypatch):
    service = HLSSegmenterService(base_dir=str(tmp_path))
    monitor_id = "stream-1"
    out_dir = tmp_path / monitor_id
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "index.m3u8"

    service._sessions[monitor_id] = SegmenterSession(
        monitor_id=monitor_id,
        source_mpegts_url="http://old-source",
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

    async def fake_stop(_key: str):
        stop_calls["count"] += 1
        return True

    async def fail_spawn(*_args, **_kwargs):
        raise AssertionError("start_segmenter should not spawn ffmpeg when a session is already warming")

    monkeypatch.setattr(service, "_wait_for_manifest", fake_wait)
    monkeypatch.setattr(service, "_stop_locked", fake_stop)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_spawn)

    path = await service.start_segmenter(monitor_id, "http://new-source")

    assert path == manifest_path
    assert wait_calls["count"] == 1
    assert stop_calls["count"] == 0
    assert service._sessions[monitor_id].source_mpegts_url == "http://old-source"


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
