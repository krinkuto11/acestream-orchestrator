# Acestream Orchestrator Dashboard

Route: `/panel`.

![Dashboard Overview](images/dashboard_overview.png)

## Overview

The Acestream Orchestrator Dashboard is a modern, high-performance web interface built with **React** and **Material-UI** for monitoring and managing Acestream engines, streams, and VPN connections. The dashboard features a professional dark theme with responsive design optimized for operational visibility and performance.

### Technology Stack

- **React 18**: Modern React with hooks for efficient state management
- **Material-UI 5**: Comprehensive UI component library with excellent accessibility
- **Vite**: Fast build tool and development server
- **Chart.js**: Interactive charts for stream statistics
- **LocalStorage**: Caching for user preferences and settings

## Key Features

### 🖥️ Modern Dashboard Interface
- **Material-UI Components**: Professional, consistent design system
- **React Architecture**: Component-based structure for maintainability
- **Responsive Grid Layout**: Adapts seamlessly to different screen sizes
- **Real-time updates** with configurable refresh intervals (2-30 seconds)
- **Card-based design** with Material-UI cards and typography
- **Visual health indicators** with intuitive color coding and icons

### ⚡ Performance Optimizations
- **React Hooks**: Efficient state management with `useState`, `useEffect`, and `useCallback`
- **LocalStorage Caching**: User preferences and settings cached to reduce network requests
- **Optimized Rendering**: Components only re-render when necessary
- **Memoized Callbacks**: `useCallback` prevents unnecessary function recreations
- **Batch API Calls**: Single fetch for all data using `Promise.all`
- **Virtual DOM**: React's efficient diffing algorithm for minimal DOM updates

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

## Building the Dashboard

### Development

For development with hot-reload:

```bash
cd app/static/panel-react
npm install
npm run dev
```

The development server runs at http://localhost:3000 with API proxy configured.

### Production Build

To build the dashboard for production (local development only):

```bash
cd app/static/panel-react
npm install
npm run build
```

The built files are placed in `app/static/panel/` and served by FastAPI at `/panel`.

### Deployment

The dashboard is built automatically during Docker image creation. The Dockerfile includes steps to:
1. Install Node.js and npm
2. Copy the React source files from `app/static/panel-react/`
3. Run `npm install` and `npm run build`
4. The built files are placed in `app/static/panel/` and served by FastAPI

No additional setup is required when deploying via Docker - the dashboard is built as part of the image build process.

For non-Docker deployments:
1. Build the dashboard manually using `npm run build` in `app/static/panel-react/`
2. The server will automatically serve the built files from `app/static/panel/`

## Architecture

### Component Structure

```
src/
├── App.jsx                 # Main application component
├── main.jsx               # React entry point with theme
├── components/
│   ├── Header.jsx         # AppBar with settings
│   ├── KPICards.jsx       # Dashboard metrics
│   ├── EngineList.jsx     # Engine status cards
│   ├── StreamList.jsx     # Active streams
│   ├── VPNStatus.jsx      # VPN monitoring
│   └── StreamDetail.jsx   # Stream details with charts
├── hooks/
│   └── useLocalStorage.js # Persistent settings hook
└── utils/
    └── formatters.js      # Date/time/size formatting
```

### Performance Features

1. **Efficient State Management**: React hooks minimize unnecessary re-renders
2. **Memoized Callbacks**: `useCallback` prevents function recreation on every render
3. **Batched API Calls**: Single `Promise.all` fetch for all data sources
4. **Conditional Rendering**: Components only render when data is available
5. **LocalStorage Caching**: User preferences persisted across sessions
6. **Virtual DOM**: React's efficient diffing algorithm for minimal DOM updates
