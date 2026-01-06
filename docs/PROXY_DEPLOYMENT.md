# AceStream Proxy Deployment Guide

## Overview

The AceStream Proxy (v1.5.0+) provides a unified streaming endpoint that simplifies video delivery from AceStream engines. This guide covers deployment, configuration, and usage.

## Quick Start

The proxy is automatically enabled in v1.5.0+. No additional configuration needed!

### Basic Usage

```bash
# Start orchestrator (any mode)
docker-compose up -d

# Access proxy endpoint
http://localhost:8000/ace/getstream?id=<your_infohash>
```

### Example with VLC

```bash
# Stream a video through the proxy
vlc "http://localhost:8000/ace/getstream?id=94c2fd8fb9bc8f2fc71a9c1b9b7a5a8f0a2e3f4d"
```

### Example with curl

```bash
# Download stream to file
curl "http://localhost:8000/ace/getstream?id=94c2fd8fb9bc8f2fc71a9c1b9b7a5a8f0a2e3f4d" > stream.ts

# Pipe to player
curl "http://localhost:8000/ace/getstream?id=94c2fd8fb9bc8f2fc71a9c1b9b7a5a8f0a2e3f4d" | mpv -
```

## Architecture

```
┌─────────────┐
│   Clients   │ (VLC, MPV, Web Players)
└──────┬──────┘
       │ GET /ace/getstream?id=...
       ↓
┌─────────────────────────┐
│  AceStream Proxy        │
│  (Port 8000)            │
│                         │
│  - Engine Selection     │
│  - Multiplexing         │
│  - Lifecycle Management │
└──────┬──────────────────┘
       │
       ↓ Selects best engine
┌──────────────────────────────┐
│  AceStream Engines           │
│  (Auto-provisioned)          │
│                              │
│  Engine 1 (Forwarded)  ←─┐  │
│  Engine 2 (Forwarded)    │  │
│  Engine 3 (Regular)      │  │
└──────┬───────────────────┼──┘
       │                   │
       │                   └─ Load Balancing
       ↓
┌──────────────┐
│  AceStream   │
│  P2P Network │
└──────────────┘
```

## Deployment Modes

### Standalone Mode (No VPN)

```bash
cp .env.example .env
# Edit .env: Set API_KEY and configure port ranges
docker-compose up -d
```

**Proxy URL**: `http://localhost:8000/ace/getstream`

### Single VPN Mode

```bash
cp .env.example .env
# Edit .env: Set API_KEY and VPN settings
# Edit docker-compose.gluetun.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun.yml up -d
```

**Proxy URL**: `http://localhost:8000/ace/getstream`

### Redundant VPN Mode (High Availability)

```bash
cp .env.example .env
# Edit .env: Set API_KEY and redundant VPN settings
# Edit docker-compose.gluetun-redundant.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun-redundant.yml up -d
```

**Proxy URL**: `http://localhost:8000/ace/getstream`

**Benefits**:
- Automatic VPN failover
- Prioritizes forwarded engines
- Maximum P2P performance

## Configuration

### Environment Variables

All proxy functionality uses existing orchestrator configuration:

```bash
# Minimum engines (ensures capacity)
MIN_REPLICAS=3

# Maximum engines (limits resource usage)
MAX_REPLICAS=20

# API authentication
API_KEY=your-secure-key
```

### Proxy-Specific Configuration

Edit `app/services/proxy/config.py` for advanced settings:

```python
# Stream timeouts
EMPTY_STREAM_TIMEOUT = 30  # Seconds to wait for initial data
STREAM_IDLE_TIMEOUT = 300  # Seconds before cleanup (5 minutes)

# Buffer sizes
STREAM_BUFFER_SIZE = 4 * 1024 * 1024  # 4 MB
COPY_CHUNK_SIZE = 64 * 1024  # 64 KB

# Engine selection
ENGINE_SELECTION_TIMEOUT = 5  # Seconds
ENGINE_CACHE_TTL = 2  # Seconds
MAX_STREAMS_PER_ENGINE = 10  # Maximum per engine
```

