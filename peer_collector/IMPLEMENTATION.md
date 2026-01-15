# Peer Collector Microservice - Implementation Summary

## Overview

This document summarizes the implementation of the peer collector microservice, a lightweight service that runs inside the Gluetun VPN container to collect torrent peer statistics for AceStream streams.

## Problem Statement

The orchestrator needs to sit outside the Gluetun VPN container, but peer statistics collection requires VPN connectivity to join BitTorrent swarms. The solution is to create a separate microservice that runs inside Gluetun.

## Solution Architecture

```
┌──────────────────────────────────────┐
│  Gluetun VPN Container               │
│  ┌────────────────────────────────┐  │
│  │  Peer Collector Microservice   │  │
│  │  - FastAPI app                 │  │
│  │  - libtorrent integration      │  │
│  │  - Redis caching               │  │
│  │  - Port: 8080                  │  │
│  └────────────────────────────────┘  │
│         ↑                             │
│         │ VPN Access                  │
│         ↓                             │
│    BitTorrent Swarms                 │
└──────────────────────────────────────┘
         ↑
         │ HTTP API
         ↓
┌──────────────────────────────────────┐
│  Orchestrator Container              │
│  - Calls peer collector via HTTP    │
│  - Displays peer data in UI          │
└──────────────────────────────────────┘
```

## Implementation Details

### 1. Peer Collector Microservice (`peer_collector/`)

**Files Created:**
- `Dockerfile` - Multi-stage build with distroless base and bundled Redis
- `requirements.txt` - FastAPI, uvicorn, httpx, redis, pydantic
- `app/main.py` - FastAPI application with health and peers endpoints
- `app/peer_stats.py` - libtorrent integration and peer collection logic
- `README.md` - Comprehensive documentation

**Key Features:**
- **Distroless**: Based on `gcr.io/distroless/python3-debian12:latest`
- **Bundled Redis**: Redis starts automatically for caching (same as orchestrator)
- **Lightweight**: Only essential dependencies included
- **VPN-Compatible**: Designed to run with `network_mode: service:gluetun`

**API Endpoints:**
```
GET /health - Health check (returns libtorrent and Redis availability)
GET /peers/{acestream_id} - Get peer stats for an AceStream ID
```

### 2. Orchestrator Updates

**Configuration (`app/core/config.py`):**
- Added `PEER_COLLECTOR_ENABLED` - Toggle peer collection on/off
- Added `PEER_COLLECTOR_URL` - URL of the microservice (e.g., `http://gluetun:8080`)

**Peer Stats Service (`app/services/peer_stats.py`):**
- New function: `get_stream_peer_stats_via_microservice()` - Calls microservice API
- New function: `get_stream_peer_stats_direct()` - Original direct collection (legacy)
- Updated: `get_stream_peer_stats()` - Auto-selects microservice or direct mode

**API Endpoints (`app/main.py`):**
- `GET /config/runtime` - Get current config (peer collector settings)
- `PATCH /config/runtime` - Update config (enable/disable peer collector)
- `POST /peer-collector/health` - Test peer collector connectivity

**Environment Variables (`.env.example`):**
```bash
PEER_COLLECTOR_ENABLED=false
PEER_COLLECTOR_URL=http://gluetun:8080
```

### 3. Docker Compose Integration

Updated both VPN compose files:
- `docker-compose.gluetun.yml`
- `docker-compose.gluetun-redundant.yml`

**Added Service:**
```yaml
peer-collector:
  build:
    context: ./peer_collector
    dockerfile: Dockerfile
  container_name: peer-collector
  network_mode: "service:gluetun"  # Share network with Gluetun
  environment:
    - CACHE_TTL_SECONDS=30
    - MAX_PEERS_TO_ENRICH=50
  restart: unless-stopped
  depends_on:
    gluetun:
      condition: service_healthy
```

### 4. UI Updates

