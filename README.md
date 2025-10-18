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
docker-compose up --build
```

Open the dashboard at `http://localhost:8000/panel`.

> **Note:** Use `--build` flag to ensure you get the latest panel version. The React dashboard is built during the Docker image build process.

![Dashboard Overview](docs/images/dashboard_overview.png)

## API Endpoints

Core endpoints: `/provision`, `/provision/acestream`, `/events/*`, `/engines`, `/streams`, `/streams/{id}/stats`, `/containers/{id}`, `/by-label`, `/vpn/status`, `/metrics`, `/panel`.

## Features

### Debug Mode (NEW)
- **Comprehensive logging** for troubleshooting performance issues during stress situations
- **Session-based logs** with timing metrics and automatic stress detection
- **Multiple log categories** (provisioning, health, VPN, circuit breaker, stress events)
- **Correlation support** with proxy repository logs for end-to-end debugging
- See [Debug Mode Documentation](docs/DEBUG_MODE.md) for details

### Health Monitoring
- **Intelligent detection** of hanging engines via `/server/api?api_version=3&method=get_status`
- **Background monitoring** every 30 seconds with status indicators
- **Visual health indicators** in dashboard (green/red/gray status)

### Usage Tracking
- **Last stream usage** timestamps for each engine
- **Idle time tracking** for proxy load balancing decisions
- **Real-time usage patterns** in dashboard

### Modern React Dashboard
- **React + Material-UI** for high performance and professional design
- **Real-time KPIs** for engines, streams, health, and VPN status
- **Enhanced engine cards** with health status and usage info
- **Advanced stream analytics** with interactive Chart.js visualizations
- **VPN monitoring panel** with connection status and port forwarding
- **Performance optimizations** with React hooks, localStorage caching, and efficient rendering

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
  static/
    panel/            # Built dashboard (generated during Docker build)
    panel-react/      # React source code
docker-compose.yml
Dockerfile
requirements.txt
.env.example
```

# Documentation
* [README](README.md)
* [Overview](docs/OVERVIEW.md)
* [Configuration](docs/CONFIG.md)
* [API](docs/API.md)
* [Events](docs/EVENTS.md)
* [Panel](docs/PANEL.md)
* [Debug Mode](docs/DEBUG_MODE.md) **← NEW**
* [Health Monitoring](docs/HEALTH_MONITORING.md)
* [Database Schema](docs/DB_SCHEMA.md)
* [Deployment](docs/DEPLOY.md)
* [Operations](docs/OPERATIONS.md)
* [Troubleshooting](docs/TROUBLESHOOTING.md)
* [Security](docs/SECURITY.md)
* [Proxy Integration](docs/PROXY_INTEGRATION.md)
* [Proxy Debug Mode Prompt](docs/PROXY_DEBUG_MODE_PROMPT.md) **← NEW**
* [Gluetun VPN Integration](docs/GLUETUN_INTEGRATION.md)
* [Gluetun Port Fix](docs/GLUETUN_PORT_FIX.md)


