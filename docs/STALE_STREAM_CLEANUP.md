# Stale Stream Cleanup Process

## Overview

The Acestream Orchestrator implements an automatic stale stream detection and cleanup mechanism to maintain system health and accuracy. This process identifies streams that have stopped but were not properly closed, and automatically removes them from the active stream list.

## How Stale Streams Occur

Stale streams can occur when:
- A playback session ends abruptly without sending a proper `stream_ended` event
- The client application crashes or disconnects without cleanup
- Network issues prevent proper cleanup notifications
- The AceStream engine stops the stream internally but the orchestrator wasn't notified

## Detection Mechanism

### 1. Collector Service (`app/services/collector.py`)

The `Collector` service is responsible for detecting stale streams through periodic polling:

**Key Components:**
- **Polling Interval**: Configured via `COLLECT_INTERVAL_S` (default: varies based on configuration)
- **Detection Method**: Polls the stream stat endpoint for each active stream
- **Stale Stream Indicator**: When the stat endpoint returns:
  ```json
  {
    "response": null,
    "error": "unknown playback session id"
  }
  ```

**Process Flow:**
1. The collector runs continuously in a background task (`_run()` method)
2. Every `COLLECT_INTERVAL_S` seconds, it fetches all streams with status="started"
3. For each stream, it calls `_collect_one()` which:
   - Makes an HTTP GET request to the stream's `stat_url`
   - Checks the response for the stale stream pattern
   - If detected, automatically ends the stream

**Code Reference:**
```python
# Check if the stream has stopped/is stale
if data.get("response") is None and data.get("error"):
    error_msg = data.get("error", "").lower()
    if "unknown playback session id" in error_msg:
        logger.info(f"Detected stale stream {stream_id}: {data.get('error')}")
        # Automatically end the stream
        state.on_stream_ended(StreamEndedEvent(
            container_id=stream.container_id,
            stream_id=stream_id,
            reason="stale_stream_detected"
        ))
        orch_stale_streams_detected.inc()
```

### 2. Automatic Cleanup Actions

When a stale stream is detected:
1. A `StreamEndedEvent` is created with `reason="stale_stream_detected"`
2. The event is processed by `state.on_stream_ended()`
3. The stream is removed from the active streams list
4. The engine's stream count is decremented
5. If the engine becomes idle (0 streams), cache cleanup may be triggered
6. Prometheus metrics are updated (`orch_stale_streams_detected` counter incremented)

## Cache Cleanup Process

When streams end (including stale stream cleanup), the orchestrator manages cache to optimize resource usage:

### 1. Immediate Cache Cleanup (On Stream End)

**Location**: `app/services/state.py` - `on_stream_ended()` method

**Trigger**: When an engine's last stream ends (becomes idle)

**Process**:
```python
if engine_became_idle and container_id_for_cleanup:
    logger.debug(f"Engine {container_id_for_cleanup[:12]} has no active streams, clearing cache")
    success, cache_size = clear_acestream_cache(container_id_for_cleanup)
    # Update engine state with cleanup timestamp and cache size
```

**Actions**:
- Executes cache cleanup command inside the container
- Records the cache size before cleanup
- Updates engine state with `last_cache_cleanup` timestamp
- Stores cache metrics for monitoring

### 2. Periodic Cache Cleanup

**Location**: `app/services/monitor.py` - `DockerMonitor._periodic_cache_cleanup()` method

**Trigger**: Runs periodically as part of the autoscale interval (`AUTOSCALE_INTERVAL_S`)

**Process**:
1. Identifies all engines with 0 active streams (idle engines)
2. For each idle engine:
   - Runs `clear_acestream_cache()` to clean the cache
   - Updates engine state with cleanup timestamp
   - Records cache size metrics
   - Updates database with cleanup information

**Benefits**:
- Catches engines that may have been missed during stream end events
- Provides regular maintenance of idle engines
- Ensures cache doesn't grow unbounded

### 3. Empty Engine Cleanup

**Location**: `app/services/monitor.py` - `DockerMonitor._cleanup_empty_engines()` method

**Trigger**: Runs periodically when `AUTO_DELETE` is enabled

**Process**:
1. Identifies engines with 0 active streams
2. Checks if engine has passed the grace period (`ENGINE_GRACE_PERIOD_S`)
3. If eligible:
   - Stops the container
   - Removes the engine from state
   - Frees up resources

**Grace Period**: Prevents premature deletion of engines that might be reused soon

## Metrics and Monitoring

### Prometheus Metrics

**Stale Stream Detection**:
- `orch_stale_streams_detected_total`: Counter tracking total stale streams detected
- Located in `app/services/metrics.py`

**Collection Errors**:
- `orch_collect_errors`: Counter tracking stat collection failures
- Helps identify polling issues

## Configuration

Key environment variables affecting stale stream cleanup:

- `COLLECT_INTERVAL_S`: How often to poll stream stats (affects detection latency)
- `AUTOSCALE_INTERVAL_S`: Interval for periodic cache cleanup
- `ENGINE_GRACE_PERIOD_S`: Time to wait before deleting empty engines
- `AUTO_DELETE`: Enable/disable automatic empty engine cleanup

## Testing

The system includes comprehensive tests for stale stream detection:

**Test Files**:
- `tests/test_stale_stream_detection.py`: Unit tests for detection logic
- `tests/test_stale_stream_integration.py`: Integration tests for full lifecycle

**Test Scenarios**:
- Detection of stale streams via stat endpoint
- Automatic stream ending
- State consistency after cleanup
- Metrics accuracy

## Error Handling

The cleanup process is resilient to errors:

1. **Collection Failures**: If stat endpoint polling fails, it increments error counter but continues
2. **Cache Cleanup Failures**: Logged but don't block other operations
3. **State Consistency**: Uses locks to prevent race conditions during cleanup
4. **Database Updates**: Wrapped in try-except to prevent database errors from blocking cleanup

## Performance Considerations

- **Async Operations**: Collector runs asynchronously to avoid blocking
- **Batch Processing**: Collects stats for all streams in parallel using `asyncio.gather()`
- **Timeout Management**: HTTP requests have 3-second timeouts to prevent hanging
- **Thread Pool**: Cache cleanup runs in executor to avoid blocking event loop

## Best Practices

1. **Monitor Metrics**: Track `orch_stale_streams_detected_total` to identify patterns
2. **Tune Intervals**: Adjust `COLLECT_INTERVAL_S` based on your workload
3. **Grace Periods**: Set `ENGINE_GRACE_PERIOD_S` appropriately for your usage patterns
4. **Error Tracking**: Monitor collection errors to identify connectivity issues
5. **Database Maintenance**: Regular database cleanup helps maintain performance

## Related Documentation

- [API Documentation](API.md) - Stream lifecycle endpoints
- [Events](EVENTS.md) - Stream event contracts
- [Configuration](CONFIG.md) - Environment variables
