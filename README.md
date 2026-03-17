# AceStream Orchestrator

**Version 1.6.0**

<img src="app/icons/favicon-96x96-dark.png" alt="AceStream Orchestrator Logo" width="96" height="96" />

Dynamic orchestration service for AceStream engines with health monitoring, usage tracking, VPN integration, and a modern web dashboard.
| Dashboard | Engines | Streams |
|---------|---------|---------|
| <img width="300" src="https://github.com/user-attachments/assets/02f40e0f-6629-4ff6-a1ba-599051fbd0dc" /> | <img width="300" src="https://github.com/user-attachments/assets/ebda1c53-782d-4e48-939b-d9e0c21283e2" /> | <img width="300" src="https://github.com/user-attachments/assets/2466b4e8-5f76-4d04-81a1-88ca1398692f" /> |

## What It Does

Provisions AceStream engine containers on-demand, monitors their health, collects stream statistics, and provides a dashboard for operational visibility.

**How to play a stream:**<br/>
In VLC or other player that supports playing a network stream:
```
<orchestrator_ip>:<port|8000>/ace/getstream?id=<id>
```

## Quick Start

The AceStream Orchestrator is **Zero-Config** out of the box. All settings are managed through the web dashboard.

### Standalone (No VPN)

```bash
docker-compose up -d
```

### With VPN (Single)

```bash
# Edit docker-compose.gluetun.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun.yml up -d
```

### With Redundant VPN (High Availability)

```bash
# Edit docker-compose.gluetun-redundant.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun-redundant.yml up -d
```

**Dashboard**: Access at `http://localhost:8000/panel`

Go to **Settings** to configure your API key, engine scaling, VPN integration, and more.

## Requirements

- Docker 24+ with access to Docker socket or docker:dind
- For VPN mode: Valid VPN credentials (ProtonVPN, NordVPN, etc.)

## Core Features

- **UI-Driven Configuration** (v1.6.0): Configure every aspect of the orchestrator from the web dashboard.
  - Zero-Config clean install support
  - Progressively disclosed Basic/Expert settings
  - JSON-backed persistent storage
- **Stream Multiplexing Proxy**: Native proxy supporting multiple clients per stream
  - Automatic stream sharing across concurrent clients
  - Redis-backed ring buffer for efficient data distribution
  - Heartbeat-based client tracking and cleanup
  - Configurable buffer and grace periods
  - Load balanced engine selection
  - Seamless failover on engine unavailability
  - **HLS Proxy Mode** (v1.6.0): Toggle between MPEG-TS and HLS streaming modes
    - Unified `/ace/getstream` endpoint for both modes
    - HLS support for krinkuto11-amd64 variant
    - Dynamic mode switching via Proxy Settings
    - See [HLS Proxy Documentation](docs/HLS_PROXY.md)
- **Stream Loop Detection** (v1.6.0): Automatically detect and stop streams that are looping (no new data)
  - Configurable threshold via UI
  - Monitors broadcast position (live_last) against real time
  - Automatic stream cleanup when threshold exceeded
- **Multiple Deployment Modes**: Standalone, single VPN, or redundant VPN with automatic failover
- **Emergency Mode**: Automatic failover and recovery when one VPN fails in redundant mode
- **Multiple Engine Variants**: Support for AMD64 and ARM architectures with optimized configurations
- **Custom Engine Variants** (v1.2.0): Configure individual AceStream parameters via UI with 35+ options
  - Template management with 10 template slots for saved configurations
  - Auto-load first available template when enabled
  - Active template persistence across container reboots
  - Edit, rename, import/export templates
  - Visual feedback for active templates
- **Stats Caching**: Intelligent caching of expensive API endpoints for improved UI responsiveness
  - 2-3 second TTL for cached endpoints
  - Automatic cache invalidation on state changes
  - Reduces Docker API load by ~60-70%
  - Transparent to API consumers
- **Reprovisioning Progress**: Real-time progress indicators when reprovisioning engines
- **Health Monitoring**: Automatic detection of engine issues with real-time status updates
- **Usage Tracking**: Track engine idle time for intelligent load balancing
- **Modern Web Dashboard**: Real-time monitoring interface built with React
- **VPN Integration**: Gluetun VPN support with port forwarding and automatic failover
- **Forwarded Engines**: Intelligent P2P port management for optimal connectivity
- **Prometheus Metrics**: Built-in metrics endpoint for monitoring and alerting
- **Stale Stream Detection**: Automatic cleanup of stopped streams
- **Cache Management**: Intelligent cache cleanup for resource optimization

## Documentation

- **[Deployment Guide](docs/DEPLOY.md)** - Complete deployment instructions for all modes
- **[Architecture & Operations](docs/ARCHITECTURE.md)** - System design and internal operations
- **[API Documentation](docs/API.md)** - Complete API endpoint reference
- **[Events](docs/EVENTS.md)** - Event contracts and stream lifecycle

### Engine & VPN
- **[Engine Variants](docs/ENGINE_VARIANTS.md)** - Engine variants for different architectures
- **[Gluetun Integration](docs/GLUETUN_INTEGRATION.md)** - Complete VPN integration guide
- **[Gluetun Failure & Recovery](docs/GLUETUN_FAILURE_RECOVERY.md)** - VPN failure scenarios with diagrams
- **[Emergency Mode](docs/EMERGENCY_MODE.md)** - Automatic emergency mode for VPN failures

## Management Endpoints

```bash
# Provisioning
POST /provision/acestream             # Start new engine

# Events
POST /events/stream_started         # Register stream
POST /events/stream_ended           # Unregister stream

# Status & Config
GET /engines                        # List engines with health status
GET /streams?status=started         # List active streams
GET /vpn/status                     # VPN status (if configured)
GET /settings/orchestrator          # Current core settings
GET /settings/vpn                   # Current VPN settings

# Monitoring
GET /health                         # Service health
GET /metrics                        # Prometheus metrics
```

See [API Documentation](docs/API.md) for complete details.

## Support & Contributing

For issues, feature requests, or contributions, please visit the [GitHub repository](https://github.com/krinkuto11/acestream-orchestrator).

## License

See LICENSE file for details.