**New Settings Page (`PeerCollectorSettings.jsx`):**
- Toggle to enable/disable peer collection
- Input field for collector URL
- "Test Connection" button to verify connectivity
- Health status display

**Updated Streams Table (`StreamsTable.jsx`):**
- Fetches peer collector config on mount
- Conditionally shows/hides "Peers" tab based on `PEER_COLLECTOR_ENABLED`
- Peer count always visible in the collapsed view
- Detailed peer list only shown when enabled

**Settings Page Integration (`SettingsPage.jsx`):**
- Added "Peer Collector" tab to settings
- 5-column tab layout (General, Proxy, Peer Collector, Loop Detection, Backup)

### 5. Documentation

**Main README:**
- Added peer collector to features list
- Added link to peer collector documentation

**Peer Collector README:**
- Architecture overview
- API endpoint documentation
- Environment variables reference
- Docker compose integration examples
- Standalone running instructions
- Development setup guide

## Usage

### Enable Peer Collection (Method 1: Environment Variables)

1. Add to `.env`:
```bash
PEER_COLLECTOR_ENABLED=true
PEER_COLLECTOR_URL=http://gluetun:8080
```

2. Start with VPN compose file:
```bash
docker-compose -f docker-compose.gluetun.yml up -d
```

### Enable Peer Collection (Method 2: UI)

1. Start the orchestrator
2. Go to Settings → Peer Collector
3. Toggle "Enable Peer Collection" ON
4. Enter URL: `http://gluetun:8080`
5. Click "Test Connection" to verify
6. Click "Save Settings"

### View Peer Data

1. Navigate to Streams page
2. Expand any active stream
3. Click the "Peers" tab
4. View peer details with geolocation data

## Technical Decisions

### Why Distroless?
- Minimal attack surface
- Smaller image size
- No shell or package manager
- Production-ready security

### Why Bundle Redis?
- Consistent with orchestrator approach
- Zero external dependencies
- Simplified deployment
- Automatic startup

### Why libtorrent?
- Industry standard for BitTorrent protocol
- Efficient peer discovery via DHT and trackers
- Reliable and well-tested

### Why FastAPI?
- Modern async framework
- Automatic API documentation
- Type validation with Pydantic
- Consistent with orchestrator stack

## Performance Considerations

### Caching Strategy
- **In-memory cache**: 30-second TTL for peer stats
- **Redis cache**: Optional (bundled and enabled by default)
- **IP geolocation cache**: 1-hour TTL to minimize API calls

### Resource Usage
- **CPU**: Low (only when peers are requested)
- **Memory**: ~100-200MB (libtorrent session + Redis)
- **Network**: Minimal (DHT queries + geolocation API)

### Scalability
- One microservice per VPN container
- Handles multiple concurrent requests
- Redis caching prevents duplicate work
- Geolocation API rate limiting handled gracefully

## Testing Checklist

- [ ] Microservice builds successfully
- [ ] Health endpoint returns correct status
- [ ] Peers endpoint returns peer data
- [ ] Redis caching works correctly
- [ ] Orchestrator can communicate with microservice
- [ ] UI settings page works
- [ ] Peer tab shows/hides based on config
- [ ] Test connection button works
- [ ] Geolocation enrichment works
- [ ] Error handling works (microservice down, invalid infohash, etc.)

## Future Enhancements

1. **Metrics**: Add Prometheus metrics for peer collection
2. **Caching**: Make cache TTL configurable via UI
3. **Filtering**: Add filters for peer list (by country, ISP, etc.)
4. **Charts**: Add peer distribution charts (country map, ISP breakdown)
5. **Historical Data**: Track peer count over time

## Conclusion

The peer collector microservice successfully separates peer collection concerns from the orchestrator, enabling the orchestrator to remain outside the VPN while still providing rich peer statistics to users. The implementation follows best practices with distroless containers, bundled dependencies, and a clean API design.