## Client Integration

### Web Players

#### Video.js Example

```html
<video id="player" class="video-js" controls></video>
<script src="https://vjs.zencdn.net/7.20.3/video.min.js"></script>
<script>
  const player = videojs('player', {
    sources: [{
      src: 'http://localhost:8000/ace/getstream?id=94c2fd8fb9bc8f2fc71a9c1b9b7a5a8f0a2e3f4d',
      type: 'video/mp2t'
    }]
  });
</script>
```

#### HLS.js Example (when HLS support added)

```html
<video id="player" controls></video>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<script>
  const video = document.getElementById('player');
  const hls = new Hls();
  
  hls.loadSource('http://localhost:8000/ace/manifest.m3u8?id=94c2fd8fb9bc8f2fc71a9c1b9b7a5a8f0a2e3f4d');
  hls.attachMedia(video);
</script>
```

### Native Players

#### VLC

```bash
vlc "http://localhost:8000/ace/getstream?id=<infohash>"
```

#### MPV

```bash
mpv "http://localhost:8000/ace/getstream?id=<infohash>"
```

#### FFmpeg

```bash
ffmpeg -i "http://localhost:8000/ace/getstream?id=<infohash>" -c copy output.ts
```

### Mobile Apps

Most mobile video players that support HTTP streaming will work:

- **Android**: VLC, MX Player, Kodi
- **iOS**: VLC, Infuse, PlayerXtreme

## Load Balancer Integration

### NGINX Example

```nginx
upstream acestream_proxy {
    server orchestrator1:8000;
    server orchestrator2:8000;
    server orchestrator3:8000;
    
    # Use IP hash for session affinity
    ip_hash;
}

server {
    listen 80;
    server_name streams.example.com;
    
    location /ace/ {
        proxy_pass http://acestream_proxy;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

### HAProxy Example

```
frontend acestream
    bind *:80
    default_backend acestream_proxy

backend acestream_proxy
    balance leastconn
    option http-keep-alive
    timeout connect 5s
    timeout client 3600s
    timeout server 3600s
    
    server orch1 orchestrator1:8000 check
    server orch2 orchestrator2:8000 check
    server orch3 orchestrator3:8000 check
```

## Monitoring

### Health Check

```bash
# Check proxy status
curl http://localhost:8000/proxy/status

# Expected response
{
  "running": true,
  "total_sessions": 5,
  "active_sessions": 4,
  "total_clients": 12
}
```

### Metrics

```bash
# Prometheus metrics
curl http://localhost:8000/metrics | grep proxy

# Check orchestrator status
curl http://localhost:8000/orchestrator/status
```

### Dashboard

Access the web dashboard:
```
http://localhost:8000/panel
```

Future updates will add proxy-specific dashboard panels.

## Performance Tuning

### Optimize for Many Clients

```bash
# Increase maximum engines
MAX_REPLICAS=50

# Reduce idle timeout for faster cleanup
# Edit app/services/proxy/config.py:
STREAM_IDLE_TIMEOUT = 120  # 2 minutes
```

### Optimize for High Bandwidth

```bash
# Increase buffer sizes
# Edit app/services/proxy/config.py:
STREAM_BUFFER_SIZE = 8 * 1024 * 1024  # 8 MB
COPY_CHUNK_SIZE = 128 * 1024  # 128 KB
```

### Optimize for Low Latency

```bash
# Decrease buffer sizes
# Edit app/services/proxy/config.py:
STREAM_BUFFER_SIZE = 2 * 1024 * 1024  # 2 MB
COPY_CHUNK_SIZE = 32 * 1024  # 32 KB
```

## Troubleshooting

### No Engines Available (503 Error)

**Problem**: `503 Service Unavailable` when requesting stream

**Diagnosis**:
```bash
# Check engine count
curl http://localhost:8000/engines | jq 'length'

