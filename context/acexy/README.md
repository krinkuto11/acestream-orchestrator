# acexy

[![Go Build](https://github.com/krinkuto11/acexy/actions/workflows/build.yaml/badge.svg)](https://github.com/krinkuto11/acexy/actions/workflows/build.yaml)
[![Docker Release](https://github.com/krinkuto11/acexy/actions/workflows/release.yaml/badge.svg?event=release)](https://github.com/krinkuto11/acexy/actions/workflows/release.yaml)

A high-performance AceStream proxy with orchestrator-based engine management for dynamic load balancing and automatic provisioning.

## Table of Contents

- [Orchestrator Integration](#orchestrator-integration)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Advanced Topics](#advanced-topics)
  - [Network Optimization](#network-optimization)
  - [Host Network Mode](#host-network-mode)
  - [Stream Buffer Tuning](#stream-buffer-tuning)
  - [Debug Mode](#debug-mode)
- [Performance Tuning](doc/PERFORMANCE_TUNING.md)

## Orchestrator Integration

Acexy integrates with acestream-orchestrator to provide dynamic engine management and intelligent load balancing. This is the recommended deployment model for production environments.

### Key Capabilities

- **Dynamic Engine Pools**: Automatically manages multiple acestream engine instances
- **Intelligent Load Balancing**: Distributes streams across engines based on configurable capacity limits
- **Automatic Provisioning**: Provisions new engine containers on-demand when capacity is reached
- **High Availability**: Automatic failover and health monitoring with graceful degradation
- **Stream Multiplexing**: Multiple clients can consume the same stream simultaneously

### How It Works

1. Client requests a stream from acexy proxy
2. Proxy queries orchestrator for available engine with capacity
3. Orchestrator selects engine with fewest active streams (prioritizing empty engines)
4. If no engines have capacity, orchestrator provisions a new engine container
5. Proxy establishes stream connection to selected engine
6. Stream lifecycle events are reported back to orchestrator for monitoring

### Fallback Mode

When orchestrator is unavailable or not configured, acexy automatically falls back to single-engine mode using `ACEXY_HOST` and `ACEXY_PORT` configuration.

## Architecture

Acexy is an **orchestrator-first stateless proxy** that wraps the [AceStream middleware HTTP API](https://docs.acestream.net/developers/start-playback/#using-middleware), supporting both HLS and MPEG-TS playback.

### Key Design Principles

1. **Stateless Proxy**: Each request is independent with its own unique PID
2. **Orchestrator-First**: Smart engine selection and dynamic provisioning
3. **High Concurrency**: Optimized for handling many simultaneous streams
4. **No Multiplexing**: Simplified architecture - let external proxies handle multiple clients

### How It Works

1. **Client Request**: Client requests a stream via `/ace/getstream?id=<stream-id>`
2. **Engine Selection**: Orchestrator selects the best available engine based on:
   - Current load (streams per engine)
   - Engine health status
   - VPN forwarding status (prioritized for better performance)
   - Last stream usage (distributes load evenly)
3. **Stream Setup**: Proxy requests stream from selected engine with unique PID
4. **Streaming**: Proxy forwards stream data directly to client (stateless passthrough)
5. **Tracking**: Orchestrator tracks stream lifecycle for monitoring and cleanup

### Benefits Over Direct AceStream Access

- **Dynamic Scaling**: Automatic engine provisioning when capacity is reached
- **Intelligent Load Balancing**: Distributes streams across healthy engines
- **High Availability**: Automatic failover and health monitoring
- **Simplified Client Integration**: Single endpoint for all streams
- **Performance Optimization**: Prioritizes faster engines (VPN-forwarded)

## Quick Start

### Using Docker Compose (Recommended)

The recommended deployment uses Docker Compose to run acexy with orchestrator integration:

```shell
wget https://raw.githubusercontent.com/krinkuto11/acexy/refs/heads/main/docker-compose.yml
docker compose up -d
```

This starts:
- acexy proxy on port 8080
- orchestrator on port 8000
- Automatic acestream engine provisioning

### Accessing Streams

Acexy provides a single endpoint `/ace/getstream` compatible with the standard [AceStream Middleware/HTTP API](https://docs.acestream.net/developers/api-reference/):

```
http://127.0.0.1:8080/ace/getstream?id=<acestream-id>
```

Example:
```
http://127.0.0.1:8080/ace/getstream?id=dd1e67078381739d14beca697356ab76d49d1a2
```

Open this URL in any media player that supports HTTP streaming (VLC, mpv, etc.).

Each request gets its own stream instance with a unique PID, ensuring no conflicts between clients.

### Single Engine Mode

For backwards compatibility or simple setups, acexy can connect directly to a single AceStream engine:

```shell
docker run --network host ghcr.io/krinkuto11/acexy-orchestrator
```

In this mode, orchestrator integration is disabled and acexy uses `ACEXY_HOST` and `ACEXY_PORT` configuration.

### Multi-Architecture Support

Acexy Docker images are built for multiple architectures using native runners for optimal performance:

- **AMD64 (x86_64)**: Built on native AMD64 runners
- **ARM64 (aarch64)**: Built on native ARM64 runners for Raspberry Pi 3/4/5, AWS Graviton, and other ARM64 devices
- **ARMv7 (32-bit ARM)**: Built via QEMU emulation on ARM64 runners for Raspberry Pi 2 and older ARM devices

Docker automatically selects the correct image for your platform:

```shell
# This command works on all supported architectures
docker pull ghcr.io/krinkuto11/acexy-orchestrator:latest
```

The multi-arch manifest ensures optimal performance by using native builds whenever possible, falling back to emulation only for ARMv7.

## Configuration

### Core Orchestrator Settings

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `ACEXY_ORCH_URL` | Orchestrator API base URL. Leave empty to disable orchestrator integration. | _(empty)_ |
| `ACEXY_ORCH_APIKEY` | API key for orchestrator authentication | _(empty)_ |
| `ACEXY_MAX_STREAMS_PER_ENGINE` | Maximum streams per engine when using orchestrator | `1` |
| `ACEXY_CONTAINER_ID` | Container ID for orchestrator identification (auto-detected in Docker) | _(auto-detected)_ |

### Fallback Engine Settings

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `ACEXY_HOST` | AceStream engine host (used when orchestrator unavailable) | `localhost` |
| `ACEXY_PORT` | AceStream engine port (used when orchestrator unavailable) | `6878` |
| `ACEXY_SCHEME` | HTTP scheme for AceStream middleware | `http` |

### Proxy Settings

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `ACEXY_LISTEN_ADDR` | Address where acexy listens | `:8080` |
| `ACEXY_BUFFER` | Stream buffer size (prevents frame drops) | `4.2MiB` |
| `ACEXY_NO_RESPONSE_TIMEOUT` | Timeout waiting for AceStream middleware response | `1s` |
| `ACEXY_EMPTY_TIMEOUT` | Timeout to close stream after receiving empty data | `1m` |

### Optional Features

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `ACEXY_M3U8` | Enable HLS/M3U8 mode (experimental) | `false` |
| `ACEXY_M3U8_STREAM_TIMEOUT` | Stream timeout in M3U8 mode | `60s` |
| `DEBUG_MODE` | Enable detailed performance logging | `false` |
| `DEBUG_LOG_DIR` | Directory for debug logs (JSON Lines format) | `./debug_logs` |

For complete list of options, run: `acexy -help`

## Advanced Topics

### Stream Buffer Tuning

Acexy uses a configurable buffer size to smooth out stream delivery and prevent frame drops. The buffer helps handle:

- **Network jitter**: Temporary variations in network latency
- **Bursty data delivery**: When AceStream sends data in irregular chunks
- **Client read speed variations**: Different media players consume data at varying rates

**Default Configuration (Recommended)**

The default buffer size of 4.2MiB is optimized for most use cases:

```yaml
services:
  acexy:
    environment:
      - ACEXY_BUFFER=4.2MiB
```

**When to Adjust Buffer Size**

- **Lower buffer (1-2MiB)**: For memory-constrained environments or low-bitrate streams
- **Higher buffer (8-16MiB)**: For high-bitrate 4K streams or unreliable networks
- **Very high buffer (32MiB+)**: Only for extreme cases with very poor network conditions

**Note**: Larger buffers consume more memory per stream. With default settings, each concurrent stream uses approximately 4.2MiB of buffer memory.

For detailed performance tuning guidance, see [doc/PERFORMANCE_TUNING.md](doc/PERFORMANCE_TUNING.md).

### Debug Mode

Enable comprehensive debug logging for troubleshooting and performance analysis:

```yaml
services:
  acexy:
    environment:
      - DEBUG_MODE=true
      - DEBUG_LOG_DIR=/app/debug_logs
    volumes:
      - ./debug_logs:/app/debug_logs
```

Debug logs capture:
- HTTP request timing and slow request detection
- Engine selection decisions
- Provisioning operations and retries
- Orchestrator health status
- Stream lifecycle events
- Performance bottlenecks

For complete documentation, see [doc/DEBUG_MODE.md](doc/DEBUG_MODE.md).
