from app.services.provisioner import _parse_conf_port, compute_current_engine_config_hash


def test_parse_conf_port_extracts_http_and_https_ports():
    conf = "--http-port=6878\n--https-port=6879\n--bind-all"
    assert _parse_conf_port(conf, "http") == 6878
    assert _parse_conf_port(conf, "https") == 6879
    assert _parse_conf_port(conf, "api") is None


def test_compute_current_engine_config_hash_is_stable_for_same_config():
    first = compute_current_engine_config_hash()
    second = compute_current_engine_config_hash()
    assert first == second
