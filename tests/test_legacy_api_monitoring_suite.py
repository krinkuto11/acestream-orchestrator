import pytest

legacy_suite = pytest.importorskip(
    "legacy_api_monitoring_suite.run_suite",
    reason="legacy_api_monitoring_suite is an optional external package",
)

LegacyStreamMonitor = legacy_suite.LegacyStreamMonitor
SuiteConfig = legacy_suite.SuiteConfig
parse_hashes_file = legacy_suite.parse_hashes_file


def _cfg():
    return SuiteConfig(
        engine_host="localhost",
        engine_port=62062,
        compose_file="compose.text.yml",
        ensure_compose_up=False,
        hashes_file="hashes.txt",
        content_ids=[],
        probe_tier="deep",
        sample_interval=1.0,
        summary_interval=30.0,
        run_seconds=1,
        connect_timeout=5.0,
        response_timeout=5.0,
        output_dir="legacy_api_monitoring_suite/output-test",
    )


def test_parse_hashes_file_detects_good_and_dead_sections(tmp_path):
    hashes_file = tmp_path / "hashes.txt"
    hashes_file.write_text(
        """
good hashes:
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

supposedly dead hashes:
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""".strip(),
        encoding="utf-8",
    )

    good, dead = parse_hashes_file(hashes_file)
    assert good == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert dead == ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]


def test_build_parsed_status_entry_includes_parsed_fields():
    monitor = LegacyStreamMonitor(_cfg())
    probe_payload = {
        "content_id": "s1",
        "duration_s": 1.2,
        "available": True,
        "status_code": 1,
        "message": None,
        "can_retry": True,
        "should_wait": False,
        "error": None,
        "status_probe": {
            "status_text": "dl",
            "immediate_progress": 10,
            "total_progress": 11,
            "progress": 10,
            "speed_down": 1200,
            "http_speed_down": 0,
            "speed_up": 0,
            "peers": 5,
            "http_peers": 0,
            "downloaded": 100,
            "http_downloaded": 0,
            "uploaded": 0,
            "livepos": {"pos": "1000"},
            "raw_status_lines": ["STATUS main:dl;0;10;1200;0;0;5;0;100;0;0"],
            "raw_event_lines": ["EVENT livepos pos=1000"],
        },
    }

    entry = monitor._build_parsed_status_entry("2026-01-01T00:00:00+00:00", probe_payload)
    assert entry["content_id"] == "s1"
    assert entry["status_text"] == "dl"
    assert entry["speed_down"] == 1200
    assert entry["raw_status_lines"]


def test_setup_uses_explicit_content_ids_over_hashes_file(tmp_path):
    hashes_file = tmp_path / "hashes.txt"
    hashes_file.write_text("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", encoding="utf-8")

    cfg = _cfg()
    cfg.hashes_file = str(hashes_file)
    cfg.content_ids = ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]

    monitor = LegacyStreamMonitor(cfg)
    monitor.setup()
    assert monitor.targets == ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
