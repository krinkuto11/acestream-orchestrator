#!/usr/bin/env python3
"""
Demo script to show livepos data in streams endpoint.

This script simulates a live stream with livepos data and shows
how the data is exposed via the /streams endpoint.
"""

import sys
import os
from datetime import datetime, timezone

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.state import State
from app.models.schemas import (
    StreamState, 
    StreamStartedEvent, 
    StreamStatSnapshot, 
    EngineAddress, 
    StreamKey, 
    SessionInfo,
    LivePosData
)

# Setup test database
from app.services.db import engine as db_engine
from app.models.db_models import Base

# Create all tables
Base.metadata.create_all(bind=db_engine)


def demo_livepos_in_streams_endpoint():
    """Demonstrate livepos data in streams endpoint response."""
    print("=" * 80)
    print("DEMO: LivePos Data in /streams Endpoint")
    print("=" * 80)
    print()
    
    # Create a fresh state
    test_state = State()
    
    # Simulate starting a live stream (like what acexy would send)
    print("1. Starting a live stream...")
    evt = StreamStartedEvent(
        container_id="demo_container_live",
        engine=EngineAddress(host="127.0.0.1", port=6878),
        stream=StreamKey(
            key_type="content_id", 
            key="c1959a27edb0b94c5005a2dea93b7a70d4312f1c"
        ),
        session=SessionInfo(
            playback_session_id="8c01437707a73e8d2c57f4ae30aad464becd0fa1",
            stat_url="http://127.0.0.1:6878/ace/stat/8c01437707a73e8d2c57f4ae30aad464becd0fa1",
            command_url="http://127.0.0.1:6878/ace/cmd/8c01437707a73e8d2c57f4ae30aad464becd0fa1",
            is_live=1
        )
    )
    
    stream_state = test_state.on_stream_started(evt)
    stream_id = stream_state.id
    print(f"   ✓ Stream started: {stream_id[:32]}...")
    print()
    
    # Simulate collector fetching stats with livepos (happens every 1 second now)
    print("2. Collector fetching stats with livepos data (every 1 second)...")
    
    # This is what the actual AceStream stat URL returns
    stat_response_sample = {
        "response": {
            "status": "dl",
            "total_progress": 0,
            "speed_down": 158,
            "speed_up": 6,
            "peers": 15,
            "downloaded": 72613888,
            "uploaded": 2506752,
            "playback_session_id": "8c01437707a73e8d2c57f4ae30aad464becd0fa1",
            "selected_file_index": -1,
            "infohash": "c1959a27edb0b94c5005a2dea93b7a70d4312f1c",
            "client_session_id": -1,
            "is_live": 1,
            "is_encrypted": 0,
            "livepos": {
                "pos": "1767629806",
                "is_live": "1",
                "buffer_pieces": "15",
                "last": "1767629808",
                "live_first": "1767628008",
                "live_last": "1767629808",
                "first_ts": "1767628008",
                "last_ts": "1767629808"
            }
        },
        "error": None
    }
    
    print("   Sample stat response from AceStream engine:")
    print(f"   - Status: {stat_response_sample['response']['status']}")
    print(f"   - Peers: {stat_response_sample['response']['peers']}")
    print(f"   - Download: {stat_response_sample['response']['speed_down']} KB/s")
    print(f"   - Upload: {stat_response_sample['response']['speed_up']} KB/s")
    print(f"   - LivePos Present: {bool(stat_response_sample['response'].get('livepos'))}")
    print()
    
    # Simulate what the collector does
    livepos_raw = stat_response_sample['response']['livepos']
    livepos = LivePosData(
        pos=livepos_raw.get("pos"),
        live_first=livepos_raw.get("live_first"),
        live_last=livepos_raw.get("live_last"),
        first_ts=livepos_raw.get("first_ts"),
        last_ts=livepos_raw.get("last_ts"),
        buffer_pieces=livepos_raw.get("buffer_pieces")
    )
    
    stat = StreamStatSnapshot(
        ts=datetime.now(timezone.utc),
        peers=stat_response_sample['response']['peers'],
        speed_down=stat_response_sample['response']['speed_down'],
        speed_up=stat_response_sample['response']['speed_up'],
        downloaded=stat_response_sample['response']['downloaded'],
        uploaded=stat_response_sample['response']['uploaded'],
        status=stat_response_sample['response']['status'],
        livepos=livepos
    )
    
    test_state.append_stat(stream_id, stat)
    print("   ✓ Stats collected and stored with livepos data")
    print()
    
    # Simulate the /streams endpoint
    print("3. Calling GET /streams endpoint...")
    streams = test_state.list_streams_with_stats(status="started")
    
    print(f"   ✓ Found {len(streams)} active stream(s)")
    print()
    
    # Display the response like the API would
    print("4. /streams endpoint response (JSON):")
    print("-" * 80)
    
    for stream in streams:
        stream_dict = stream.model_dump(mode='json')
        
        print(f"Stream ID: {stream_dict['id'][:32]}...")
        print(f"Status: {stream_dict['status']}")
        print(f"Is Live: {stream_dict['is_live']}")
        print(f"Container: {stream_dict['container_id']}")
        print()
        print("Latest Stats:")
        print(f"  - Peers: {stream_dict.get('peers')}")
        print(f"  - Download Speed: {stream_dict.get('speed_down')} KB/s")
        print(f"  - Upload Speed: {stream_dict.get('speed_up')} KB/s")
        print(f"  - Downloaded: {stream_dict.get('downloaded')} bytes")
        print(f"  - Uploaded: {stream_dict.get('uploaded')} bytes")
        print()
        
        if stream_dict.get('livepos'):
            print("LivePos Data (NEW!):")
            lp = stream_dict['livepos']
            print(f"  - Current Position: {lp.get('pos')}")
            print(f"  - Live First: {lp.get('live_first')}")
            print(f"  - Live Last: {lp.get('live_last')}")
            print(f"  - First Timestamp: {lp.get('first_ts')}")
            print(f"  - Last Timestamp: {lp.get('last_ts')}")
            print(f"  - Buffer Pieces: {lp.get('buffer_pieces')}")
            
            # Calculate buffer duration
            if lp.get('live_last') and lp.get('pos'):
                buffer_seconds = int(lp['live_last']) - int(lp['pos'])
                print(f"  - Buffer Duration: {buffer_seconds} seconds")
        else:
            print("LivePos Data: Not available (VOD stream)")
        
        print()
    
    print("-" * 80)
    print()
    
    # Demonstrate ended streams persistence
    print("5. Simulating stream end...")
    from app.models.schemas import StreamEndedEvent
    test_state.on_stream_ended(StreamEndedEvent(
        container_id="demo_container_live",
        stream_id=stream_id,
        reason="demo_ended"
    ))
    print("   ✓ Stream ended")
    print()
    
    print("6. Calling GET /streams endpoint (no status filter)...")
    all_streams = test_state.list_streams_with_stats()  # No status filter = all streams
    
    active_count = len([s for s in all_streams if s.status == 'started'])
    ended_count = len([s for s in all_streams if s.status == 'ended'])
    
    print(f"   ✓ Total streams: {len(all_streams)}")
    print(f"     - Active: {active_count}")
    print(f"     - Ended: {ended_count}")
    print()
    
    print("   Note: Ended streams will persist in the UI until page reload,")
    print("         allowing users to review final stats before they disappear.")
    print()
    
    # Clean up
    test_state.clear_state()
    
    print("=" * 80)
    print("✅ Demo completed successfully!")
    print("=" * 80)
    print()
    print("Summary of Changes:")
    print("  1. ✓ Collector now fetches livepos data every 1 second (was 2 seconds)")
    print("  2. ✓ /streams endpoint includes livepos data in response")
    print("  3. ✓ /streams endpoint returns all streams by default (active + ended)")
    print("  4. ✓ UI shows active and ended streams in separate tables")
    print("  5. ✓ Ended streams persist until page reload")
    print()


if __name__ == "__main__":
    demo_livepos_in_streams_endpoint()
