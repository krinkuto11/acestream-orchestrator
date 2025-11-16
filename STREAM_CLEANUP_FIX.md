# Stream Cleanup Fix - Implementation Summary

## Problem Statement

Two issues were identified in the stream management system:

1. **Ended streams accumulate indefinitely**: The `/streams` endpoint was returning all streams by default (both started and ended), and ended streams were never removed from memory or database, leading to unbounded growth.

2. **Confusing stale stream detection logs**: Logs showed repeated "Detected stale stream" messages without corresponding "Automatically ending stale stream" messages, causing confusion about whether streams were being properly handled.

## Root Causes

1. **No cleanup mechanism**: The `state.streams` dictionary never removed ended streams - they would accumulate in memory forever.

2. **No default filter on /streams**: The `/streams` endpoint had no default status filter, returning both started and ended streams, which became increasingly problematic as ended streams accumulated.

3. **Misleading logging**: The collector logged "Detected stale stream" before checking if the stream was actually still started, resulting in INFO logs for already-ended streams.

## Solution

### 1. Changed `/streams` Endpoint Default Behavior
**File**: `app/main.py`

Changed the `/streams` endpoint to default to showing only `started` streams:
```python
def get_streams(status: Optional[str] = Query("started", pattern="^(started|ended)$"), ...):
```

**Impact**: 
- By default, `/streams` now shows only active streams
- Ended streams can still be accessed via `/streams?status=ended`
- Prevents accumulation from being visible to normal API consumers

### 2. Added Stream Cleanup Method
**File**: `app/services/state.py`

Added `cleanup_ended_streams()` method to the `State` class:
- Removes ended streams older than a configurable threshold (default: 1 hour)
- Cleans up both in-memory state and database records
- Also removes associated stream stats to free memory
- Returns count of removed streams for monitoring

### 3. Created Stream Cleanup Service
**File**: `app/services/stream_cleanup.py`

Created a new background service that:
- Runs every 5 minutes
- Automatically calls `state.cleanup_ended_streams()`
- Logs cleanup activity for observability
- Integrated into application lifecycle (starts on startup, stops on shutdown)

### 4. Fixed Collector Logging
**File**: `app/services/collector.py`

Improved logging to reduce confusion:
- Only logs INFO "Detected stale stream" + "Automatically ending" when actually ending a started stream
- Logs DEBUG when stream is already ended (expected case on subsequent checks)
- Eliminates misleading log messages

## Testing

Created comprehensive test suite:

### Unit Tests (`tests/test_stream_cleanup.py`)
- Test cleanup removes old ended streams
- Test cleanup keeps recent ended streams
- Test cleanup never removes started streams
- Test list_streams filtering works correctly
- Test collector logging behavior

### Integration Test (`tests/test_stream_cleanup_integration.py`)
- Tests complete lifecycle: start → stale detection → end → cleanup
- Verifies cleanup service can start and stop
- Validates all components work together

All tests pass successfully.

## Configuration

The cleanup behavior can be adjusted by modifying the `StreamCleanup` class:
- `_max_age_seconds`: How old ended streams must be before cleanup (default: 3600 = 1 hour)
- `cleanup_interval`: How often cleanup runs (default: 300 = 5 minutes)

## Backward Compatibility

**Breaking Change**: The `/streams` endpoint now defaults to `status=started` instead of returning all streams.

**Migration**: If any existing code depends on getting all streams by default, it should be updated to explicitly use:
- `/streams?status=started` for active streams (new default)
- `/streams?status=ended` for ended streams
- Query without status parameter to get both (no longer available - must use one of the above)

Actually, looking at the code again, there's a subtle issue - if someone wants BOTH started and ended streams, they can no longer get them in one call. Let me check if we should support a way to get all streams.

Looking at the pattern regex `^(started|ended)$`, it only accepts "started" or "ended", not "all" or similar. Since the default is now "started", and you can get "ended" explicitly, but there's no way to get both in one call, this could be an issue for some use cases.

However, for the problem at hand (ended streams shouldn't be kept in the endpoint), defaulting to "started" is the right fix. If someone needs all streams, they can make two API calls or we can add support for "all" later if needed.

## Benefits

1. **Prevents memory leaks**: Ended streams are now automatically cleaned up after 1 hour
2. **Cleaner API responses**: `/streams` endpoint shows only relevant (active) streams by default
3. **Better observability**: Clearer logging for stale stream detection
4. **Database efficiency**: Old stream records are removed from the database
5. **Configurable**: Cleanup thresholds can be adjusted as needed

## Monitoring

The cleanup service logs when it removes streams:
```
INFO: Cleaned up N ended streams older than 3600s
```

The stale stream detection now produces clearer logs:
```
INFO: Detected stale stream <id>: unknown playback session id
INFO: Automatically ending stale stream <id>
```

Or (for already-ended streams):
```
DEBUG: Stale stream <id> is already ended or doesn't exist, skipping
```
