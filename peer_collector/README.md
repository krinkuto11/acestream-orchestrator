# AceStream Peer Collector Microservice

A lightweight, distroless, rootless microservice for collecting peer statistics from AceStream torrents. This service runs inside the Gluetun VPN container and provides peer data to the orchestrator via a simple API.

## Features

- **Distroless & Rootless**: Built on Google's Distroless base image for minimal attack surface
- **Ultra-lightweight**: Only essential dependencies included
- **VPN-Compatible**: Designed to run inside Gluetun container to leverage VPN connectivity
- **Built-in Redis**: Redis is bundled and starts automatically for fast caching (same as orchestrator)
- **Geolocation**: Enriches peer data with country, city, ISP information

## API Endpoints

### Health Check
```
GET /health
```

Returns the health status and availability of libtorrent and Redis.

**Response:**
```json
{
  "status": "healthy",
  "libtorrent_available": true,
  "redis_available": true
}
```

### Get Peer Statistics
```
GET /peers/{acestream_id}
```

Returns peer statistics for the given AceStream ID, including geolocation data.

**Response:**
```json
{
  "acestream_id": "94c2fd8fb9bc8f2fc71a2cbe9d4b866f227a0209",
  "infohash": "94c2fd8fb9bc8f2fc71a2cbe9d4b866f227a0209",
  "peers": [
    {
      "ip": "1.2.3.4",
      "port": 12345,
      "client": "uTorrent 3.5.5",
      "progress": 100.0,
      "download_rate": 0,
      "upload_rate": 256000,
      "country": "United States",
      "country_code": "US",
      "city": "New York",
      "isp": "Example ISP"
    }
  ],
  "peer_count": 1,
  "total_peers": 1,
  "cached_at": 1705343674.123
}
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_HOST` | Redis server hostname (bundled Redis uses localhost) | `127.0.0.1` |
| `REDIS_PORT` | Redis server port | `6379` |
| `REDIS_DB` | Redis database number | `0` |
| `CACHE_TTL_SECONDS` | How long to cache peer stats | `30` |
| `MAX_PEERS_TO_ENRICH` | Max peers to enrich with geolocation | `50` |

**Note**: Redis is bundled in the container and starts automatically (same as the orchestrator). You don't need to configure an external Redis instance unless you want to use one.

## Docker Compose Integration

Add the peer collector to your `docker-compose.gluetun.yml`:

```yaml
services:
  gluetun:
    # ... existing gluetun config ...

  peer-collector:
    # Option 1: Use pre-built image from GHCR (recommended for production)
    image: ghcr.io/krinkuto11/acestream-peer-collector:latest
    
    # Option 2: Build locally (recommended for development)
    # build:
    #   context: ./peer_collector
    #   dockerfile: Dockerfile
    
    container_name: peer-collector
    network_mode: "service:gluetun"  # Share network with Gluetun
    environment:
      - CACHE_TTL_SECONDS=30
      - MAX_PEERS_TO_ENRICH=50
      # Redis is bundled and starts automatically
    restart: unless-stopped
    depends_on:
      - gluetun

  orchestrator:
    # ... existing orchestrator config ...
    environment:
      # ... other vars ...
      - PEER_COLLECTOR_ENABLED=true
      - PEER_COLLECTOR_URL=http://gluetun:8080  # Access via gluetun's network
```

## Pre-built Docker Images

Pre-built multi-architecture images are available on GitHub Container Registry:

```bash
# Pull the latest version
docker pull ghcr.io/krinkuto11/acestream-peer-collector:latest

# Pull a specific version (when releases are published)
docker pull ghcr.io/krinkuto11/acestream-peer-collector:v1.0.0
```

Supported architectures:
- `linux/amd64` (x86_64)
- `linux/arm64` (ARM 64-bit)

## Building

### Local Build

```bash
cd peer_collector
docker build -t acestream-peer-collector:latest .
```

### Multi-Architecture Build

The GitHub Actions workflow automatically builds for `linux/amd64` and `linux/arm64` on every push to `dev` branch and on releases.

## Running Standalone

### Using Pre-built Image

```bash
docker run -p 8080:8080 ghcr.io/krinkuto11/acestream-peer-collector:latest
```

### Using Local Build

```bash
docker run -p 8080:8080 acestream-peer-collector:latest
```

Redis starts automatically for caching.

## Development

### Local Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the service:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### Testing

Test the health endpoint:
```bash
curl http://localhost:8080/health
```

Test peer collection:
```bash
curl http://localhost:8080/peers/94c2fd8fb9bc8f2fc71a2cbe9d4b866f227a0209
```

## Architecture Notes

- **Libtorrent Integration**: Uses libtorrent to join BitTorrent swarms and collect peer information
- **Caching Strategy**: Two-tier caching (in-memory + Redis) to minimize API calls; Redis is bundled and starts automatically
- **Geolocation**: Uses ipwhois.io API with 1-hour cache to enrich peer data
- **Security**: Runs in distroless container for minimal attack surface
- **Redis**: Bundled in the container (same approach as orchestrator) for zero-config caching
