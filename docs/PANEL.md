# Acestream Orchestrator Dashboard

Route: `/panel`.

![Dashboard Overview](images/dashboard_overview.png)

## Overview

The Acestream Orchestrator Dashboard is a modern, professional web interface for monitoring and managing Acestream engines, streams, and VPN connections. The dashboard features a dark theme with responsive design optimized for operational visibility.

## Key Features

### 🖥️ Modern Dashboard Interface
- **Professional dark theme** with CSS variables for consistent styling
- **Responsive grid layout** that adapts to different screen sizes
- **Real-time updates** with configurable refresh intervals (2-30 seconds)
- **Card-based design** with modern typography and spacing
- **Visual health indicators** with intuitive color coding

### 📊 Key Performance Indicators (KPIs)
- **Engines**: Total number of managed engines
- **Active Streams**: Currently running streams
- **Healthy Engines**: Engines passing health checks
- **VPN Status**: Connection status (Connected/Disconnected/Disabled)
- **Last Update**: Timestamp of most recent data refresh

### 🔧 Engine Management
Enhanced engine cards displaying:
- **Engine name and port** (no more confusing labels)
- **Health status** with color-coded indicators:
  - 🟢 Green: Healthy engines responding properly
  - 🔴 Red: Unhealthy engines not responding
  - ⚪ Gray: Unknown status or pending health check
- **Active stream count** for each engine
- **Last usage tracking** with "time ago" formatting
- **Health check timing** showing when last checked
- **Delete engine** functionality with confirmation

### 🎬 Stream Analytics
- **Stream listing** with enhanced metadata
- **Real-time statistics** with dual-axis charts:
  - Download/Upload speeds (MB/s)
  - Peer connections
  - Historical data visualization
- **Stream controls**: Stop stream and delete engine buttons
- **Detailed session information** including:
  - Stream ID and session details
  - Content key and type
  - Engine assignment
  - Start time and duration
  - Direct links to statistics and command URLs

### 🌐 VPN Integration
Comprehensive VPN monitoring section:
- **VPN status**: Running, stopped, or not configured
- **Health monitoring**: Real-time connection health
- **Container information**: Gluetun container status
- **Forwarded port**: Current port forwarding configuration
- **Last check**: Timestamp of most recent VPN health check

## Configuration

### Connection Settings
- **Server URL**: Orchestrator base URL (default: http://localhost:8000)
- **API Key**: Bearer token for protected endpoints
- **Refresh Interval**: Auto-refresh frequency (2s, 5s, 10s, 30s)

### Authentication
For protected endpoints, provide your API key in the "API Key" field. The dashboard will automatically include the Bearer token in requests to protected endpoints.

## Health Monitoring

### Engine Health Checking
The dashboard implements intelligent health monitoring using the Acestream API endpoint `/server/api?api_version=3&method=get_status`. When engines hang or become unresponsive, this endpoint fails to respond, allowing automatic detection of problematic engines.

**Health Status Indicators:**
- **Healthy** (Green): Engine responding normally to API requests
- **Unhealthy** (Red): Engine not responding or returning errors
- **Unknown** (Gray): Health status pending or unable to determine

### Background Monitoring
- Health checks run automatically every 30 seconds
- Real-time status updates in the dashboard
- Visual indicators update immediately when status changes
- Historical health check timing displayed

## Stream Usage Tracking

The dashboard tracks when streams were last loaded into each engine, enabling intelligent engine selection:
- **Last usage timestamp** displayed with human-readable formatting
- **Usage patterns** help identify idle engines
- **Proxy integration** can use this data for load balancing
- **Automatic updates** when new streams start

## VPN Status Monitoring

When Gluetun VPN integration is configured, the dashboard provides comprehensive VPN monitoring:
- **Real-time connection status** with visual indicators
- **Health monitoring** of VPN container and connection
- **Port forwarding information** for proxy configuration
- **Container status** and last health check timing

## Technical Details

### CORS Support
The panel is served from the same host as the orchestrator. If you need to serve it separately, enable CORS in `main.py`.

### API Integration
The dashboard interfaces with the following endpoints:
- `GET /engines` - Engine listing with health data
- `GET /streams` - Active stream information
- `GET /vpn/status` - VPN status and configuration
- `GET /streams/{id}/stats` - Historical stream statistics
- `DELETE /containers/{id}` - Engine deletion (protected)

### Browser Compatibility
- Modern browsers with ES6+ support
- Responsive design for desktop and mobile
- Chart.js integration for data visualization
- Local storage for settings persistence
