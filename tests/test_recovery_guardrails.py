from app.services import recovery


def test_source_reason_classification():
    recovery._reset_guardrails_for_tests()
    assert recovery._is_source_instability_reason("eof") is True
    assert recovery._is_source_instability_reason("read_timeout") is True
    assert recovery._is_source_instability_reason("chunked_encoding_error") is True
    assert recovery._is_source_instability_reason("engine_removed") is False


def test_pair_penalty_and_ping_pong_cooldown_tracking():
    recovery._reset_guardrails_for_tests()
    base_time = 1000.0
    stream_id = "s3"
    stream_key = "k3"

    recovery._record_source_failure(stream_id, stream_key, "eng-a", base_time)
    recovery._record_source_failure(stream_id, stream_key, "eng-b", base_time + 1)
    recovery._record_source_failure(stream_id, stream_key, "eng-a", base_time + 2)
    recovery._record_source_failure(stream_id, stream_key, "eng-b", base_time + 3)

    penalties = recovery._recent_pair_penalties(stream_key, base_time + 4)
    cooldown_remaining = recovery._get_cooldown_remaining(stream_id, base_time + 4)

    assert penalties.get("eng-a", 0) >= recovery.PAIR_PENALTY_SCORE
    assert penalties.get("eng-b", 0) >= recovery.PAIR_PENALTY_SCORE
    assert cooldown_remaining > 0
