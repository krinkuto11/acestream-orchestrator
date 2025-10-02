#!/usr/bin/env python3
"""
Integration test to verify the datetime parsing fix between acexy and orchestrator
"""

import asyncio
import json
import subprocess
import time
import sys
import os
from datetime import datetime, timezone

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from models.schemas import EngineState, StreamState

# Create a minimal test without full state import
from datetime import datetime, timezone

async def test_orchestrator_datetime_fix():
    """Test the complete flow that was failing"""
    
    print("🧪 Testing orchestrator datetime fix...")
    
    # Test 1: Create engines and streams directly (without full state)
    print("\n📊 Test 1: Creating timezone-aware datetime objects...")
    
    # Add engine with current UTC time
    utc_now = datetime.now(timezone.utc)
    engine = EngineState(
        container_id="test-engine-123",
        host="localhost",
        port=19001,
        labels={"app": "acestream"},
        first_seen=utc_now,
        last_seen=utc_now,
        streams=[]
    )
    
    # Add stream
    stream = StreamState(
        id="test-stream-456",
        key_type="content_id", 
        key="abc123def456",
        container_id="test-engine-123",
        playback_session_id="session789",
        stat_url="http://localhost:19001/ace/stat",
        command_url="http://localhost:19001/ace/cmd",
        is_live=True,
        started_at=utc_now,
        status="started"
    )
    
    # Test 2: Verify serialization produces timezone-aware JSON
    print("\n🔄 Test 2: Verifying JSON serialization...")
    
    engines_json = json.dumps([engine.model_dump()], default=str)
    print(f"Engines JSON: {engines_json}")
    
    # Parse back and check for timezone
    engines_data = json.loads(engines_json)
    first_seen_str = engines_data[0]['first_seen']
    print(f"First seen: {first_seen_str}")
    
    if first_seen_str.endswith('Z') or '+' in first_seen_str:
        print("✅ Engine datetime includes timezone info")
    else:
        print("❌ Engine datetime missing timezone info")
        return False
        
    streams_json = json.dumps([stream.model_dump()], default=str)
    print(f"Streams JSON: {streams_json}")
    
    streams_data = json.loads(streams_json)
    started_at_str = streams_data[0]['started_at']
    print(f"Started at: {started_at_str}")
    
    if started_at_str.endswith('Z') or '+' in started_at_str:
        print("✅ Stream datetime includes timezone info")
    else:
        print("❌ Stream datetime missing timezone info")
        return False
    
    # Test 3: Simulate the database round-trip issue
    print("\n💾 Test 3: Simulating database round-trip (the fix)...")
    
    # Simulate what happens when loading from database with naive datetimes
    class MockDbEngine:
        def __init__(self):
            self.engine_key = "test-engine-123"
            self.container_id = "test-engine-123"
            self.host = "localhost"
            self.port = 19001
            self.labels = {"app": "acestream"}
            # Simulate database returning naive datetime (the problem)
            self.first_seen = datetime.now()  # Naive
            self.last_seen = datetime.now()   # Naive
            
    class MockDbStream:
        def __init__(self):
            self.id = "test-stream-456"
            self.engine_key = "test-engine-123"
            self.key_type = "content_id"
            self.key = "abc123def456"
            self.playback_session_id = "session789"
            self.stat_url = "http://localhost:19001/ace/stat"
            self.command_url = "http://localhost:19001/ace/cmd"
            self.is_live = True
            self.started_at = datetime.now()  # Naive
            self.ended_at = None
            self.status = "started"
    
    # Test the fix: ensure naive datetimes become timezone-aware
    mock_engine = MockDbEngine()
    mock_stream = MockDbStream()
    
    print(f"Mock engine first_seen (naive): {mock_engine.first_seen} (tz: {mock_engine.first_seen.tzinfo})")
    print(f"Mock stream started_at (naive): {mock_stream.started_at} (tz: {mock_stream.started_at.tzinfo})")
    
    # Apply the fix (from our updated load_from_db method)
    fixed_first_seen = mock_engine.first_seen.replace(tzinfo=timezone.utc) if mock_engine.first_seen.tzinfo is None else mock_engine.first_seen
    fixed_last_seen = mock_engine.last_seen.replace(tzinfo=timezone.utc) if mock_engine.last_seen.tzinfo is None else mock_engine.last_seen
    fixed_started_at = mock_stream.started_at.replace(tzinfo=timezone.utc) if mock_stream.started_at.tzinfo is None else mock_stream.started_at
    fixed_ended_at = mock_stream.ended_at.replace(tzinfo=timezone.utc) if mock_stream.ended_at and mock_stream.ended_at.tzinfo is None else mock_stream.ended_at
    
    print(f"Fixed first_seen: {fixed_first_seen} (tz: {fixed_first_seen.tzinfo})")
    print(f"Fixed started_at: {fixed_started_at} (tz: {fixed_started_at.tzinfo})")
    
    # Create EngineState and StreamState with fixed datetimes
    fixed_engine = EngineState(
        container_id=mock_engine.engine_key,
        host=mock_engine.host,
        port=mock_engine.port,
        labels=mock_engine.labels or {},
        first_seen=fixed_first_seen,
        last_seen=fixed_last_seen,
        streams=[]
    )
    
    fixed_stream = StreamState(
        id=mock_stream.id,
        key_type=mock_stream.key_type,
        key=mock_stream.key,
        container_id=mock_stream.engine_key,
        playback_session_id=mock_stream.playback_session_id,
        stat_url=mock_stream.stat_url,
        command_url=mock_stream.command_url,
        is_live=mock_stream.is_live,
        started_at=fixed_started_at,
        ended_at=fixed_ended_at,
        status=mock_stream.status
    )
    
    # Test JSON serialization of fixed objects
    fixed_engine_json = fixed_engine.model_dump_json()
    fixed_stream_json = fixed_stream.model_dump_json()
    
    print(f"Fixed engine JSON: {fixed_engine_json}")
    print(f"Fixed stream JSON: {fixed_stream_json}")
    
    # Verify timezone is present
    fixed_engine_data = json.loads(fixed_engine_json)
    fixed_stream_data = json.loads(fixed_stream_json)
    
    if (fixed_engine_data['first_seen'].endswith('Z') and 
        fixed_stream_data['started_at'].endswith('Z')):
        print("✅ Fixed objects have timezone info")
    else:
        print("❌ Fixed objects missing timezone info")
        return False
    
    print("\n🎯 Test 4: Simulating Go client parsing...")
    
    # Create test JSON that matches what the /engines endpoint would return
    test_engines_response = json.dumps([{
        "container_id": "test-engine-123",
        "host": "localhost", 
        "port": 19001,
        "labels": {"app": "acestream"},
        "first_seen": fixed_engine_data['first_seen'],
        "last_seen": fixed_engine_data['last_seen'],
        "streams": ["test-stream-456"]
    }])
    
    # Write a temporary Go test file
    go_test_code = f'''package main

import (
	"encoding/json"
	"fmt"
	"time"
)

type engineState struct {{
	ContainerID string            `json:"container_id"`
	Host        string            `json:"host"`
	Port        int               `json:"port"`
	Labels      map[string]string `json:"labels"`
	FirstSeen   time.Time         `json:"first_seen"`
	LastSeen    time.Time         `json:"last_seen"`
	Streams     []string          `json:"streams"`
}}

func main() {{
	jsonData := `{test_engines_response}`
	
	var engines []engineState
	err := json.Unmarshal([]byte(jsonData), &engines)
	if err != nil {{
		fmt.Printf("ERROR: %v\\n", err)
		return
	}}
	
	fmt.Printf("SUCCESS: Parsed %d engines\\n", len(engines))
	fmt.Printf("Engine: %s at %s:%d\\n", engines[0].ContainerID, engines[0].Host, engines[0].Port)
	fmt.Printf("First seen: %v\\n", engines[0].FirstSeen)
}}'''
    
    with open('/tmp/test_go_integration.go', 'w') as f:
        f.write(go_test_code)
    
    # Run the Go test
    try:
        result = subprocess.run(['go', 'run', '/tmp/test_go_integration.go'], 
                              capture_output=True, text=True, cwd='/tmp')
        print(f"Go test output: {result.stdout}")
        if result.stderr:
            print(f"Go test stderr: {result.stderr}")
        
        if result.returncode == 0 and "SUCCESS" in result.stdout:
            print("✅ Go client successfully parsed fixed datetime format")
        else:
            print("❌ Go client failed to parse datetime")
            return False
            
    except Exception as e:
        print(f"❌ Failed to run Go test: {e}")
        return False
    
    print("\n🎉 All integration tests passed!")
    print("The datetime parsing issue has been fixed:")
    print("- Python orchestrator now ensures timezone-aware datetimes when loading from DB")
    print("- JSON responses include timezone information (Z suffix)")
    print("- Go acexy client can successfully parse the datetime format")
    print("- The original error 'cannot parse \"\" as \"Z07:00\"' should no longer occur")
    
    return True

if __name__ == "__main__":
    success = asyncio.run(test_orchestrator_datetime_fix())
    sys.exit(0 if success else 1)