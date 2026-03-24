import pytest

from app.services.legacy_stream_monitoring import LegacyStreamMonitoringService


@pytest.mark.asyncio
async def test_list_monitors_omits_recent_status_when_requested():
    service = LegacyStreamMonitoringService()
    monitor_id = "monitor-1"

    service._sessions[monitor_id] = {
        "content_id": "abc",
        "stream_name": "Test",
        "status": "running",
        "interval_s": 1.0,
        "run_seconds": 0,
        "started_at": "2026-01-01T00:00:00Z",
        "last_collected_at": "2026-01-01T00:00:10Z",
        "ended_at": None,
        "sample_count": 2,
        "last_error": None,
        "dead_reason": None,
        "reconnect_attempts": 0,
        "engine": {"container_id": "eng-1"},
        "session": {"playback_url": "http://example"},
        "latest_status": {"status": "dl"},
        "recent_status": [
            {"status": "dl", "livepos": {"last_ts": 1000}},
            {"status": "dl", "livepos": {"last_ts": 1001}},
        ],
    }

    full_items = await service.list_monitors(include_recent_status=True)
    slim_items = await service.list_monitors(include_recent_status=False)

    assert len(full_items) == 1
    assert len(slim_items) == 1

    assert "recent_status" in full_items[0]
    assert "recent_status" not in slim_items[0]
    assert slim_items[0]["latest_status"] == {"status": "dl"}
    assert "livepos_movement" in slim_items[0]


@pytest.mark.asyncio
async def test_get_monitor_omits_recent_status_when_requested():
    service = LegacyStreamMonitoringService()
    monitor_id = "monitor-2"

    service._sessions[monitor_id] = {
        "content_id": "def",
        "stream_name": "Single",
        "status": "running",
        "interval_s": 1.0,
        "run_seconds": 0,
        "started_at": "2026-01-01T00:00:00Z",
        "last_collected_at": "2026-01-01T00:00:10Z",
        "ended_at": None,
        "sample_count": 2,
        "last_error": None,
        "dead_reason": None,
        "reconnect_attempts": 0,
        "engine": {"container_id": "eng-2"},
        "session": {"playback_url": "http://example"},
        "latest_status": {"status": "dl"},
        "recent_status": [
            {"status": "dl", "livepos": {"last_ts": 2000}},
            {"status": "dl", "livepos": {"last_ts": 2001}},
        ],
    }

    full_item = await service.get_monitor(monitor_id, include_recent_status=True)
    slim_item = await service.get_monitor(monitor_id, include_recent_status=False)

    assert full_item is not None
    assert slim_item is not None
    assert "recent_status" in full_item
    assert "recent_status" not in slim_item
    assert slim_item["latest_status"] == {"status": "dl"}
    assert "livepos_movement" in slim_item
