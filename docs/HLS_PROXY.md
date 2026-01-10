# HLS Proxy Mode

## Overview

The AceStream Orchestrator now supports both MPEG-TS and HLS (HTTP Live Streaming) proxy modes. The `/ace/getstream?id=` endpoint is an all-in-one endpoint that serves streams in the configured mode.

## Features

- **Unified Endpoint**: `/ace/getstream?id=<content_id>` serves both TS and HLS streams
- **Toggle Mode**: Switch between MPEG-TS and HLS modes in Proxy Settings
- **Variant Compatibility**: HLS mode requires the `krinkuto11-amd64` engine variant
- **Persistent Settings**: Mode selection persists across restarts
- **Automatic Validation**: System prevents incompatible configurations

## Configuration

### Via Web UI (Recommended)

1. Navigate to **Settings** → **Proxy Settings**
2. Locate the **Stream Mode** section at the top
3. Select either:
   - **MPEG-TS (Transport Stream)** - Default, works with all variants
   - **HLS (HTTP Live Streaming)** - Requires krinkuto11-amd64 variant
4. Click **Save Proxy Settings**

The UI will automatically:
- Disable HLS option if your variant doesn't support it
- Show compatibility warnings
- Display current variant information

### Via API

Update stream mode programmatically:

```bash
curl -X POST "http://localhost:8000/proxy/config?stream_mode=HLS" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Get current configuration:

```bash
curl "http://localhost:8000/proxy/config"
```

## Compatibility

### Supported Engine Variants

| Variant | TS Support | HLS Support |
|---------|-----------|-------------|
| krinkuto11-amd64 | ✅ Yes | ✅ Yes |
| jopsis-* | ✅ Yes | ❌ No |
| custom variants | ✅ Yes | ❌ No |

**Note**: HLS streaming is only available with the `krinkuto11-amd64` variant. Other variants (jopsis, custom) only support MPEG-TS mode.

## Usage

### Client Connection

The endpoint remains the same regardless of mode:

```
http://your-orchestrator:8000/ace/getstream?id=<content_id>
```

**MPEG-TS Mode**:
- Returns: `video/mp2t` content type
- Compatible with: VLC, most IPTV players

**HLS Mode**:
- Returns: `application/vnd.apple.mpegurl` content type
- Compatible with: Safari, iOS, modern browsers, HLS players

### Example with VLC

```bash
# Works in both modes
vlc "http://localhost:8000/ace/getstream?id=94c2fd8fb9bc8f2fc71a2cbe9d4b866f227a0209"
```

## Technical Details

### How It Works

1. **Configuration**: Stream mode is stored in `app/config/proxy_settings.json`
2. **Validation**: On startup and when changing modes, the system validates engine variant compatibility
3. **Engine Selection**: When requesting a stream, the proxy selects the appropriate AceStream API endpoint:
   - **TS Mode**: `/ace/getstream?id=<id>&format=json&pid=<uuid>`
   - **HLS Mode**: `/ace/manifest.m3u8?id=<id>&format=json&pid=<uuid>`
4. **Response**: The orchestrator proxies the stream with the appropriate media type

### PID Parameter

Both modes include a unique PID (Process ID) parameter to prevent conflicts when multiple clients access the same engine. This matches the behavior of the original AceXY implementation.

## Troubleshooting

### HLS Option Not Available

**Symptom**: HLS option is grayed out in the UI

**Cause**: Your engine variant doesn't support HLS

**Solution**: 
1. Check your `ENGINE_VARIANT` setting in `.env`
2. Ensure it's set to `krinkuto11-amd64`
3. Restart the orchestrator if you changed the variant

### Error: "HLS streaming is only supported for krinkuto11-amd64 variant"

**Symptom**: API returns 501 error when trying to stream in HLS mode

**Cause**: Stream mode is set to HLS but the engine variant is incompatible

**Solution**:
1. Go to Proxy Settings
2. Change Stream Mode to "MPEG-TS (Transport Stream)"
3. Save settings

OR

1. Update `ENGINE_VARIANT=krinkuto11-amd64` in `.env`
2. Restart orchestrator
3. Restart engines

### Mode Doesn't Persist

**Symptom**: Stream mode resets to TS after restart

**Cause**: Settings file is not being persisted or variant validation is reverting the mode

**Solution**:
1. Check file permissions on `app/config/` directory
2. Review orchestrator logs for validation warnings
3. Ensure variant is compatible with HLS if trying to use HLS mode

## Migration Notes

### From MPEG-TS Only

If you're upgrading from a version that only supported MPEG-TS:
- Default mode is TS (no behavior change)
- Existing configurations continue to work
- No action required unless you want to enable HLS

### Backup/Restore

Stream mode is included in backup/restore:
- Export: Stream mode is saved in backup ZIP
- Import: Stream mode is restored and validated
- Incompatible modes are automatically reverted to TS

## API Reference

### GET /proxy/config

Returns current proxy configuration including stream mode.

**Response**:
```json
{
  "stream_mode": "TS",
  "engine_variant": "krinkuto11-amd64",
  "initial_data_wait_timeout": 10,
  ...
}
```

### POST /proxy/config

Updates proxy configuration including stream mode.

**Parameters**:
- `stream_mode` (optional): "TS" or "HLS"
- Other proxy settings...

**Example**:
```bash
curl -X POST "http://localhost:8000/proxy/config?stream_mode=HLS" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

**Validation**:
- Returns 400 if `stream_mode` is not "TS" or "HLS"
- Returns 400 if HLS mode requested with incompatible variant

### GET /ace/getstream

Unified streaming endpoint supporting both modes.

**Parameters**:
- `id` (required): AceStream content ID

**Response**:
- **TS Mode**: Streaming response with `Content-Type: video/mp2t`
- **HLS Mode**: Streaming response with `Content-Type: application/vnd.apple.mpegurl`

## Performance Considerations

- **TS Mode**: Lower overhead, direct streaming
- **HLS Mode**: Slightly higher overhead due to manifest generation, better compatibility with modern browsers

Choose based on your client requirements:
- **Use TS** for IPTV players, VLC, traditional streaming clients
- **Use HLS** for web browsers, iOS/Safari, modern streaming applications
