

# Overview

Objective: launch AceStream containers on-demand to serve streams requested by a proxy. The orchestrator provides intelligent health monitoring, usage tracking, and a modern dashboard interface for operational visibility.

## Core Functionality

The orchestrator:
- **Provisions containers** with dynamic internal and external ports
- **Receives events** for `stream_started` and `stream_ended`  
- **Collects statistics** periodically from `stat_url`
- **Monitors health** of engines using native Acestream API endpoints
- **Tracks usage** patterns for intelligent engine selection
- **Persists data** in SQLite (engines, streams, statistics)
- **Exposes dashboard** with modern UI and real-time monitoring
- **Integrates VPN** monitoring with Gluetun support
- **Provides metrics** via Prometheus endpoints

## Architecture Components

- **Orchestrator API**: FastAPI over Uvicorn with health monitoring
- **Docker host**: `docker:dind` in Compose or host Docker via `DOCKER_HOST`
- **Dashboard**: Modern responsive web interface at `/panel`
- **Health Monitor**: Background service checking engine status every 30s
- **VPN Integration**: Gluetun monitoring with port forwarding support
- **Proxy**: Client that communicates with engines and orchestrator

## Enhanced Features

### 🩺 Health Monitoring
- **Smart detection** of hanging engines via `/server/api?api_version=3&method=get_status`
- **Background monitoring** every 30 seconds with automatic status updates
- **Visual indicators** in dashboard (healthy/unhealthy/unknown status)
- **API integration** with health data in all engine endpoints

### ⏱️ Usage Tracking  
- **Last stream usage** timestamps for intelligent engine selection
- **Idle time tracking** enables proxy load balancing decisions
- **Real-time patterns** displayed in dashboard with human-readable formatting

### 🎨 Modern Dashboard
- **Professional interface** with dark theme and responsive design
- **Real-time KPIs** showing engines, streams, health status, and VPN info
- **Enhanced analytics** with dual-axis charts for stream statistics
- **Visual health indicators** with intuitive color coding
- **VPN monitoring panel** with connection status and port forwarding

### 🌐 VPN Integration
- **Gluetun monitoring** with health checks and status tracking
- **Port forwarding** information for proxy configuration
- **Container status** monitoring with real-time updates

## Typical Workflow

1. **Proxy requests** `POST /provision/acestream` if no engine is available
2. **Orchestrator starts** container with `--http-port`, `--https-port` flags and host binding
3. **Health monitor** begins checking engine status every 30 seconds
4. **Proxy initiates** playback against `http://<host>:<host_http_port>/ace/manifest.m3u8?...&format=json`
5. **Proxy obtains** `stat_url` and `command_url` from engine and sends `POST /events/stream_started`
6. **Usage tracking** updates `last_stream_usage` timestamp for the engine
7. **Orchestrator collects** stats periodically from `stat_url`
8. **Dashboard displays** real-time engine health, usage patterns, and stream analytics
9. **When finished**, proxy sends `POST /events/stream_ended`
10. **If AUTO_DELETE=true**, orchestrator deletes the container after grace period

## Monitoring & Operations

### Dashboard Access
Access the modern dashboard at `http://localhost:8000/panel` featuring:
- Real-time engine health monitoring
- Stream analytics with historical charts  
- VPN status and port forwarding info
- Usage tracking for operational insights

### Health Monitoring
- Automatic detection of problematic engines
- Background health checks every 30 seconds
- Visual status indicators in dashboard
- API endpoints include health data

### VPN Integration
- Gluetun container monitoring
- Port forwarding status tracking
- Connection health verification
- Dashboard integration for operational visibility
