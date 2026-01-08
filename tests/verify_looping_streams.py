#!/usr/bin/env python3
"""
Quick verification script for stream loop detection feature.
Tests the basic functionality without requiring a running orchestrator.
"""

import sys
import os

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.looping_streams import looping_streams_tracker
from datetime import datetime

def test_basic_functionality():
    """Test basic looping streams tracker functionality."""
    print("🧪 Testing Looping Streams Tracker...")
    
    # Test 1: Initialize and clear
    print("\n1. Initializing tracker...")
    looping_streams_tracker.clear_all()
    streams = looping_streams_tracker.get_looping_stream_ids()
    assert len(streams) == 0, "Initial state should be empty"
    print("   ✅ Tracker initialized")
    
    # Test 2: Add a stream
    print("\n2. Adding a looping stream...")
    test_id = "test_stream_abc123"
    looping_streams_tracker.add_looping_stream(test_id)
    assert looping_streams_tracker.is_looping(test_id), "Stream should be marked as looping"
    print(f"   ✅ Added stream: {test_id}")
    
    # Test 3: Get streams with timestamps
    print("\n3. Retrieving looping streams...")
    all_streams = looping_streams_tracker.get_looping_streams()
    assert test_id in all_streams, "Stream should be in the list"
    timestamp = all_streams[test_id]
    print(f"   ✅ Stream found with timestamp: {timestamp}")
    
    # Verify timestamp is valid ISO format
    parsed_time = datetime.fromisoformat(timestamp)
    print(f"   ✅ Timestamp parsed: {parsed_time}")
    
    # Test 4: Remove stream
    print("\n4. Removing stream...")
    removed = looping_streams_tracker.remove_looping_stream(test_id)
    assert removed, "Stream should be removed successfully"
    assert not looping_streams_tracker.is_looping(test_id), "Stream should no longer be looping"
    print(f"   ✅ Removed stream: {test_id}")
    
    # Test 5: Retention configuration
    print("\n5. Testing retention configuration...")
    
    # Set to indefinite
    looping_streams_tracker.set_retention_minutes(0)
    assert looping_streams_tracker.get_retention_minutes() is None, "0 should map to None (indefinite)"
    print("   ✅ Indefinite retention (0 → None)")
    
    # Set to specific value
    looping_streams_tracker.set_retention_minutes(60)
    assert looping_streams_tracker.get_retention_minutes() == 60, "Retention should be 60 minutes"
    print("   ✅ Time-limited retention (60 minutes)")
    
    # Test 6: Multiple streams
    print("\n6. Testing multiple streams...")
    streams_to_add = ["stream_1", "stream_2", "stream_3"]
    for stream_id in streams_to_add:
        looping_streams_tracker.add_looping_stream(stream_id)
    
    all_streams = looping_streams_tracker.get_looping_stream_ids()
    assert len(all_streams) == 3, f"Should have 3 streams, got {len(all_streams)}"
    print(f"   ✅ Added {len(all_streams)} streams")
    
    # Test 7: Clear all
    print("\n7. Clearing all streams...")
    looping_streams_tracker.clear_all()
    all_streams = looping_streams_tracker.get_looping_stream_ids()
    assert len(all_streams) == 0, "All streams should be cleared"
    print("   ✅ All streams cleared")
    
    print("\n" + "="*60)
    print("✅ ALL TESTS PASSED!")
    print("="*60)
    
    return True

if __name__ == "__main__":
    try:
        success = test_basic_functionality()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