# Check engine health
curl http://localhost:8000/engines | jq '.[] | {id: .container_id, health: .health_status}'
```

**Solutions**:
1. Wait for engines to provision (check `MIN_REPLICAS`)
2. Check Docker resources (CPU, memory, disk)
3. Verify VPN connectivity (if using VPN mode)

### Stream Buffering/Stuttering

**Problem**: Video playback is choppy

**Diagnosis**:
```bash
# Check engine load
curl http://localhost:8000/proxy/status | jq '.sessions[] | {engine: .container_id, clients: .client_count}'

# Check engine stats
curl http://localhost:8000/engines/stats/all
```

**Solutions**:
1. Increase `MAX_REPLICAS` to add more engines
2. Check network bandwidth
3. Verify P2P connectivity (forwarded engines help)

### High Memory Usage

**Problem**: Orchestrator using too much memory

**Diagnosis**:
```bash
# Check active sessions
curl http://localhost:8000/proxy/status | jq '{total: .total_sessions, active: .active_sessions}'

# Check idle sessions
curl http://localhost:8000/proxy/sessions | jq '.sessions[] | select(.client_count == 0)'
```

**Solutions**:
1. Reduce `STREAM_IDLE_TIMEOUT` (faster cleanup)
2. Monitor for session leaks
3. Restart orchestrator if needed

### Clients Not Multiplexing

**Problem**: Each client creates a new stream

**Diagnosis**:
```bash
# Check if same content ID creates separate sessions
curl http://localhost:8000/proxy/sessions | jq '.sessions[] | .ace_id' | sort | uniq -c
```

**Solutions**:
1. Verify clients use exact same `id` parameter
2. Check for URL encoding differences
3. Review client implementation

## Security Considerations

### API Key Protection

The proxy uses the orchestrator's existing API key system:

```bash
# Protected endpoints (require X-API-KEY header)
# None currently - proxy is designed for open access

# Public endpoints
GET /ace/getstream
GET /proxy/status
GET /proxy/sessions
```

### Rate Limiting

Consider adding rate limiting at the reverse proxy level:

```nginx
# NGINX rate limiting example
limit_req_zone $binary_remote_addr zone=stream_limit:10m rate=10r/s;

location /ace/getstream {
    limit_req zone=stream_limit burst=5;
    proxy_pass http://acestream_proxy;
}
```

### Network Security

```bash
# Restrict orchestrator access
iptables -A INPUT -p tcp --dport 8000 -s trusted_network -j ACCEPT
iptables -A INPUT -p tcp --dport 8000 -j DROP
```

## Migration from Acexy

If migrating from the acexy proxy:

### URL Format

**Old (acexy)**:
```
http://acexy:8080/ace/getstream?id=<infohash>
```

**New (orchestrator proxy)**:
```
http://orchestrator:8000/ace/getstream?id=<infohash>
```

### Differences

1. **Endpoint**: Port changed from 8080 to 8000 (orchestrator default)
2. **Engine Selection**: Now uses orchestrator's intelligent selection
3. **Monitoring**: Integrated with orchestrator dashboard
4. **Multiplexing**: Better client multiplexing support
5. **VPN Integration**: Automatic VPN-aware engine selection

### Migration Steps

1. Update client URLs to point to orchestrator
2. Remove acexy containers
3. Verify streams work through new proxy
4. Monitor `/proxy/status` for issues

## Best Practices

1. **Use VPN Mode** for better P2P connectivity
2. **Monitor `/proxy/status`** regularly
3. **Set `MIN_REPLICAS`** high enough for your expected load
4. **Use forwarded engines** when possible (better performance)
5. **Configure load balancer** for long-lived connections
6. **Set client-side timeouts** > 5 minutes
7. **Monitor engine health** via `/engines` endpoint

## Support

For issues, questions, or feature requests:
- GitHub Issues: https://github.com/krinkuto11/acestream-orchestrator/issues
- Documentation: See `docs/` directory
- API Reference: See `docs/PROXY_API.md`
