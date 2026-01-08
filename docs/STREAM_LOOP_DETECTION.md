# Stream Loop Detection and Looping Streams Tracker

This document describes the stream loop detection feature and the looping streams tracker that prevents playback of detected looping streams.

## Overview

The orchestrator includes a stream loop detection system that automatically identifies and stops streams that are looping (not receiving new data from the broadcast). When a looping stream is detected, its AceStream ID is added to a tracker that can be queried by the Acexy proxy to prevent playback attempts.

## Components

### 1. Stream Loop Detector (`app/services/stream_loop_detector.py`)

Periodically monitors active live streams by checking their `live_last` timestamp from the AceStream engine's stat URL. If a stream falls too far behind the current time, it's considered looping and automatically stopped.

**Configuration:**
- `STREAM_LOOP_DETECTION_ENABLED`: Enable/disable loop detection (default: `false`)
- `STREAM_LOOP_DETECTION_THRESHOLD_S`: Time threshold in seconds (default: `3600` = 1 hour)
- `STREAM_LOOP_CHECK_INTERVAL_S`: How often to check streams (default: `10` seconds)

### 2. Looping Streams Tracker (`app/services/looping_streams.py`)

Maintains a list of AceStream content IDs that have been detected as looping. Supports configurable retention time for automatic cleanup.

**Configuration:**
- `STREAM_LOOP_RETENTION_MINUTES`: How long to keep looping stream IDs
  - `0` or unset: Indefinite retention (manual removal required)
  - `> 0`: Automatic removal after specified minutes

### 3. Acexy Proxy Integration (`context/acexy/acexy/`)

The Acexy proxy checks the looping streams list before selecting an engine. If a stream ID is marked as looping, Acexy returns a "stream not available" error to the player.

**Changes:**
- `SelectBestEngine()` now accepts `aceID` parameter
- New `IsStreamLooping()` method checks the `/looping-streams` endpoint
- Returns structured error with `stream_looping` code when detected

## API Endpoints

### GET `/looping-streams`

Returns list of streams currently marked as looping.

**Response:**
```json
{
  "stream_ids": ["content_id_1", "content_id_2"],
  "streams": {
    "content_id_1": "2024-01-08T12:00:00Z",
    "content_id_2": "2024-01-08T12:05:00Z"
  },
  "retention_minutes": 0
}
```

### DELETE `/looping-streams/{stream_id}`

Manually remove a stream from the looping list. Requires API key.

**Response:**
```json
{
  "message": "Stream content_id_1 removed from looping list"
}
```

### POST `/looping-streams/clear`

Clear all looping streams. Requires API key.

**Response:**
```json
{
  "message": "All looping streams cleared"
}
```

### GET `/stream-loop-detection/config`

Get current loop detection configuration.

**Response:**
```json
{
  "enabled": true,
  "threshold_seconds": 3600,
  "threshold_minutes": 60,
  "threshold_hours": 1,
  "check_interval_seconds": 10,
  "retention_minutes": 0
}
```

### POST `/stream-loop-detection/config`

Update loop detection configuration. Requires API key.

**Parameters:**
- `enabled` (boolean): Enable/disable loop detection
- `threshold_seconds` (integer): Detection threshold (minimum: 60)
- `check_interval_seconds` (integer, optional): Check frequency (minimum: 5)
- `retention_minutes` (integer, optional): Retention time (0 = indefinite)

**Response:**
```json
{
  "message": "Stream loop detection configuration updated",
  "enabled": true,
  "threshold_seconds": 3600,
  "threshold_minutes": 60,
  "threshold_hours": 1,
  "check_interval_seconds": 10,
  "retention_minutes": 0
}
```

## UI Configuration

The orchestrator panel provides a Settings page with loop detection configuration:

1. **Enable Loop Detection**: Toggle to enable/disable the feature
2. **Threshold**: Time in minutes before a stream is considered looping
3. **Check Interval**: How often to check streams (in seconds)
4. **Retention Time**: How long to keep looping stream IDs (0 = indefinite)
5. **Looping Streams List**: View and manage currently blocked streams

## Usage Examples

### Enable Loop Detection with 2-hour threshold

```bash
curl -X POST "http://orchestrator:8000/stream-loop-detection/config?enabled=true&threshold_seconds=7200&check_interval_seconds=15&retention_minutes=120" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Check if a stream is looping

```bash
curl "http://orchestrator:8000/looping-streams"
```

### Manually remove a looping stream

```bash
curl -X DELETE "http://orchestrator:8000/looping-streams/STREAM_ID" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Behavior

### Loop Detection Process

1. Stream loop detector checks active live streams every `STREAM_LOOP_CHECK_INTERVAL_S` seconds
2. For each stream, it queries the engine's stat URL to get `live_last` timestamp
3. If `current_time - live_last > STREAM_LOOP_DETECTION_THRESHOLD_S`:
   - Stream is stopped via command URL
   - Stream's content ID is added to looping streams tracker
   - Event is logged to orchestrator event log

### Acexy Proxy Behavior

1. When a client requests a stream via `/ace/getstream?id=CONTENT_ID`
2. Acexy calls orchestrator's `/looping-streams` endpoint
3. If the content ID is in the list:
   - Returns 503 Service Unavailable
   - Error code: `stream_looping`
   - Message: "This stream has been detected as looping (no new data). Playback is not available."
   - Video player sees this as stream unavailable/error

### Retention and Cleanup

- If `STREAM_LOOP_RETENTION_MINUTES = 0`: Streams remain indefinitely until manually removed
- If `STREAM_LOOP_RETENTION_MINUTES > 0`: Background task removes entries older than configured time
- Cleanup runs every 60 seconds

## Best Practices

1. **Threshold Configuration**: Set based on your use case
   - Live sports/events: 30-60 minutes (detect stale feeds quickly)
   - General streaming: 1-2 hours (avoid false positives)

2. **Retention Strategy**:
   - Indefinite retention (0): For manual curation
   - Time-limited retention: For automatic recovery (e.g., 24 hours)

3. **Check Interval**:
   - Lower values (5-10s): Faster detection, higher CPU usage
   - Higher values (30-60s): Lower CPU usage, slower detection

4. **Monitoring**:
   - Check orchestrator event logs for loop detection events
   - Monitor looping streams list via UI or API
   - Use retention to automatically clear recovered streams

## Troubleshooting

### Stream marked as looping but is actually live

- Manually remove from looping list via UI or API
- Check if `live_last` timestamp is updating correctly
- Verify stream is actually receiving new data
- Consider increasing detection threshold

### Loop detection not triggering

- Verify `STREAM_LOOP_DETECTION_ENABLED=true`
- Check that streams are marked as `is_live=true`
- Ensure stat URLs are accessible
- Review orchestrator logs for errors

### Acexy not blocking looping streams

- Verify Acexy can reach orchestrator's `/looping-streams` endpoint
- Check network connectivity and firewall rules
- Review Acexy logs for looping stream checks
- Ensure stream ID format matches (content_id vs infohash)

## Migration Notes

This feature is backwards compatible. Existing installations will have loop detection disabled by default. To enable:

1. Set `STREAM_LOOP_DETECTION_ENABLED=true` in `.env`
2. Configure threshold and check interval as needed
3. Restart orchestrator
4. Configure retention time via UI or API

No Acexy changes are required - the proxy gracefully handles missing `/looping-streams` endpoint (fails open).
