from app.proxy.ace_api_client import AceLegacyApiClient


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

    def fake_collect(samples=3, interval_s=0.5, per_sample_timeout_s=2.0):
        return {"status_text": "dl", "peers": 1, "http_peers": 0}

    def fake_stop():
        calls["stop"] += 1

    monkeypatch.setattr(client, "resolve_content", fake_resolve)
    monkeypatch.setattr(client, "start_stream", fake_start)
    monkeypatch.setattr(client, "collect_status_samples", fake_collect)
    monkeypatch.setattr(client, "stop_stream", fake_stop)

    result = client.preflight("orig-hash", tier="deep")

    assert result["available"] is True
    assert result["infohash"] == "abc123"
    assert calls["start"] == 1
    assert calls["stop"] == 1
