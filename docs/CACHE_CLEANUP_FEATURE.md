# Cache Cleanup Feature Enhancement

## Overview

This enhancement adds comprehensive cache cleanup tracking, logging, and periodic cleanup functionality to the AceStream Orchestrator. The cache cleanup task targets the `/home/appuser/.ACEStream/.acestream_cache` directory in AceStream engine containers.

## Features

### 1. Cache Cleanup Tracking

Each engine now tracks:
- **Last Cache Cleanup**: Timestamp of when cache was last cleaned
- **Cache Size**: Size of the cache in bytes before cleanup

### 2. Periodic Cache Cleanup Task

A new monitoring task runs periodically to:
- Identify engines with 0 active streams (idle engines)
- Execute cache cleanup on these idle engines
- Track cleanup timestamps and cache sizes
- Update engine state and persist to database

### 3. Enhanced Logging

Cache operations now include detailed logging:
```
INFO: Running periodic cache cleanup for idle engine abc123def456
INFO: Cache size for container abc123def456: 10485760 bytes (10.00 MB)
INFO: Clearing AceStream cache for container abc123def456
INFO: Successfully cleared AceStream cache for container abc123def456 (freed 10.00 MB)
```

### 4. API Enhancement

The `/engines` endpoint now returns additional fields:

```json
{
  "container_id": "abc123def456",
  "container_name": "acestream-engine-1",
  "host": "192.168.1.100",
  "port": 6878,
  "streams": [],
  "health_status": "healthy",
  "last_health_check": "2025-10-03T19:50:47.522053+00:00",
  "last_stream_usage": "2025-10-03T19:50:47.522053+00:00",
  "last_cache_cleanup": "2025-10-03T19:50:47.522053+00:00",  // NEW
  "cache_size_bytes": 10485760                                 // NEW
}
```

### 5. UI Enhancement

The engine details panel now displays:

```
┌────────────────────────────────────────┐
│ Engine: acestream-engine-1             │
├────────────────────────────────────────┤
│ Endpoint:       192.168.1.100:6878     │
│ Active Streams: 0                      │
│ Last Used:      2m ago                 │
│ Health Check:   Just now               │
│ Cache Cleanup:  Just now          ⭐   │  <- NEW
│ Cache Size:     10.00 MB          ⭐   │  <- NEW
└────────────────────────────────────────┘
```

## Implementation Details

### Schema Changes

**app/models/schemas.py** - EngineState:
```python
class EngineState(BaseModel):
    # ... existing fields ...
    last_cache_cleanup: Optional[datetime] = None
    cache_size_bytes: Optional[int] = None
```

**app/models/db_models.py** - EngineRow:
```python
class EngineRow(Base):
    # ... existing fields ...
    last_cache_cleanup: Mapped[datetime | None] = mapped_column(DateTime)
    cache_size_bytes: Mapped[int | None] = mapped_column(Integer)
```

### Cache Cleanup Function

**app/services/provisioner.py**:
```python
def clear_acestream_cache(container_id: str) -> tuple[bool, int]:
    """
    Clear the AceStream cache in a container.
    
    Returns:
        tuple[bool, int]: (success, cache_size_bytes)
    """
    # 1. Get cache size before cleanup
    # 2. Execute rm -rf on cache directory
    # 3. Return success status and cache size
```

### Periodic Task

**app/services/monitor.py** - DockerMonitor:
```python
def _periodic_cache_cleanup(self):
    """Periodically clean cache for engines with 0 active streams."""
    # 1. Get all engines
    # 2. Filter for idle engines (0 streams)
    # 3. Execute cache cleanup
    # 4. Update state with cleanup info
```

This task runs every `AUTOSCALE_INTERVAL_S` seconds alongside other monitoring tasks.

## Configuration

No new configuration variables are required. The periodic cleanup uses the existing `AUTOSCALE_INTERVAL_S` configuration.

## Testing

Comprehensive tests validate:
- Cache cleanup with size tracking
- EngineState schema with new fields
- Function return value format
- Periodic cleanup task behavior
- Integration with stream lifecycle

Run tests:
```bash
python3 tests/test_cache_cleanup.py
python3 tests/test_cache_cleanup_enhancements.py
```

Run demo:
```bash
python3 tests/demo_cache_cleanup_features.py
```

## Benefits

1. **Automatic Cleanup**: Idle engines automatically have their cache cleaned
2. **Visibility**: Users can see when cache was last cleaned and its size
3. **Diagnostics**: Detailed logging helps troubleshoot cache issues
4. **Metrics**: Cache size information can be used for capacity planning
5. **Resource Management**: Periodic cleanup prevents cache buildup on idle engines

## Cache Cleanup Behavior

### When Cache is Cleaned

1. **On Stream End**: When the last stream on an engine ends (engine becomes idle)
2. **Periodic Task**: Every `AUTOSCALE_INTERVAL_S` seconds for engines with 0 streams

### What is Cleaned

- Path: `/home/appuser/.ACEStream/.acestream_cache`
- Command: `rm -rf /home/appuser/.ACEStream/.acestream_cache`
- Size measured before deletion with: `du -sb`

### State Updates

When cache is successfully cleaned:
1. Engine's `last_cache_cleanup` is set to current time
2. Engine's `cache_size_bytes` is set to the measured cache size
3. Updates are persisted to both in-memory state and database

## UI Elements

### JavaScript Helper

```javascript
function formatBytes(bytes) {
  if (bytes == null || bytes === 0) return 'N/A';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}
```

### Display Format

- **Cache Cleanup**: Uses `timeAgo()` helper - shows "Never", "Just now", "2m ago", "3h ago", "2d ago"
- **Cache Size**: Uses `formatBytes()` helper - shows "N/A", "1.50 MB", "512.00 KB", etc.

## Database Migration

No explicit migration is required. The new fields are nullable:
- `last_cache_cleanup: Optional[datetime] = None`
- `cache_size_bytes: Optional[int] = None`

Existing engines will have these fields as `NULL` until their first cache cleanup.

## Performance Considerations

- Cache size measurement uses `du -sb` which is fast for typical cache sizes
- Periodic cleanup only runs on idle engines (0 streams)
- State updates are performed outside of critical locks
- Database updates are non-blocking and continue on failure

## Backwards Compatibility

✅ Fully backwards compatible:
- New fields are optional (nullable)
- API clients ignoring new fields will work unchanged
- UI gracefully handles `null` values with "N/A" display
- Existing functionality is unchanged
