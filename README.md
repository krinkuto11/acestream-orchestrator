# Acestream Orchestrator

Dynamic orchestration service for Acestream engines with health monitoring, usage tracking, and a web dashboard.

## What it does

Provisions Acestream engine containers on-demand, monitors their health, collects stream statistics, and provides a dashboard for operational visibility. Works with proxy services that request engines when needed.

## Quick Start

```bash
cp .env.example .env
docker-compose up --build
```

Access the dashboard at `http://localhost:8000/panel`

## Requirements

- Docker 24+ with docker:dind in compose (or direct access to docker socket)
- Free ports within the ranges defined in .env

## Core Features

- **Multiple Engine Variants**: Support for AMD64 and ARM architectures with optimized configurations
- **Health Monitoring**: Automatic detection of engine issues
- **Usage Tracking**: Track engine idle time for load balancing
- **Web Dashboard**: Real-time monitoring interface
- **VPN Integration**: Gluetun VPN support with port forwarding
- **Metrics**: Prometheus-compatible metrics endpoint

## Configuration

Copy `.env.example` to `.env` and adjust:
- `API_KEY`: Protect endpoints
- `MIN_REPLICAS`: Initial engine pool size
- `PORT_RANGE_HOST`: External port range
- `ENGINE_VARIANT`: Engine variant (krinkuto11-amd64, jopsis-amd64, jopsis-arm32, jopsis-arm64)

See [Configuration](docs/CONFIG.md) and [Engine Variants](docs/ENGINE_VARIANTS.md) for all options.

## API

Core endpoints:
- `POST /provision/acestream` - Start new engine
- `POST /events/stream_started` - Register stream
- `POST /events/stream_ended` - Unregister stream
- `GET /engines` - List engines with health status
- `GET /streams` - List active streams
- `GET /vpn/status` - VPN status
- `GET /metrics` - Prometheus metrics

See [API Documentation](docs/API.md) for details.

## Documentation

- [Configuration](docs/CONFIG.md) - Environment variables
- [Engine Variants](docs/ENGINE_VARIANTS.md) - Available engine variants and usage
- [API](docs/API.md) - Endpoint reference
- [Events](docs/EVENTS.md) - Event contracts
- [Security](docs/SECURITY.md) - Security considerations
- [Deployment](docs/DEPLOY.md) - Deployment guide


