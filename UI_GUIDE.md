# UI Screenshots Guide

## Before (Diagnostics Section)

The Settings page previously had a diagnostics section that allowed testing AceStream proxy connections. This has been completely removed.

## After - Streams Section (Both Modes)

### Streams Table - Collapsed View
Both lightweight and FFmpeg modes show the same table view:
- Status badge (ACTIVE/ENDED)
- Stream ID (truncated)
- Engine name
- Started timestamp
- Download/Upload speeds
- Peers count
- Downloaded/Uploaded totals

### Streams Table - Expanded View

#### Lightweight Mode
When `PROXY_MODE=lightweight`, the expanded stream details show:
```
Stream Details:
  Stream ID: abc123def456...
  Engine: acestream-engine-1
  Started At: 2026-01-07 19:00:00
  
Live Position Data: (if available)
  Current Position: ...
  Live Start: ...
  ...
  
[Statistics URL] [Command URL]

[Performance Chart]
```

#### FFmpeg Mode
When `PROXY_MODE=ffmpeg`, the expanded stream details show **additional metadata**:
```
Stream Details:
  Stream ID: abc123def456...
  Engine: acestream-engine-1
  Started At: 2026-01-07 19:00:00
  Resolution: 1920x1080       ← NEW
  FPS: 25.00                   ← NEW
  Video Codec: H264            ← NEW
  Audio Codec: AAC             ← NEW
  
Live Position Data: (if available)
  Current Position: ...
  Live Start: ...
  ...
  
[Statistics URL] [Command URL]

[Performance Chart]
```

## Key UI Features

### Conditional Rendering
The metadata fields (Resolution, FPS, Video Codec, Audio Codec) only appear when:
1. `PROXY_MODE=ffmpeg` is configured
2. FFmpeg successfully extracted the metadata
3. The stream is active or recently ended

### Formatting
- **Resolution**: Displayed as "{width}x{height}" (e.g., "1920x1080")
- **FPS**: Displayed with 2 decimal places (e.g., "25.00")
- **Codecs**: Displayed in uppercase (e.g., "H264", "AAC")
- **Labels**: Muted foreground color
- **Values**: Medium weight, foreground color

### Layout
Metadata fields are integrated into the existing grid layout, appearing after the timestamp fields and before the Live Position Data section. This ensures they're prominently displayed without disrupting the existing layout.

## Responsive Design
The metadata fields use the same responsive grid as other stream details:
- 1 column on mobile
- 2 columns on medium screens
- 3 columns on large screens

## No Breaking Changes
- Existing stream information displays normally
- Lightweight mode shows no metadata (backward compatible)
- All other stream features work as before
- No UI errors if metadata is unavailable

## Testing the UI

1. **Start orchestrator** in lightweight mode (default)
2. **Navigate** to Streams page
3. **Start a stream** via `/ace/getstream?id=<ace_id>`
4. **Click expand** on the stream row
5. **Observe**: No metadata fields (Resolution, FPS, Codecs)

6. **Change** to FFmpeg mode: `PROXY_MODE=ffmpeg`
7. **Rebuild**: `docker-compose up -d --build`
8. **Start a new stream**
9. **Click expand** on the stream row
10. **Observe**: Metadata fields appear with extracted values

## Example Metadata Values

Common values you might see in FFmpeg mode:

**Resolutions:**
- 1920x1080 (Full HD)
- 1280x720 (HD)
- 854x480 (SD)

**FPS:**
- 25.00 (PAL standard)
- 30.00 (NTSC standard)  
- 50.00 (High frame rate)
- 60.00 (High frame rate)

**Video Codecs:**
- H264 (most common)
- HEVC (H265)
- MPEG2
- VP9

**Audio Codecs:**
- AAC (most common)
- MP3
- AC3
- OPUS
