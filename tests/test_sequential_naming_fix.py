"""
Test for sequential engine naming fix.

This test verifies that engine names use the lowest available number
rather than always incrementing, preventing acestream-11 with only 10 active engines.
"""

import pytest
from app.services.naming import generate_container_name
from app.services.db import SessionLocal, engine
from app.models.db_models import EngineRow, Base


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all tables before running tests."""
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def clean_database():
    """Fixture to clean the database before and after tests."""
    with SessionLocal() as session:
        # Clean up any existing test engines
        session.query(EngineRow).filter(
            EngineRow.container_name.like('test-prefix-%')
        ).delete()
        session.commit()
    
    yield
    
    with SessionLocal() as session:
        # Clean up after test
        session.query(EngineRow).filter(
            EngineRow.container_name.like('test-prefix-%')
        ).delete()
        session.commit()


def test_sequential_naming_with_gaps(clean_database):
    """Test that naming fills gaps instead of always incrementing."""
    with SessionLocal() as session:
        # Create engines with gaps: 1, 2, 4, 5 (missing 3)
        for num in [1, 2, 4, 5]:
            engine = EngineRow(
                engine_key=f"test-key-{num}",
                container_id=f"test-id-{num}",
                container_name=f"test-prefix-{num}",
                host="test-host",
                port=8000 + num,
                labels={},
                forwarded=False,
                first_seen=None,
                last_seen=None
            )
            session.add(engine)
        session.commit()
    
    # Next name should be test-prefix-3 (fills the gap)
    name = generate_container_name("test-prefix")
    assert name == "test-prefix-3", f"Expected 'test-prefix-3' but got '{name}'"


def test_sequential_naming_no_gaps(clean_database):
    """Test that naming works correctly when there are no gaps."""
    with SessionLocal() as session:
        # Create engines: 1, 2, 3
        for num in [1, 2, 3]:
            engine = EngineRow(
                engine_key=f"test-key-{num}",
                container_id=f"test-id-{num}",
                container_name=f"test-prefix-{num}",
                host="test-host",
                port=8000 + num,
                labels={},
                forwarded=False,
                first_seen=None,
                last_seen=None
            )
            session.add(engine)
        session.commit()
    
    # Next name should be test-prefix-4 (no gaps, next in sequence)
    name = generate_container_name("test-prefix")
    assert name == "test-prefix-4", f"Expected 'test-prefix-4' but got '{name}'"


def test_sequential_naming_empty(clean_database):
    """Test that naming starts at 1 when no engines exist."""
    # No engines in database
    name = generate_container_name("test-prefix")
    assert name == "test-prefix-1", f"Expected 'test-prefix-1' but got '{name}'"


def test_sequential_naming_multiple_gaps(clean_database):
    """Test that naming fills the first gap when multiple gaps exist."""
    with SessionLocal() as session:
        # Create engines with multiple gaps: 1, 3, 5, 7 (missing 2, 4, 6)
        for num in [1, 3, 5, 7]:
            engine = EngineRow(
                engine_key=f"test-key-{num}",
                container_id=f"test-id-{num}",
                container_name=f"test-prefix-{num}",
                host="test-host",
                port=8000 + num,
                labels={},
                forwarded=False,
                first_seen=None,
                last_seen=None
            )
            session.add(engine)
        session.commit()
    
    # Next name should be test-prefix-2 (first gap)
    name = generate_container_name("test-prefix")
    assert name == "test-prefix-2", f"Expected 'test-prefix-2' but got '{name}'"


def test_sequential_naming_after_deletion(clean_database):
    """Test that naming reuses numbers after engines are deleted."""
    with SessionLocal() as session:
        # Create engines 1-5
        for num in range(1, 6):
            engine = EngineRow(
                engine_key=f"test-key-{num}",
                container_id=f"test-id-{num}",
                container_name=f"test-prefix-{num}",
                host="test-host",
                port=8000 + num,
                labels={},
                forwarded=False,
                first_seen=None,
                last_seen=None
            )
            session.add(engine)
        session.commit()
        
        # Delete engine 3
        session.query(EngineRow).filter(
            EngineRow.container_name == "test-prefix-3"
        ).delete()
        session.commit()
    
    # Next name should be test-prefix-3 (reuses deleted number)
    name = generate_container_name("test-prefix")
    assert name == "test-prefix-3", f"Expected 'test-prefix-3' but got '{name}'"


def test_naming_range_stays_within_active_count(clean_database):
    """
    Test the key requirement: with N active engines, names should be in range [1, N+1].
    
    This prevents acestream-11 appearing when there are only 10 active engines.
    """
    with SessionLocal() as session:
        # Simulate scenario from problem statement:
        # Had 10 engines (1-10), some failed and were removed, now have 8 active
        active_numbers = [1, 2, 4, 5, 6, 8, 9, 10]  # 8 active engines
        
        for num in active_numbers:
            engine = EngineRow(
                engine_key=f"test-key-{num}",
                container_id=f"test-id-{num}",
                container_name=f"test-prefix-{num}",
                host="test-host",
                port=8000 + num,
                labels={},
                forwarded=False,
                first_seen=None,
                last_seen=None
            )
            session.add(engine)
        session.commit()
    
    # With 8 active engines, the next name should fill a gap (3 or 7)
    # not jump to 11 or higher
    name = generate_container_name("test-prefix")
    name_num = int(name.split("-")[-1])
    
    # The new engine number should be ≤ 9 (8 active + 1)
    assert name_num <= 9, f"With 8 active engines, new engine should be ≤9, but got {name_num}"
    assert name in ["test-prefix-3", "test-prefix-7"], f"Expected 'test-prefix-3' or 'test-prefix-7' but got '{name}'"
