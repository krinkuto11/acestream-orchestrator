from unittest.mock import patch

from app.services.provisioner import AceProvisionRequest, ResourceScheduler, _vpn_pending_engines


def test_scheduler_balances_assignments_with_pending_counts():
    scheduler = ResourceScheduler()
    _vpn_pending_engines.clear()

    port_counter = {"http": 32000, "api": 32100, "https": 32200}

    def _alloc(*args, **kwargs):
        port_counter["http"] += 1
        port_counter["api"] += 1
        port_counter["https"] += 1
        return {
            "host_http_port": port_counter["http"],
            "container_http_port": 6878,
            "container_https_port": 6879,
            "host_api_port": port_counter["api"],
            "container_api_port": 62062,
            "host_https_port": port_counter["https"],
        }

    with patch("app.services.provisioner.cfg.GLUETUN_CONTAINER_NAME", "gluetun"), \
         patch("app.services.provisioner.cfg.GLUETUN_CONTAINER_NAME_2", "gluetun2"), \
         patch("app.services.provisioner.cfg.VPN_MODE", "redundant"), \
         patch("app.services.provisioner.cfg.CONTAINER_LABEL", "orchestrator.managed=true"), \
         patch("app.services.provisioner.cfg.ACE_MAP_HTTPS", True), \
         patch("app.services.gluetun.gluetun_monitor.is_healthy", return_value=True), \
         patch("app.services.state.state.list_vpn_nodes", return_value=[
             {"container_name": "gluetun", "healthy": True},
             {"container_name": "gluetun2", "healthy": True},
         ]), \
         patch("app.services.state.state.get_engines_by_vpn", side_effect=lambda vpn: []), \
         patch("app.services.provisioner.alloc.allocate_engine_ports", side_effect=_alloc), \
         patch.object(ResourceScheduler, "_elect_forwarded_engine_locked", return_value=(False, None)):
        assignments = []
        for _ in range(8):
            spec = scheduler.schedule(AceProvisionRequest(labels={}, env={}), engine_variant_name="AceServe-amd64")
            assignments.append(spec.vpn_container)

    vpn1 = sum(1 for a in assignments if a == "gluetun")
    vpn2 = sum(1 for a in assignments if a == "gluetun2")

    assert abs(vpn1 - vpn2) <= 1
