from app.proxy.ace_api_client import AceLegacyApiClient, AceLegacyApiError


def test_normalize_session_id_numeric_only():
    assert AceLegacyApiClient._normalize_session_id(None) == "0"
    assert AceLegacyApiClient._normalize_session_id("0") == "0"
    assert AceLegacyApiClient._normalize_session_id("123") == "123"
    assert AceLegacyApiClient._normalize_session_id("test1") == "0"


def test_parse_status_line_dl_shape():
    line = "STATUS main:dl;0;0;992;0;96;3;0;14680064;0;1802240"
    parsed = AceLegacyApiClient.parse_status_line(line)

    assert parsed["status"] == "dl"
    assert parsed["speed_down"] == "992"
    assert parsed["peers"] == "3"
    assert parsed["http_peers"] == "0"
    assert parsed["downloaded"] == "14680064"


def test_parse_status_line_wait_shape_normalized():
    line = "STATUS main:wait;10;0;0;0;0;0;0;0;0;0;0"
    parsed = AceLegacyApiClient.parse_status_line(line)

    assert parsed["status"] == "wait"
    # wait removes one shape-specific field before mapping.
    assert parsed["total_progress"] == "0"


def test_parse_status_line_buf_shape_normalized():
    line = "STATUS main:buf;10;22;0;0;0;0;0;0;0;0;0;0"
    parsed = AceLegacyApiClient.parse_status_line(line)

    assert parsed["status"] == "buf"
    # buf removes two shape-specific fields before mapping.
    assert parsed["total_progress"] == "0"


def test_parse_event_line_livepos():
    line = (
        "EVENT livepos pos=1774205851 is_live=0 buffer_pieces=15 "
        "last=1774205877 live_first=1774204077 live_last=1774205877 "
        "first_ts=1774204077 last_ts=1774205877"
    )
    parsed = AceLegacyApiClient.parse_event_line(line)

    assert parsed["event"] == "livepos"
    assert parsed["pos"] == "1774205851"
    assert parsed["buffer_pieces"] == "15"


def test_preflight_deep_stops_stream(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)
    calls = {"start": 0, "stop": 0}

    def fake_resolve(content_id, session_id=None):
        return {"status": 1, "infohash": "abc123"}, "content_id"

    def fake_start(content_id, mode, stream_type="output_format=http"):
        calls["start"] += 1
        assert content_id == "abc123"
        assert mode == "infohash"
        return {"url": "http://127.0.0.1:6878/content/abc123/0.1"}

    def fake_collect(samples=4, interval_s=0.5, per_sample_timeout_s=2.0):
        return {
            "status_text": "dl",
            "peers": 1,
            "http_peers": 0,
            "sample_points": [
                {"status": "dl", "progress": 0, "downloaded": 100, "pos": 1000, "last_ts": 1000},
                {"status": "dl", "progress": 1, "downloaded": 120, "pos": 1001, "last_ts": 1001},
                {"status": "dl", "progress": 2, "downloaded": 145, "pos": 1002, "last_ts": 1002},
                {"status": "dl", "progress": 3, "downloaded": 170, "pos": 1003, "last_ts": 1003},
            ],
        }

    def fake_stop():
        calls["stop"] += 1

    monkeypatch.setattr(client, "resolve_content", fake_resolve)
    monkeypatch.setattr(client, "start_stream", fake_start)
    monkeypatch.setattr(client, "collect_status_samples", fake_collect)
    monkeypatch.setattr(client, "stop_stream", fake_stop)

    result = client.preflight("orig-hash", tier="deep")

    assert result["available"] is True
    assert result["infohash"] == "abc123"
    assert result["availability_checks"]["transport_signal"] is True
    assert result["availability_checks"]["progression_signal"] is True
    assert calls["start"] == 1
    assert calls["stop"] == 1


