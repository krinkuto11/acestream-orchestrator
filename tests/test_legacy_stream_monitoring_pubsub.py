from app.services.legacy_stream_monitoring import LegacyStreamMonitoringService


def test_legacy_monitoring_subscribe_and_unsubscribe():
    service = LegacyStreamMonitoringService()
    received = []

    unsubscribe = service.subscribe_updates(lambda payload: received.append(payload))
    service._broadcast_update({"change_type": "upsert", "monitor_id": "m-1"})

    assert len(received) == 1
    assert received[0].get("change_type") == "upsert"
    assert received[0].get("monitor_id") == "m-1"
    assert isinstance(received[0].get("seq"), int)

    unsubscribe()
    service._broadcast_update({"change_type": "deleted", "monitor_id": "m-1"})

    assert len(received) == 1
