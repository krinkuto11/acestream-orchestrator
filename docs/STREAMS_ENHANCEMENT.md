# Streams Endpoint and UI Enhancement

## Overview

This document describes the enhancements made to the streams endpoint and UI to provide better access to live stream information, particularly the `livepos` data that was previously only available through direct stat URL queries.

## Backend Changes

### 1. LivePos Data Structure

Added a new `LivePosData` model to capture live position information from AceStream engines:

```python
class LivePosData(BaseModel):
    """Live position data for live streams."""
    pos: Optional[str] = None                # Current playback position timestamp
    live_first: Optional[str] = None         # First available position in live buffer
    live_last: Optional[str] = None          # Last available position in live buffer
    first_ts: Optional[str] = None           # First timestamp
    last_ts: Optional[str] = None            # Last timestamp
    buffer_pieces: Optional[str] = None      # Number of buffered pieces
```

### 2. Enhanced Stream State

Updated `StreamState` to include livepos data:

```python
class StreamState(BaseModel):
    # ... existing fields ...
    livepos: Optional[LivePosData] = None  # NEW: Live position data
```

### 3. Faster Collection Interval

Changed the default collection interval from 2 seconds to 1 second for more frequent updates:

```python
COLLECT_INTERVAL_S: int = int(os.getenv("COLLECT_INTERVAL_S", 1))  # Was 2
```

### 4. Collector Enhancements

Modified the collector service to extract and store livepos data from AceStream stat responses:

```python
# Extract livepos data (for live streams)
livepos_raw = payload.get("livepos")
if livepos_raw:
    livepos_data = LivePosData(
        pos=livepos_raw.get("pos"),
        live_first=livepos_raw.get("live_first") or livepos_raw.get("first_ts"),
        live_last=livepos_raw.get("live_last") or livepos_raw.get("last_ts"),
        first_ts=livepos_raw.get("first_ts") or livepos_raw.get("first"),
        last_ts=livepos_raw.get("last_ts") or livepos_raw.get("last"),
        buffer_pieces=livepos_raw.get("buffer_pieces")
    )
```

### 5. Updated /streams Endpoint

The `/streams` endpoint now:
- Returns all streams by default (both active and ended)
- Includes livepos data in the response for live streams
- Can be filtered with `?status=started` or `?status=ended`

**Example Response:**

```json
[
  {
    "id": "c1959a27edb0b94c5005a2dea93b7a70d4312f1c|8c01437707a73e8d2c57f4ae30aad464becd0fa1",
    "key_type": "content_id",
    "key": "c1959a27edb0b94c5005a2dea93b7a70d4312f1c",
    "container_id": "acestream_engine_1",
    "status": "started",
    "is_live": true,
    "peers": 15,
    "speed_down": 158,
    "speed_up": 6,
    "downloaded": 72613888,
    "uploaded": 2506752,
    "livepos": {
      "pos": "1767629806",
      "live_first": "1767628008",
      "live_last": "1767629808",
      "first_ts": "1767628008",
      "last_ts": "1767629808",
      "buffer_pieces": "15"
    }
  }
]
```

## Frontend Changes

### 1. New StreamsTable Component

Created a modern table-based UI component using ShadCN Table:

**Features:**
- Clean, professional table layout
- Sortable columns
- Expandable rows for detailed information
- Separate sections for active and ended streams
- Color-coded status badges
- Real-time stat updates

**Visible at First Sight:**
- Status (Active/Ended)
- Stream ID (truncated)
- Engine name
- Start time
- Download/Upload speeds
- Peer count
- Total downloaded/uploaded

**Available on Row Expansion:**
- Full stream ID
- Detailed timestamps
- LivePos data (for live streams):
  - Current position
  - Live start/end positions
  - Buffer pieces
  - Calculated buffer duration
- Extended stats (title, content type, etc.)
- Stream statistics chart
- Action buttons (Stop Stream, Delete Engine)

### 2. LivePos Display

The expanded row shows livepos data in a user-friendly format:

```
LivePos Data
  - Current Position: 12/05/2025, 4:30:06 PM
  - Live Start: 12/05/2025, 4:00:08 PM
  - Live End: 12/05/2025, 4:30:08 PM
  - Buffer Pieces: 15
  - Buffer Duration: 2 seconds
```

### 3. Ended Streams Persistence

Ended streams are:
- Displayed in a separate "Ended Streams" section
- Styled with reduced opacity to distinguish from active streams
- Persisted until page reload
- Include a note: "These streams have ended. Reload the page to clear them."

### 4. Table Structure

**Active Streams Table:**
| Status | Stream ID | Engine | Started | Download | Upload | Peers | Downloaded | Uploaded |
|--------|-----------|--------|---------|----------|--------|-------|------------|----------|
| 🟢 ACTIVE | abc123... | engine-1 | 2m ago | 158 KB/s | 6 KB/s | 15 | 72.6 MB | 2.5 MB |

**Ended Streams Table:**
| Status | Stream ID | Engine | Started | Download | Upload | Peers | Downloaded | Uploaded |
|--------|-----------|--------|---------|----------|--------|-------|------------|----------|
| ⚫ ENDED | xyz789... | engine-2 | 10m ago | 0 KB/s | 0 KB/s | 0 | 150 MB | 5 MB |

## Benefits

1. **Better API Access**: Clients can now get livepos data directly from `/streams` without parsing stat URLs
2. **Faster Updates**: 1-second collection interval provides near real-time information
3. **Improved UX**: Table layout is more scannable and professional
4. **Historical Data**: Ended streams persist until reload, allowing users to review final stats
5. **Expandable Details**: Important info visible at a glance, full details on demand
6. **ShadCN Consistency**: Uses ShadCN components throughout for consistent UI/UX

## Migration Notes

- Existing API consumers will continue to work (backward compatible)
- Frontend now fetches all streams by default (was `?status=started`)
- UI component completely rewritten but maintains all functionality
- Collection interval can be overridden via `COLLECT_INTERVAL_S` env var

## Testing

Comprehensive test suite added:
- `tests/test_livepos_enrichment.py` - Tests livepos data collection and enrichment
- `tests/demo_livepos.py` - Demonstrates end-to-end functionality

All tests pass successfully.
