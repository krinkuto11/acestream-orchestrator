"""
Tests for the event logging system.
"""

import pytest
from datetime import datetime, timezone, timedelta
from app.services.event_logger import event_logger
from app.models.db_models import Base
from app.services.db import engine, get_session


@pytest.fixture(scope="function", autouse=True)
def setup_database():
    """Create tables before each test and clean up after."""
    Base.metadata.create_all(bind=engine)
    yield
    # Clean up events after test
    with get_session() as session:
        from app.models.db_models import EventRow
        session.query(EventRow).delete()
        session.commit()


def test_log_event():
    """Test logging a basic event."""
    event_id = event_logger.log_event(
        event_type="engine",
        category="created",
        message="Test engine created",
        details={"test": "data"},
        container_id="test123"
    )
    
    assert event_id > 0
    
    # Verify the event was stored
    events = event_logger.get_events(limit=1)
    assert len(events) == 1
    assert events[0].event_type == "engine"
    assert events[0].category == "created"
    assert events[0].message == "Test engine created"
    assert events[0].container_id == "test123"


def test_get_events_filtering():
    """Test filtering events by type and category."""
    # Log multiple events
    event_logger.log_event("engine", "created", "Engine 1")
    event_logger.log_event("stream", "started", "Stream 1")
    event_logger.log_event("engine", "deleted", "Engine 2")
    event_logger.log_event("vpn", "connected", "VPN 1")
    
    # Filter by event type
    engine_events = event_logger.get_events(event_type="engine")
    assert len(engine_events) == 2
    assert all(e.event_type == "engine" for e in engine_events)
    
    # Filter by category
    created_events = event_logger.get_events(category="created")
    assert len(created_events) == 1
    assert created_events[0].category == "created"


def test_get_events_pagination():
    """Test event pagination."""
    # Log 5 events
    for i in range(5):
        event_logger.log_event("system", "test", f"Event {i}")
    
    # Get first page
    page1 = event_logger.get_events(limit=2, offset=0)
    assert len(page1) == 2
    
    # Get second page
    page2 = event_logger.get_events(limit=2, offset=2)
    assert len(page2) == 2
    
    # Ensure different events
    assert page1[0].id != page2[0].id


def test_get_events_since():
    """Test filtering events by timestamp."""
    # Log an old event
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    
    # Log current events
    event_logger.log_event("engine", "created", "Recent event")
    
    # Filter by timestamp
    recent_events = event_logger.get_events(
        since=datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    assert len(recent_events) >= 1


def test_event_stats():
    """Test event statistics."""
    # Log various events
    event_logger.log_event("engine", "created", "Engine 1")
    event_logger.log_event("engine", "deleted", "Engine 2")
    event_logger.log_event("stream", "started", "Stream 1")
    event_logger.log_event("vpn", "connected", "VPN 1")
    
    stats = event_logger.get_event_stats()
    
    assert stats["total"] >= 4
    assert stats["by_type"]["engine"] >= 2
    assert stats["by_type"]["stream"] >= 1
    assert stats["by_type"]["vpn"] >= 1
    assert stats["oldest"] is not None
    assert stats["newest"] is not None


def test_event_count():
    """Test getting event counts."""
    # Log events
    event_logger.log_event("health", "warning", "Health warning 1")
    event_logger.log_event("health", "warning", "Health warning 2")
    event_logger.log_event("system", "scaling", "Scaling event")
    
    # Get counts
    total_count = event_logger.get_event_count()
    assert total_count >= 3
    
    health_count = event_logger.get_event_count(event_type="health")
    assert health_count >= 2
    
    warning_count = event_logger.get_event_count(category="warning")
    assert warning_count >= 2


def test_cleanup_old_events():
    """Test manual cleanup of old events."""
    # This test just ensures the cleanup doesn't error
    deleted = event_logger.cleanup_old_events(max_age_days=30)
    assert deleted >= 0


def test_event_types():
    """Test all event types are supported."""
    event_types = ["engine", "stream", "vpn", "health", "system"]
    
    for event_type in event_types:
        event_id = event_logger.log_event(
            event_type=event_type,
            category="test",
            message=f"Test {event_type} event"
        )
        assert event_id > 0
    
    # Verify all types were logged
    for event_type in event_types:
        events = event_logger.get_events(event_type=event_type, limit=1)
        assert len(events) >= 1
        assert events[0].event_type == event_type


def test_event_with_associations():
    """Test events with container_id and stream_id."""
    container_id = "test_container_123"
    stream_id = "test_stream_456"
    
    event_logger.log_event(
        event_type="stream",
        category="started",
        message="Stream started with associations",
        container_id=container_id,
        stream_id=stream_id
    )
    
    # Filter by container_id
    events_by_container = event_logger.get_events(container_id=container_id)
    assert len(events_by_container) >= 1
    assert events_by_container[0].container_id == container_id
    
    # Filter by stream_id
    events_by_stream = event_logger.get_events(stream_id=stream_id)
    assert len(events_by_stream) >= 1
    assert events_by_stream[0].stream_id == stream_id


def test_event_details():
    """Test structured event details."""
    details = {
        "deficit": 3,
        "total_running": 2,
        "target": 5,
        "nested": {"key": "value"}
    }
    
    event_logger.log_event(
        event_type="system",
        category="scaling",
        message="Auto-scaling test",
        details=details
    )
    
    events = event_logger.get_events(limit=1)
    assert len(events) == 1
    assert events[0].details == details
