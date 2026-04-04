from app.services.event_logger import EventLogger


def test_event_logger_subscribe_and_unsubscribe():
    logger = EventLogger()
    received = []

    unsubscribe = logger.subscribe(lambda payload: received.append(payload))
    logger._broadcast_event({"event_type": "system", "message": "hello"})

    assert len(received) == 1
    assert received[0].get("event_type") == "system"
    assert received[0].get("message") == "hello"
    assert isinstance(received[0].get("seq"), int)

    unsubscribe()
    logger._broadcast_event({"event_type": "system", "message": "world"})

    assert len(received) == 1