def test_preflight_deep_rejects_false_positive_without_progression(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)

    def fake_resolve(content_id, session_id=None):
        return {"status": 1, "infohash": "abc123"}, "content_id"

    def fake_start(content_id, mode, stream_type="output_format=http"):
        return {"url": "http://127.0.0.1:6878/content/abc123/0.1"}

    def fake_collect(samples=4, interval_s=0.5, per_sample_timeout_s=2.0):
        # Transport looks healthy but probe is fully static.
        return {
            "status_text": "dl",
            "peers": 1,
            "http_peers": 0,
            "sample_points": [
                {"status": "dl", "progress": 0, "downloaded": 7864320, "pos": 1774256958, "last_ts": 1774256958},
                {"status": "dl", "progress": 0, "downloaded": 7864320, "pos": 1774256958, "last_ts": 1774256958},
                {"status": "dl", "progress": 0, "downloaded": 7864320, "pos": 1774256958, "last_ts": 1774256958},
            ],
        }

    monkeypatch.setattr(client, "resolve_content", fake_resolve)
    monkeypatch.setattr(client, "start_stream", fake_start)
    monkeypatch.setattr(client, "collect_status_samples", fake_collect)
    monkeypatch.setattr(client, "stop_stream", lambda: None)

    result = client.preflight("orig-hash", tier="deep")

    assert result["available"] is False
    assert result["availability_checks"]["transport_signal"] is True
    assert result["availability_checks"]["progression_signal"] is False


def test_preflight_deep_accepts_moving_stream_with_fluctuating_downloaded(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)

    def fake_resolve(content_id, session_id=None):
        return {"status": 1, "infohash": "abc123"}, "content_id"

    def fake_start(content_id, mode, stream_type="output_format=http"):
        return {"url": "http://127.0.0.1:6878/content/abc123/0.1"}

    def fake_collect(samples=4, interval_s=0.5, per_sample_timeout_s=2.0):
        # Typical moving stream: live timeline advances while downloaded can oscillate.
        return {
            "status_text": "dl",
            "peers": 1,
            "http_peers": 0,
            "sample_points": [
                {"status": "dl", "progress": 0, "downloaded": 13107200, "pos": 1774259009, "last_ts": 1774259009},
                {"status": "dl", "progress": 0, "downloaded": 12845056, "pos": 1774259012, "last_ts": 1774259012},
                {"status": "dl", "progress": 0, "downloaded": 13369344, "pos": 1774259015, "last_ts": 1774259015},
                {"status": "dl", "progress": 0, "downloaded": 12582912, "pos": 1774259018, "last_ts": 1774259018},
            ],
        }

    monkeypatch.setattr(client, "resolve_content", fake_resolve)
    monkeypatch.setattr(client, "start_stream", fake_start)
    monkeypatch.setattr(client, "collect_status_samples", fake_collect)
    monkeypatch.setattr(client, "stop_stream", lambda: None)

    result = client.preflight("orig-hash", tier="deep")

    assert result["available"] is True
    assert result["availability_checks"]["transport_signal"] is True
    assert result["availability_checks"]["progression_signal"] is True


def test_preflight_deep_accepts_early_progression_with_tail_plateau(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)

    def fake_resolve(content_id, session_id=None):
        return {"status": 1, "infohash": "abc123"}, "content_id"

    def fake_start(content_id, mode, stream_type="output_format=http"):
        return {"url": "http://127.0.0.1:6878/content/abc123/0.1"}

    def fake_collect(samples=4, interval_s=0.5, per_sample_timeout_s=2.0):
        # Real-world transient case:
        # first sample has no livepos yet, then movement appears, then tail sample plateaus.
        return {
            "status_text": "dl",
            "peers": 1,
            "http_peers": 0,
            "sample_points": [
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": None, "last_ts": None},
                {"status": "dl", "progress": 0, "downloaded": 20955136, "pos": 1774263082, "last_ts": 1774263082},
                {"status": "dl", "progress": 0, "downloaded": 25165824, "pos": 1774263083, "last_ts": 1774263083},
                {"status": "dl", "progress": 0, "downloaded": 25165824, "pos": 1774263083, "last_ts": 1774263083},
            ],
        }

    monkeypatch.setattr(client, "resolve_content", fake_resolve)
    monkeypatch.setattr(client, "start_stream", fake_start)
    monkeypatch.setattr(client, "collect_status_samples", fake_collect)
    monkeypatch.setattr(client, "stop_stream", lambda: None)

    result = client.preflight("orig-hash", tier="deep")

    assert result["available"] is True
    assert result["availability_checks"]["transport_signal"] is True
    assert result["availability_checks"]["progression_signal"] is True


