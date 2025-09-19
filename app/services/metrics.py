from prometheus_client import Counter, Gauge, make_asgi_app

orch_events_started = Counter("orch_events_started_total", "stream_started events")
orch_events_ended = Counter("orch_events_ended_total", "stream_ended events")
orch_collect_errors = Counter("orch_collector_errors_total", "collector errors")
orch_streams_active = Gauge("orch_streams_active", "active streams")
orch_provision_total = Counter("orch_provision_total", "provision requests", ["kind"])

metrics_app = make_asgi_app()
