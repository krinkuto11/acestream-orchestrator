import m3u8
import pytest

from app.proxy.hls_proxy import HLSProxyServer, StreamBuffer, StreamFetcher, StreamManager


def _build_manager() -> StreamManager:
    return StreamManager(
        playback_url="http://engine-a:6878/ace/manifest.m3u8",
        channel_id="test-channel",
        engine_host="engine-a",
        engine_port=6878,
        engine_container_id="engine-a",
        session_info={
            "playback_session_id": "session-1",
            "stat_url": "http://engine-a:6878/stat",
            "command_url": "http://engine-a:6878/command",
            "is_live": 1,
        },
    )


def test_build_manifest_inserts_discontinuity_on_engine_change():
    proxy = HLSProxyServer()
    manager = _build_manager()
    buffer = StreamBuffer()
    channel_id = "test-channel"

    for seq in range(4):
        buffer[seq] = f"seg-{seq}".encode("utf-8")
        manager.segment_durations[seq] = 4.0

    manager.segment_sources[0] = "engine-a"
    manager.segment_sources[1] = "engine-a"
    manager.segment_sources[2] = "engine-b"
    manager.segment_sources[3] = "engine-b"

    manifest = proxy._build_manifest(channel_id, manager, buffer)
    lines = manifest.splitlines()

    assert lines.count("#EXT-X-DISCONTINUITY") == 1

    seq2_uri = f"/ace/hls/{channel_id}/segment/2.ts"
    seq2_index = lines.index(seq2_uri)
    assert lines[seq2_index - 2] == "#EXT-X-DISCONTINUITY"


def test_build_manifest_without_engine_change_has_no_discontinuity():
    proxy = HLSProxyServer()
    manager = _build_manager()
    buffer = StreamBuffer()

    for seq in range(3):
        buffer[seq] = f"seg-{seq}".encode("utf-8")
        manager.segment_durations[seq] = 4.0
        manager.segment_sources[seq] = "engine-a"

    manifest = proxy._build_manifest("test-channel", manager, buffer)
    assert "#EXT-X-DISCONTINUITY" not in manifest


@pytest.mark.asyncio
async def test_fetcher_records_source_engine_per_segment():
    manager = _build_manager()
    buffer = StreamBuffer()
    fetcher = StreamFetcher(manager, buffer)

    async def fake_download(_url: str):
        return b"segment-bytes"

    fetcher._download_segment = fake_download  # type: ignore[method-assign]

    initial_manifest = m3u8.loads(
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:4\n"
        "#EXTINF:4.0,\n"
        "seg1.ts\n"
        "#EXTINF:4.0,\n"
        "seg2.ts\n"
    )

    await fetcher._fetch_initial_segments(
        initial_manifest,
        base_url="http://engine-a:6878/ace/manifest.m3u8",
        source_engine_id="engine-a",
    )

    assert manager.segment_sources[0] == "engine-a"
    assert manager.segment_sources[1] == "engine-a"

    latest_manifest = m3u8.loads(
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:4\n"
        "#EXTINF:4.0,\n"
        "seg3.ts\n"
    )

    await fetcher._fetch_latest_segment(
        latest_manifest,
        base_url="http://engine-b:6878/ace/manifest.m3u8",
        source_engine_id="engine-b",
    )

    assert manager.segment_sources[2] == "engine-b"
