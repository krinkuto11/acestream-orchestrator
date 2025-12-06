# Architecture & Internal Operations

This document describes the internal architecture, operations, and data structures of the AceStream Orchestrator.

## Table of Contents

- [System Overview](#system-overview)
- [Architecture Components](#architecture-components)
- [Typical Workflow](#typical-workflow)
- [Operations](#operations)
- [Database Schema](#database-schema)
- [State Management](#state-management)

---

## System Overview

The AceStream Orchestrator is a dynamic container orchestration service that provisions AceStream engine containers on-demand, monitors their health, collects stream statistics, and provides a dashboard for operational visibility.

### Objective

Launch AceStream containers on-demand to serve streams requested by a proxy. The orchestrator provides intelligent health monitoring, usage tracking, and a modern dashboard interface for operational visibility.

### Core Functionality

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

---

## Architecture Components

### 1. Orchestrator API

**Technology**: FastAPI over Uvicorn

**Responsibilities**:
- REST API for provisioning and events
- Health monitoring endpoints
- VPN status queries
- Prometheus metrics exposure

**Key Endpoints**:
- `POST /provision/acestream` - Start new engine
- `POST /events/stream_started` - Register stream
- `POST /events/stream_ended` - Unregister stream
- `GET /engines` - List engines with health status
- `GET /streams` - List active streams
- `GET /vpn/status` - VPN status (if configured)
- `GET /metrics` - Prometheus metrics

### 2. Docker Host

**Options**:
- `docker:dind` in Compose (for containerized environments)
- Host Docker via `DOCKER_HOST` environment variable
- Direct access to Docker socket at `/var/run/docker.sock`

**Usage**:
- Container lifecycle management
- Network configuration
- Port allocation
- Health checks

### 3. Dashboard

**Technology**: React 18 + Material-UI 5

**Features**:
- Modern responsive web interface at `/panel`
- Real-time monitoring of engines and streams
- VPN status display
- Health indicators with color coding
- Stream analytics with charts

**Location**: `/app/static/panel/` (built from `/app/static/panel-react/`)

### 4. Health Monitor

**Technology**: Background asyncio service

**Responsibilities**:
- Checks engine health every 30 seconds
- Uses AceStream's `/server/api?api_version=3&method=get_status` endpoint
- Updates engine health status in real-time
- Detects hanging or unresponsive engines

**Health States**:
- `healthy` - Engine responding normally
- `unhealthy` - Engine not responding or errors
- `unknown` - Status pending or unable to determine

### 5. VPN Integration

**Technology**: Gluetun monitoring service

**Responsibilities**:
- Monitors Gluetun container health continuously
- Queries port forwarding information
- Manages forwarded engine assignment
- Handles VPN reconnection events
- Implements double-check via engine connectivity

**Supported Modes**:
- **Single VPN**: One Gluetun container
- **Redundant VPN**: Two Gluetun containers with automatic failover

### 6. Collector Service

**Technology**: Background asyncio task

**Responsibilities**:
- **PRIMARY** mechanism for detecting stale/ended streams (via stat URL polling)
- Polls stream statistics every `COLLECT_INTERVAL_S` seconds (default: 2s for quick detection)
- Collects data from engine `stat_url` endpoints
- Detects stale streams when engine returns "unknown playback session id"
- Stores statistics in database and memory

**Data Collected**:
- Download/upload speeds
- Peer connections
- Data transferred
- Stream status

### 7. Autoscaler

**Technology**: Background service

**Responsibilities**:
- Maintains minimum replica count (`MIN_REPLICAS`)
- Provisions new engines when needed
- Distributes engines across VPNs (redundant mode)
- Cleans up idle engines (if `AUTO_DELETE=true`)

### 8. Proxy Service

**External Component** (communicates with orchestrator)

**Responsibilities**:
- Requests engines from orchestrator
- Initiates playback on engines
- Sends stream events to orchestrator
- Monitors stream health

---

## Typical Workflow

### 1. Engine Provisioning

```
1. Proxy → POST /provision/acestream (no engine available)
2. Orchestrator allocates ports
3. Orchestrator checks VPN health (if configured)
4. Orchestrator starts container with proper configuration
5. Health monitor begins checking engine every 30s
6. Orchestrator → Response with engine details
```

### 2. Stream Lifecycle

```
1. Proxy initiates playback on engine
2. Proxy obtains stat_url and command_url from engine
3. Proxy → POST /events/stream_started
4. Orchestrator records stream and updates usage tracking
5. Collector begins polling stat_url every 5s
6. Dashboard displays real-time stream analytics
7. When playback ends:
   - Proxy → POST /events/stream_ended
   - Orchestrator marks stream as ended
   - If last stream on engine: cache cleanup triggered
   - If AUTO_DELETE=true: engine deleted after grace period
```

### 3. Health Monitoring

```
1. Health monitor runs in background (every 30s)
2. For each engine:
   - GET /server/api?api_version=3&method=get_status
   - Update health_status based on response
   - Record last_health_check timestamp
3. Dashboard displays health status with color coding
4. Unhealthy engines can be identified and handled
```

### 4. Stale Stream Detection

```
1. Collector polls stream stat_url
2. Engine returns: {"response": null, "error": "unknown playback session id"}
3. Collector detects stale stream
4. Automatically triggers stream_ended event
5. Cache cleanup and container management as needed
```

### 5. VPN Monitoring (if configured)

```
1. VPN monitor runs in background (every 5s)
2. Checks Gluetun container health
3. If unhealthy:
   - Double-checks via engine connectivity
   - Hides engines on unhealthy VPN
   - Routes new traffic to healthy VPN (redundant mode)
4. If recovery detected:
   - Restarts engines on recovered VPN (if configured)
   - Resumes normal operation
5. Updates VPN status in dashboard
```

---

## Operations

### Startup

1. **Database Initialization**: Creates tables if not exist
2. **Reindex**: Discovers existing managed containers
   - Reads Docker labels to restore state
   - Identifies forwarded engines
   - Rebuilds port allocation maps
3. **Service Startup**: Launches background services
   - Collector service
   - Health monitor
   - VPN monitor (if configured)
   - Autoscaler
4. **Initial Provisioning**: Ensures `MIN_REPLICAS` are running

### Autoscaling

**Trigger**: Runs every `AUTOSCALE_INTERVAL_S` seconds

**Process**:
1. Check current engine count
2. Compare with `MIN_REPLICAS`
3. If below minimum:
   - Provision new engines
   - Distribute across VPNs (redundant mode)
   - Assign forwarded status if needed
4. If `AUTO_DELETE=true`:
   - Identify idle engines past grace period
   - Stop and remove containers
   - Free allocated ports

### Stats Collection

**Trigger**: Every `COLLECT_INTERVAL_S` seconds (default: 2s)

**Process**:
1. Fetch all active streams from database
2. For each stream:
   - GET request to `stat_url`
   - Parse statistics data
   - Check for stale stream indicators (primary stream state management)
   - Store in database and memory
3. Limit stored samples to `STATS_HISTORY_MAX`

### Garbage Collection

**Manual Trigger**: `POST /gc` endpoint

**Automatic**: Via `AUTO_DELETE=true`

**Process**:
1. Identify streams marked for deletion
2. Stop associated containers
3. Remove from state management
4. Free allocated resources
5. Backoff retry: 1s, 2s, 3s on failure

### Backups

**Database**: Copy `orchestrator.db` with your backup policy

**Recommended**:
```bash
# Daily backup
cp orchestrator.db backups/orchestrator-$(date +%Y%m%d).db

# Rotate old backups (keep 7 days)
find backups/ -name "orchestrator-*.db" -mtime +7 -delete
```

---

## Database Schema

The orchestrator uses SQLite with SQLAlchemy ORM.

### Tables

#### 1. `engines`

Stores information about managed AceStream engine containers.

```sql
CREATE TABLE engines (
    engine_key TEXT PRIMARY KEY,         -- Unique identifier (container_id:port)
    container_id TEXT NOT NULL,          -- Docker container ID
    container_name TEXT,                 -- Container name
    host TEXT NOT NULL,                  -- Hostname (container name or Gluetun name)
    port INTEGER NOT NULL,               -- External HTTP port
    labels JSON,                         -- Docker labels
    vpn_container TEXT,                  -- VPN container name (if using VPN)
    forwarded BOOLEAN DEFAULT FALSE,     -- Is this the forwarded engine?
    health_status TEXT,                  -- healthy/unhealthy/unknown
    last_health_check TIMESTAMP,         -- Last health check time
    last_stream_usage TIMESTAMP,         -- Last stream activity
    last_cache_cleanup TIMESTAMP,        -- Last cache cleanup
    cache_size_bytes INTEGER,            -- Cache size at last cleanup
    first_seen TIMESTAMP NOT NULL,       -- When engine was first registered
    last_seen TIMESTAMP NOT NULL         -- Last update time
);
```

#### 2. `streams`

Stores information about active and ended streams.

```sql
CREATE TABLE streams (
    id TEXT PRIMARY KEY,                 -- Stream ID (UUID)
    engine_key TEXT NOT NULL,            -- Foreign key to engines
    key_type TEXT NOT NULL,              -- content_id/infohash/url/magnet
    key TEXT NOT NULL,                   -- Stream content key
    playback_session_id TEXT NOT NULL,   -- AceStream session ID
    stat_url TEXT NOT NULL,              -- Statistics endpoint URL
    command_url TEXT NOT NULL,           -- Command endpoint URL
    is_live BOOLEAN NOT NULL,            -- Live vs VOD stream
    started_at TIMESTAMP NOT NULL,       -- Stream start time
    ended_at TIMESTAMP,                  -- Stream end time
    status TEXT NOT NULL                 -- started/ended
);

CREATE INDEX idx_stream_engine ON streams(engine_key);
CREATE INDEX idx_stream_status ON streams(status);
```

#### 3. `stream_stats`

Stores time-series statistics for streams.

```sql
CREATE TABLE stream_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id TEXT NOT NULL,             -- Foreign key to streams
    ts TIMESTAMP NOT NULL,               -- Timestamp of sample
    peers INTEGER,                       -- Number of connected peers
    speed_down INTEGER,                  -- Download speed (bytes/s)
    speed_up INTEGER,                    -- Upload speed (bytes/s)
    downloaded INTEGER,                  -- Total downloaded (bytes)
    uploaded INTEGER,                    -- Total uploaded (bytes)
    status TEXT                          -- Stream status
);

CREATE INDEX idx_stats_stream ON stream_stats(stream_id);
CREATE INDEX idx_stats_ts ON stream_stats(ts);
```

### Initial Load

**On Startup**:
1. Creates tables if they don't exist
2. Runs reindex to discover existing containers
3. Reads Docker labels to restore port allocations
4. Rebuilds in-memory state from database
5. Identifies forwarded engines from labels

**Reindex Process**:
- Queries Docker for containers with `CONTAINER_LABEL`
- Extracts port information from labels
- Adds to in-memory state
- Updates database with current status
- Identifies and restores forwarded engine status

---

## State Management

### In-Memory State

The orchestrator maintains in-memory state for fast access:

```python
class State:
    engines: Dict[str, EngineState]           # Container ID → Engine state
    forwarded_engine_id: Optional[str]        # Current forwarded engine
    port_allocator: PortAllocator             # Port allocation manager
    vpn_state: Optional[VPNState]             # VPN status (if configured)
```

### Engine State

```python
class EngineState:
    container_id: str
    container_name: str
    host: str                                 # Hostname for accessing engine
    port: int                                 # External HTTP port
    vpn_container: Optional[str]              # VPN container (if using VPN)
    forwarded: bool                           # Is forwarded engine?
    health_status: str                        # healthy/unhealthy/unknown
    last_health_check: Optional[datetime]
    last_stream_usage: Optional[datetime]
    last_cache_cleanup: Optional[datetime]
    cache_size_bytes: Optional[int]
    streams: List[str]                        # Active stream IDs
```

### Synchronization

- **Database**: Persistent storage for engines, streams, and stats
- **In-Memory**: Fast access for API requests and monitoring
- **Docker Labels**: Container metadata for reindex on restart

**Write Flow**:
1. Update in-memory state
2. Apply changes to Docker (if needed)
3. Persist to database
4. Return response to client

**Read Flow**:
1. Read from in-memory state (fast)
2. Fall back to database if needed
3. Reindex from Docker if state is inconsistent

---

## Related Documentation

- [API Documentation](API.md) - API endpoint reference
- [Configuration](CONFIG.md) - Environment variable guide
- [Deployment](DEPLOY.md) - Deployment instructions
- [Health Monitoring](HEALTH_MONITORING.md) - Health check details
- [Gluetun Integration](GLUETUN_INTEGRATION.md) - VPN integration

## Event Logging System

The event logging system provides transparent tracking of significant operational events throughout the application lifecycle.

### Purpose

Unlike debug logs which are for development troubleshooting, the event logging system captures user-facing operational events that provide:
- **Transparency**: Clear visibility into what the orchestrator is doing
- **Traceability**: Historical record of important operations and state changes
- **Monitoring**: Real-time awareness of system health and activities

### Architecture

**Components:**
1. **EventLogger Service** (`app/services/event_logger.py`)
   - Central service for logging and retrieving events
   - Automatic cleanup of old events (>10,000 total or >30 days old)
   - SQLite-backed persistent storage

2. **Database Model** (`EventRow` in `app/models/db_models.py`)
   - Stores event metadata: timestamp, type, category, message
   - Optional associations: container_id, stream_id
   - JSON details field for additional structured data

3. **API Endpoints** (`app/main.py`)
   - `GET /events` - Query events with filtering and pagination
   - `GET /events/stats` - Event statistics and counts
   - `POST /events/cleanup` - Manual cleanup trigger (protected)

4. **UI Integration** (`EventsPage.jsx`)
   - Color-coded event display by type
   - Real-time updates (5s refresh)
   - Filtering by event type
   - Configurable display limits

### Event Types

Events are categorized into five types, each with a distinct color in the UI:

1. **Engine Events** (🔧 Blue)
   - Engine provisioning and deletion
   - Logged in: `provision_acestream()`, `delete()`

2. **Stream Events** (📺 Green)
   - Stream start and end lifecycle
   - Logged in: `ev_stream_started()`, `ev_stream_ended()`

3. **VPN Events** (🔒 Purple)
   - VPN connection, disconnection, and recovery
   - Logged in: `VpnContainerMonitor.update_health_status()`

4. **Health Events** (💊 Yellow)
   - Health check warnings and circuit breaker states
   - Logged in: `HealthMonitor._monitor_loop()`, `CircuitBreaker` state changes

5. **System Events** (⚡ Gray)
   - Auto-scaling operations
   - Logged in: `ensure_minimum()` and other autoscaler operations

### Integration Points

The event logger is integrated throughout the application at key decision points:

```python
from .services.event_logger import event_logger

# Engine provisioning
event_logger.log_event(
    event_type="engine",
    category="created",
    message=f"AceStream engine provisioned on port {port}",
    details={"host_http_port": port, "image": image},
    container_id=container_id
)

# Health warnings
event_logger.log_event(
    event_type="health",
    category="warning",
    message=f"High proportion of unhealthy engines: {unhealthy}/{total}",
    details={"unhealthy_count": unhealthy, "total_engines": total}
)
```

### Data Retention

Events are automatically cleaned up to prevent unbounded growth:
- **Count Limit**: Maximum 10,000 events retained
- **Age Limit**: Events older than 30 days are deleted
- **Cleanup Trigger**: Runs automatically after each new event
- **Manual Cleanup**: Available via `/events/cleanup` endpoint

### UI Features

The Events page in the React dashboard provides:
- **Statistics Cards**: Count by event type at the top
- **Filtering**: Filter by event type (all, engine, stream, vpn, health, system)
- **Display Limit**: Configurable in settings (50, 100, 200, 500 events)
- **Details Expansion**: Click to view structured event details
- **Color Coding**: Visual distinction between event types
- **Timestamps**: Relative time display (e.g., "2 minutes ago")
- **Auto-refresh**: Updates every 5 seconds

### Performance Considerations

- Events are logged asynchronously to avoid blocking main operations
- Database queries use indexes on `timestamp` and `event_type` columns
- Automatic cleanup prevents database bloat
- UI pagination limits data transfer
