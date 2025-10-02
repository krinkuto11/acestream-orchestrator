# Acestream Orchestrator + Dashboard

Modern orchestration platform for Acestream engines with intelligent health monitoring, usage tracking, and a professional dashboard interface.

**Key Features:**
- 🩺 **Smart Health Monitoring**: Automatic detection of hanging engines using Acestream API endpoints
- ⏱️ **Usage Tracking**: Track engine idle time for intelligent proxy selection
- 🎨 **Modern Dashboard**: Professional UI with real-time monitoring and VPN integration
- 🌐 **VPN Integration**: Comprehensive Gluetun VPN monitoring with port forwarding
- 📊 **Advanced Analytics**: Enhanced stream statistics with dual-axis charts

## Quick Start

```bash
cp .env.example .env
docker-compose up
```

Open the dashboard at `http://localhost:8000/panel`.

![Dashboard Overview](docs/images/dashboard_overview.png)

## Acexy Integration

This orchestrator works seamlessly with **[Acexy](acexy/)**, a high-performance Go proxy for AceStream streams. Together they provide:

- **Dynamic Engine Selection**: Acexy queries orchestrator for best available engine
- **Load Balancing**: Intelligent distribution across multiple engines  
- **Auto-Provisioning**: Orchestrator provisions engines on-demand
- **High Availability**: Graceful fallback and fault isolation

**Architecture Decision:** Acexy and Orchestrator are **intentionally separate services** for optimal performance, scalability, and reliability. See [Architecture Analysis](docs/RECOMMENDATION_SUMMARY.md) for details.

## API Endpoints

Core endpoints: `/provision`, `/provision/acestream`, `/events/*`, `/engines`, `/streams`, `/streams/{id}/stats`, `/containers/{id}`, `/by-label`, `/vpn/status`, `/metrics`, `/panel`.

## Features

### Health Monitoring
- **Intelligent detection** of hanging engines via `/server/api?api_version=3&method=get_status`
- **Background monitoring** every 30 seconds with status indicators
- **Visual health indicators** in dashboard (green/red/gray status)

### Usage Tracking
- **Last stream usage** timestamps for each engine
- **Idle time tracking** for proxy load balancing decisions
- **Real-time usage patterns** in dashboard

### Modern Dashboard
- **Professional dark theme** with responsive design
- **Real-time KPIs** for engines, streams, health, and VPN status
- **Enhanced engine cards** with health status and usage info
- **Advanced stream analytics** with dual-axis charts
- **VPN monitoring panel** with connection status and port forwarding

### VPN Integration
- **Gluetun integration** with health monitoring
- **Port forwarding status** for proxy configuration
- **Real-time connection monitoring** in dashboard

# Requirements

 - Docker 24+ and docker:dind in compose (or direct access to docker socket).

 - Python 3.12 in image.

 - Free ports within the ranges defined in .env.

# Structure

```md
app/
  main.py
  core/config.py
  models/{schemas.py,db_models.py}
  services/*.py
  static/panel/index.html
docker-compose.yml
Dockerfile
requirements.txt
.env.example
```

# Documentation

## Core Documentation
* [README](README.md)
* [Overview](docs/OVERVIEW.md)
* [Configuration](docs/CONFIG.md)
* [API](docs/API.md)
* [Events](docs/EVENTS.md)
* [Panel](docs/PANEL.md)

## Architecture & Integration
* **[Acexy-Orchestrator Analysis](docs/RECOMMENDATION_SUMMARY.md)** - Should Acexy and Orchestrator be merged?
* **[Architecture Comparison](docs/ARCHITECTURE_COMPARISON.md)** - Visual comparison of separate vs merged
* **[Integration Improvements](docs/INTEGRATION_IMPROVEMENTS.md)** - Practical improvements for better integration
* [Proxy Integration](docs/PROXY_INTEGRATION.md)

## Operations & Monitoring
* [Health Monitoring](docs/HEALTH_MONITORING.md)
* [Troubleshooting](docs/TROUBLESHOOTING.md)
* **[Troubleshooting Integration Issues](docs/TROUBLESHOOTING_INTEGRATION.md)** - Fix communication issues under load
* [Database Schema](docs/DB_SCHEMA.md)
* [Deployment](docs/DEPLOY.md)
* [Operations](docs/OPERATIONS.md)
* [Security](docs/SECURITY.md)
* [Gluetun VPN Integration](docs/GLUETUN_INTEGRATION.md)
* [Performance Optimizations](docs/PERFORMANCE.md)
* [Reliability Features](docs/RELIABILITY.md)


