from unittest.mock import patch

from app.services.provisioner import (
    AceProvisionRequest,
    ResourceScheduler,
    compute_current_engine_config_hash,
)
from app.services.state import state


def test_compute_current_engine_config_hash_changes_when_variant_changes():
    with patch("app.services.provisioner.cfg.ENGINE_VARIANT", "AceServe-amd64"):
        hash_a = compute_current_engine_config_hash()

    with patch("app.services.provisioner.cfg.ENGINE_VARIANT", "AceServe-arm64"):
        hash_b = compute_current_engine_config_hash()

    assert hash_a != hash_b


def test_scheduler_labels_include_config_hash_and_generation():
    state.set_target_engine_config("hash-123")
    scheduler = ResourceScheduler()

    with patch("app.services.provisioner.cfg.GLUETUN_CONTAINER_NAME", None), \
         patch("app.services.provisioner.cfg.CONTAINER_LABEL", "orchestrator.managed=true"), \
         patch("app.services.provisioner.cfg.ACE_MAP_HTTPS", True), \
         patch("app.services.provisioner.alloc.allocate_engine_ports", return_value={
             "host_http_port": 30001,
             "container_http_port": 6878,
             "container_https_port": 6879,
             "host_api_port": 30002,
             "container_api_port": 62062,
             "host_https_port": 30003,
         }), \
         patch.object(ResourceScheduler, "_elect_forwarded_engine_locked", return_value=(False, None)):
        spec = scheduler.schedule(AceProvisionRequest(labels={}, env={}), engine_variant_name="AceServe-amd64")

    assert spec.labels["acestream.config_hash"] == "hash-123"
    assert spec.labels["acestream.config_generation"] == str(state.get_target_engine_config()["generation"])
