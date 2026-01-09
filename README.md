# AceStream Orchestrator

**Version 1.5.1**

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

### Standalone (No VPN)

```bash
cp .env.example .env
# Edit .env: Set API_KEY and configure port ranges
docker-compose up -d
```

### With VPN (Single)

```bash
cp .env.example .env
# Edit .env: Set API_KEY and VPN settings
# Edit docker-compose.gluetun.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun.yml up -d
```

### With Redundant VPN (High Availability)

```bash
cp .env.example .env
# Edit .env: Set API_KEY and redundant VPN settings
# Edit docker-compose.gluetun-redundant.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun-redundant.yml up -d
```

**Dashboard**: Access at `http://localhost:8000/panel`

## Requirements

- Docker 24+ with access to Docker socket or docker:dind
- Free ports within the ranges defined in `.env`
- For VPN mode: Valid VPN credentials (ProtonVPN, NordVPN, etc.)

## Core Features

- **Stream Multiplexing Proxy**: Native proxy supporting multiple clients per stream
  - Automatic stream sharing across concurrent clients
  - Redis-backed ring buffer for efficient data distribution
  - Heartbeat-based client tracking and cleanup
  - Configurable buffer and grace periods
  - Load balanced engine selection
  - Seamless failover on engine unavailability
- **Stream Loop Detection**: Automatically detect and stop streams that are looping (no new data)
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
  - Standalone deployment
  - Single VPN mode setup
  - Redundant VPN mode (high availability)
  - Production checklist
  - Monitoring and troubleshooting

- **[Configuration Reference](docs/CONFIG.md)** - All environment variables explained
- **[.env.example](.env.example)** - Example configuration file with comments

### Architecture & Operations

- **[Architecture & Operations](docs/ARCHITECTURE.md)** - System design and internal operations
  - System overview and components
  - Typical workflows
  - Database schema
  - State management

- **[API Documentation](docs/API.md)** - Complete API endpoint reference
- **[Events](docs/EVENTS.md)** - Event contracts and stream lifecycle

### Engine Configuration

- **[Engine Variants](docs/ENGINE_VARIANTS.md)** - Engine variants for different architectures
  - AMD64 variants (krinkuto11, jopsis)
  - ARM32 and ARM64 variants
  - Configuration methods
  - P2P port handling
  - **Custom Engine Variants** (v1.2.0)
    - Configure 35+ AceStream parameters via UI
    - Template management with 10 slots
    - Auto-load on enable
    - Active template persistence
    - Import/export capabilities
    - Platform auto-detection
    - ARM version selection
    - Per-parameter enable/disable
    - VPN-aware configuration

### VPN Integration

- **[Gluetun Integration](docs/GLUETUN_INTEGRATION.md)** - Complete VPN integration guide
  - Overview and benefits
  - Single VPN mode setup
  - Redundant VPN mode (high availability)
  - Port configuration
  - Forwarded engines explained
  - Docker Compose examples

- **[Gluetun Failure & Recovery](docs/GLUETUN_FAILURE_RECOVERY.md)** - VPN failure scenarios with diagrams
  - VPN connection loss
  - Container restart scenarios
  - Redundant VPN failover
  - Port forwarding loss
  - Monitoring and alerts

- **[Emergency Mode](docs/EMERGENCY_MODE.md)** - Automatic emergency mode for VPN failures
  - Immediate failover when one VPN fails
  - Automatic cleanup of failed VPN's engines
  - Single-VPN operation with reduced capacity
  - Automatic recovery and capacity restoration
  - API and monitoring integration

### Monitoring & Health

- **[Health Monitoring](docs/HEALTH_MONITORING.md)** - Health check system
  - Engine health monitoring
  - Usage tracking
  - Stale stream detection
  - Cache cleanup process

- **[Stats Caching](docs/STATS_CACHING.md)** - Performance optimization via caching
  - Cached endpoints and TTLs
  - Cache invalidation strategy
  - Performance impact and monitoring
  - Configuration and troubleshooting

- **[Dashboard Guide](docs/PANEL.md)** - Web dashboard features and usage
  - Modern React interface
  - Real-time monitoring
  - Engine management
  - VPN status display

### Security

- **[Security Considerations](docs/SECURITY.md)** - Security best practices
  - API key protection
  - Network security
  - VPN credential management

## Quick Reference

### Essential Environment Variables

```bash
# Security (REQUIRED)
API_KEY=your-secure-api-key

# Engine Configuration
ENGINE_VARIANT=krinkuto11-amd64
MIN_REPLICAS=3
MAX_REPLICAS=20

# Port Ranges
PORT_RANGE_HOST=19000-19999
ACE_HTTP_RANGE=40000-44999
ACE_HTTPS_RANGE=45000-49999

# VPN (Optional - for VPN modes)
VPN_MODE=single                    # or 'redundant'
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_API_PORT=8001
```

See [Configuration Reference](docs/CONFIG.md) for all options.

### Core API Endpoints

```bash
# Provisioning
POST /provision/acestream             # Start new engine

# Events
POST /events/stream_started         # Register stream
POST /events/stream_ended           # Unregister stream

# Status
GET /engines                        # List engines with health status
GET /streams?status=started         # List active streams
GET /vpn/status                     # VPN status (if configured)

# Stream Loop Detection (v1.6.0)
GET /stream-loop-detection/config   # Get loop detection settings
POST /stream-loop-detection/config  # Update loop detection settings

# Monitoring
GET /health                         # Service health
GET /metrics                        # Prometheus metrics
```

See [API Documentation](docs/API.md) for complete details.

## Docker Compose Files

- **[docker-compose.yml](docker-compose.yml)** - Standalone mode (no VPN)
- **[docker-compose.gluetun.yml](docker-compose.gluetun.yml)** - Single VPN mode
- **[docker-compose.gluetun-redundant.yml](docker-compose.gluetun-redundant.yml)** - Redundant VPN mode

## Testing

Run the comprehensive test suite:

```bash
# Unit tests
python -m pytest tests/

# Manual testing guide
python tests/manual_test_forwarded.py
```

See [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) for more details.

## Support & Contributing

For issues, feature requests, or contributions, please visit the [GitHub repository](https://github.com/krinkuto11/acestream-orchestrator).

## License

See LICENSE file for details.