def test_preflight_deep_accepts_when_pos_plateaus_but_last_ts_moves(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)

    def fake_resolve(content_id, session_id=None):
        return {"status": 1, "infohash": "abc123"}, "content_id"

    def fake_start(content_id, mode, stream_type="output_format=http"):
        return {"url": "http://127.0.0.1:6878/content/abc123/0.1"}

    def fake_collect(samples=4, interval_s=0.5, per_sample_timeout_s=2.0):
        return {
            "status_text": "dl",
            "peers": 1,
            "http_peers": 0,
            "sample_points": [
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774265000, "last_ts": 1774265000},
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774265000, "last_ts": 1774265001},
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774265000, "last_ts": 1774265002},
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774265000, "last_ts": 1774265003},
            ],
        }

    monkeypatch.setattr(client, "resolve_content", fake_resolve)
    monkeypatch.setattr(client, "start_stream", fake_start)
    monkeypatch.setattr(client, "collect_status_samples", fake_collect)
    monkeypatch.setattr(client, "stop_stream", lambda: None)

    result = client.preflight("orig-hash", tier="deep")

    assert result["available"] is True
    assert result["availability_checks"]["transport_signal"] is True
    assert result["availability_checks"]["progression_signal"] is True


def test_preflight_deep_rejects_when_last_ts_static_even_if_pos_moves(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)

    def fake_resolve(content_id, session_id=None):
        return {"status": 1, "infohash": "abc123"}, "content_id"

    def fake_start(content_id, mode, stream_type="output_format=http"):
        return {"url": "http://127.0.0.1:6878/content/abc123/0.1"}

    def fake_collect(samples=4, interval_s=0.5, per_sample_timeout_s=2.0):
        return {
            "status_text": "dl",
            "peers": 1,
            "http_peers": 0,
            "sample_points": [
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774266000, "last_ts": 1774266000},
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774266001, "last_ts": 1774266000},
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774266002, "last_ts": 1774266000},
                {"status": "dl", "progress": 0, "downloaded": 11272192, "pos": 1774266003, "last_ts": 1774266000},
            ],
        }

    monkeypatch.setattr(client, "resolve_content", fake_resolve)
    monkeypatch.setattr(client, "start_stream", fake_start)
    monkeypatch.setattr(client, "collect_status_samples", fake_collect)
    monkeypatch.setattr(client, "stop_stream", lambda: None)

    result = client.preflight("orig-hash", tier="deep")

    assert result["available"] is False
    assert result["availability_checks"]["transport_signal"] is True
    assert result["availability_checks"]["progression_signal"] is False


def test_collect_status_samples_tolerates_timeouts(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)

    monkeypatch.setattr(client, "_write", lambda *_: None)
    monkeypatch.setattr(
        client,
        "_read_message",
        lambda timeout=0: (_ for _ in ()).throw(AceLegacyApiError("timeout")),
    )

    probe = client.collect_status_samples(samples=1, interval_s=0.0, per_sample_timeout_s=0.2)

    assert probe["status"] is None
    assert probe["livepos"] is None
    assert probe["raw_status_lines"] == []


def test_resolve_content_direct_url_bypasses_loadasync():
    client = AceLegacyApiClient("127.0.0.1", 62062)

    payload, mode = client.resolve_content("https://example.test/video.ts", mode="direct_url")

    assert mode == "direct_url"
    assert payload["status"] == 1
    assert payload["direct_url"] == "https://example.test/video.ts"


def test_resolve_content_torrent_url_uses_loadasync_torrent(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)

    called = {"value": False}

    def fake_loadasync_torrent(torrent_url, session_id):
        called["value"] = True
        assert torrent_url == "https://example.test/file.torrent"
        assert session_id == "0"
        return {"status": 1, "infohash": "abc123"}

    monkeypatch.setattr(client, "_loadasync_torrent", fake_loadasync_torrent)

    payload, mode = client.resolve_content("https://example.test/file.torrent", mode="torrent_url")

    assert called["value"] is True
    assert mode == "torrent_url"
    assert payload["status"] == 1


def test_start_stream_supports_torrent_direct_raw(monkeypatch):
    client = AceLegacyApiClient("127.0.0.1", 62062)
    commands = []

    monkeypatch.setattr(client, "_write", lambda msg: commands.append(msg))
    monkeypatch.setattr(client, "_wait_for", lambda *_args, **_kwargs: ("START", ["START", "url=http://x", "playback_session_id=s1"], {}))

    client.start_stream("https://example.test/file.torrent", mode="torrent_url")
    client.start_stream("magnet:?xt=urn:btih:abc", mode="direct_url")
    client.start_stream("ZmFrZS1yYXctcGF5bG9hZA==", mode="raw_data")

    assert commands[0].startswith("START TORRENT https://example.test/file.torrent")
    assert commands[1].startswith("START URL magnet:?xt=urn:btih:abc")
    assert commands[2].startswith("START RAW ZmFrZS1yYXctcGF5bG9hZA==")
