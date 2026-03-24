from app.services.legacy_stream_monitoring import LegacyStreamMonitoringService


def test_stuck_when_livepos_missing_for_full_window():
    service = LegacyStreamMonitoringService()

    raw = {
        "interval_s": 1.0,
        "recent_status": [
            {"livepos": None, "downloaded": 100},
            {"livepos": None, "downloaded": 120},
            {"livepos": None, "downloaded": 140},
            {"livepos": None, "downloaded": 160},
            {"livepos": None, "downloaded": 180},
            {"livepos": None, "downloaded": 200},
            {"livepos": None, "downloaded": 220},
            {"livepos": None, "downloaded": 240},
            {"livepos": None, "downloaded": 260},
            {"livepos": None, "downloaded": 280},
            {"livepos": None, "downloaded": 300},
            {"livepos": None, "downloaded": 320},
            {"livepos": None, "downloaded": 340},
            {"livepos": None, "downloaded": 360},
            {"livepos": None, "downloaded": 380},
            {"livepos": None, "downloaded": 400},
            {"livepos": None, "downloaded": 420},
            {"livepos": None, "downloaded": 440},
            {"livepos": None, "downloaded": 460},
            {"livepos": None, "downloaded": 480},
            {"livepos": None, "downloaded": 500},
        ],
    }

    assert service._is_session_stuck(raw) is True


def test_not_stuck_with_insufficient_window_even_if_livepos_missing():
    service = LegacyStreamMonitoringService()

    raw = {
        "interval_s": 1.0,
        "recent_status": [
            {"livepos": None, "downloaded": 100},
            {"livepos": None, "downloaded": 110},
            {"livepos": None, "downloaded": 120},
        ],
    }

    assert service._is_session_stuck(raw) is False


def test_stuck_when_last_ts_static_and_no_payload_growth():
    service = LegacyStreamMonitoringService()

    points = []
    for _ in range(21):
        points.append({
            "livepos": {"pos": 1774266000, "last_ts": 1774266000},
            "downloaded": 1000,
        })

    raw = {"interval_s": 1.0, "recent_status": points}

    assert service._is_session_stuck(raw) is True
