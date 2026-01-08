# Performance Tuning Guide

## Stream Buffer Size

### Overview

Acexy uses a configurable buffer for stream delivery to ensure smooth playback and prevent frame drops. The buffer acts as a shock absorber between the AceStream engine's data delivery and the client's consumption rate.

### How It Works

When streaming video data from AceStream to the client:

1. **Data Arrival**: AceStream engine sends video data (often in bursts)
2. **Buffering**: Acexy buffers the data in memory (default 4.2MiB)
3. **Delivery**: Buffered data is written to the client in a smooth, consistent manner

This buffering mechanism prevents frame drops caused by:
- Network jitter and latency variations
- Bursty data delivery from AceStream
- Variable client read speeds
- Temporary network congestion

### Configuration

Set the buffer size using the `ACEXY_BUFFER` environment variable:

```bash
export ACEXY_BUFFER=4.2MiB
```

Or in Docker Compose:

```yaml
services:
  acexy:
    environment:
      - ACEXY_BUFFER=4.2MiB
```

### Recommended Settings

| Use Case | Buffer Size | Memory per Stream |
|----------|-------------|-------------------|
| Low-bitrate streams (SD) | 1-2 MiB | 1-2 MB |
| Standard HD streams | 4.2 MiB (default) | 4.2 MB |
| High-bitrate 4K streams | 8-16 MiB | 8-16 MB |
| Unreliable networks | 16-32 MiB | 16-32 MB |

### Memory Considerations

Total memory usage = `ACEXY_BUFFER × concurrent streams`

**Examples:**
- 10 concurrent streams × 4.2 MiB = ~42 MB buffer memory
- 50 concurrent streams × 4.2 MiB = ~210 MB buffer memory
- 100 concurrent streams × 4.2 MiB = ~420 MB buffer memory

**Note**: This is buffer memory only. Total memory usage includes Go runtime, HTTP connections, and other overhead.

### Troubleshooting Frame Drops

If you experience frame drops despite buffering:

1. **Increase buffer size** to 8-16 MiB for more tolerance to network jitter
2. **Check network latency** between acexy and AceStream engine
3. **Monitor CPU usage** - high CPU may delay buffer flushes
4. **Check client bandwidth** - ensure client can sustain the stream bitrate
5. **Enable debug mode** to identify performance bottlenecks

### Technical Details

Acexy uses a `bufio.Writer` with the configured buffer size to batch write operations:

- **Small buffer (32KB - default io.Copy)**: More frequent writes, higher sensitivity to network jitter
- **Large buffer (4.2MiB - acexy default)**: Fewer writes, better tolerance to network variations

The buffer is automatically flushed when:
- Buffer is full
- Stream completes (EOF)
- Empty timeout is reached (no data for configured duration)

### Related Configuration

Other settings that affect streaming performance:

| Setting | Description | Default |
|---------|-------------|---------|
| `ACEXY_EMPTY_TIMEOUT` | Timeout for idle streams | 60s |
| `ACEXY_NO_RESPONSE_TIMEOUT` | Timeout for engine response | 1s |

### Best Practices

1. **Start with defaults**: 4.2MiB works well for most scenarios
2. **Monitor memory**: Ensure sufficient memory for expected concurrent streams
3. **Test under load**: Validate buffer size under realistic streaming conditions
4. **Adjust gradually**: Increase buffer in small increments (2-4 MiB steps)
5. **Use debug mode**: Enable debug logging to identify performance issues

## Additional Optimizations

### Network Optimization

For optimal P2P performance, expose AceStream's P2P ports:

```yaml
services:
  acexy:
    ports:
      - "8080:8080"
      - "8621:8621/tcp"
      - "8621:8621/udp"
```

### Host Network Mode (Linux only)

For best performance on Linux, use host networking:

```yaml
services:
  acexy:
    network_mode: host
```

This eliminates Docker bridge networking overhead and enables direct UPnP IGD access.

## Monitoring

Enable debug mode to track streaming performance:

```yaml
services:
  acexy:
    environment:
      - DEBUG_MODE=true
      - DEBUG_LOG_DIR=/app/debug_logs
    volumes:
      - ./debug_logs:/app/debug_logs
```

See [DEBUG_MODE.md](DEBUG_MODE.md) for details on performance metrics.
