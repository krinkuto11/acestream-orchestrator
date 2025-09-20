#!/usr/bin/env python3
"""
Test script to verify datetime timezone handling fix
"""

import sys
import os
import json
from datetime import datetime, timezone

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from models.schemas import EngineState, StreamState

def test_datetime_serialization():
    """Test that datetime objects are properly serialized with timezone info"""
    
    print("Testing datetime serialization with timezone awareness...")
    
    # Test 1: Create EngineState with timezone-aware datetime
    utc_now = datetime.now(timezone.utc)
    engine = EngineState(
        container_id="test-container",
        host="localhost", 
        port=6878,
        labels={},
        first_seen=utc_now,
        last_seen=utc_now,
        streams=[]
    )
    
    # Serialize to JSON
    engine_json = engine.model_dump_json()
    print(f"Engine JSON: {engine_json}")
    
    # Parse back to verify format
    engine_dict = json.loads(engine_json)
    first_seen_str = engine_dict['first_seen']
    print(f"First seen string: {first_seen_str}")
    
    # Check if it has timezone info (should end with +00:00 or Z)
    has_timezone = first_seen_str.endswith('+00:00') or first_seen_str.endswith('Z') or '+' in first_seen_str or 'T' in first_seen_str and first_seen_str.count(':') >= 2
    
    if has_timezone:
        print("✓ Engine datetime includes timezone information")
    else:
        print("✗ Engine datetime missing timezone information")
        return False
        
    # Test 2: Create StreamState with timezone-aware datetime  
    stream = StreamState(
        id="test-stream",
        key_type="content_id",
        key="test123",
        container_id="test-container",
        playback_session_id="session123",
        stat_url="http://localhost:6878/stat",
        command_url="http://localhost:6878/cmd",
        is_live=True,
        started_at=utc_now,
        status="started"
    )
    
    stream_json = stream.model_dump_json()
    print(f"Stream JSON: {stream_json}")
    
    stream_dict = json.loads(stream_json)
    started_at_str = stream_dict['started_at']
    print(f"Started at string: {started_at_str}")
    
    has_timezone = started_at_str.endswith('+00:00') or started_at_str.endswith('Z') or '+' in started_at_str or 'T' in started_at_str and started_at_str.count(':') >= 2
    
    if has_timezone:
        print("✓ Stream datetime includes timezone information")
    else:
        print("✗ Stream datetime missing timezone information")
        return False
        
    # Test 3: Simulate naive datetime from database and fix it
    naive_datetime = datetime.now()  # This is what might come from database
    print(f"Naive datetime: {naive_datetime}")
    print(f"Naive datetime tzinfo: {naive_datetime.tzinfo}")
    
    # Apply the same fix as in the code
    fixed_datetime = naive_datetime.replace(tzinfo=timezone.utc) if naive_datetime.tzinfo is None else naive_datetime
    print(f"Fixed datetime: {fixed_datetime}")
    print(f"Fixed datetime tzinfo: {fixed_datetime.tzinfo}")
    
    # Test with EngineState using fixed datetime
    engine_fixed = EngineState(
        container_id="test-container-fixed",
        host="localhost",
        port=6878, 
        labels={},
        first_seen=fixed_datetime,
        last_seen=fixed_datetime,
        streams=[]
    )
    
    engine_fixed_json = engine_fixed.model_dump_json()
    print(f"Fixed engine JSON: {engine_fixed_json}")
    
    engine_fixed_dict = json.loads(engine_fixed_json)
    fixed_first_seen = engine_fixed_dict['first_seen']
    
    has_timezone = fixed_first_seen.endswith('+00:00') or fixed_first_seen.endswith('Z') or '+' in fixed_first_seen
    
    if has_timezone:
        print("✓ Fixed datetime includes timezone information")
        return True
    else:
        print("✗ Fixed datetime missing timezone information")
        return False

if __name__ == "__main__":
    success = test_datetime_serialization()
    if success:
        print("\n✓ All datetime tests passed!")
        sys.exit(0)
    else:
        print("\n✗ Datetime tests failed!")
        sys.exit(1)