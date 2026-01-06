# Deployment Guide

This guide covers deployment of the AceStream Orchestrator in various configurations.

## Table of Contents

- [Quick Start](#quick-start)
- [Deployment Modes](#deployment-modes)
  - [Standalone (No VPN)](#standalone-no-vpn)
  - [Single VPN Mode](#single-vpn-mode)
  - [Redundant VPN Mode](#redundant-vpn-mode-high-availability)
- [Configuration](#configuration)
- [Production Checklist](#production-checklist)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Standalone Mode (No VPN)

The simplest setup without VPN integration:

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env: Set API_KEY and adjust port ranges

# 2. Start the orchestrator
docker-compose up -d

# 3. Access the dashboard
open http://localhost:8000/panel
```

### With VPN (Single)

For VPN-protected engines:

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env: Set API_KEY, configure VPN settings

# 2. Configure VPN credentials in docker-compose.gluetun.yml
# Edit WIREGUARD_PRIVATE_KEY or other VPN settings

# 3. Start with VPN
docker-compose -f docker-compose.gluetun.yml up -d

# 4. Access the dashboard
open http://localhost:8000/panel
```

---

## Deployment Modes

### Standalone (No VPN)

**Use Case:** Testing, development, or when VPN is not required.

**Docker Compose:** `docker-compose.yml`

**Key Features:**
- Simple setup with minimal configuration
- Direct network access for engines
- No VPN overhead
- Best for development and testing

**Configuration:**

1. **Edit `.env`:**
```bash
# Core settings
API_KEY=your-secure-api-key-here
ENGINE_VARIANT=krinkuto11-amd64
MIN_REPLICAS=3
MAX_REPLICAS=20

# Port ranges
PORT_RANGE_HOST=19000-19999
ACE_HTTP_RANGE=40000-44999
ACE_HTTPS_RANGE=45000-49999

# Container management
CONTAINER_LABEL=orchestrator.managed=acestream
AUTO_DELETE=true
```

2. **Start services:**
```bash
docker-compose up -d
```

3. **Verify deployment:**
```bash
# Check orchestrator logs
docker logs orchestrator

# Test API
curl http://localhost:8000/engines

# Access dashboard
open http://localhost:8000/panel
```

### Single VPN Mode

**Use Case:** Production deployments requiring VPN protection for all engines.

**Docker Compose:** `docker-compose.gluetun.yml`

**Key Features:**
- All engines route through VPN
- Port forwarding support
- VPN health monitoring
- Automatic engine restart on VPN reconnect

**Configuration:**

1. **Edit `.env`:**
```bash
# Core settings
API_KEY=your-secure-api-key-here
ENGINE_VARIANT=krinkuto11-amd64
MIN_REPLICAS=3
MAX_REPLICAS=20

# VPN Configuration
VPN_MODE=single
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_API_PORT=8001
VPN_RESTART_ENGINES_ON_RECONNECT=true

# Port ranges (must match Gluetun port mappings)
PORT_RANGE_HOST=19000-19999
ACE_HTTP_RANGE=40000-44999
ACE_HTTPS_RANGE=45000-49999
```

2. **Edit `docker-compose.gluetun.yml`:**
```yaml
# Configure VPN credentials
- WIREGUARD_PRIVATE_KEY=YOUR_WIREGUARD_PRIVATE_KEY_HERE
- VPN_SERVICE_PROVIDER=protonvpn  # or your provider
- SERVER_COUNTRIES=Spain,France,Germany
```

3. **Start services:**
```bash
docker-compose -f docker-compose.gluetun.yml up -d
```

4. **Verify VPN:**
```bash
# Check VPN status
curl http://localhost:8000/vpn/status | jq

# Verify engine IP is VPN IP
docker exec <engine-container> curl -s ifconfig.me
```

**Port Configuration:**

Ensure Gluetun port mappings match your `.env` settings:
```yaml
gluetun:
  ports:
    - "19000-19999:19000-19999"  # Must match PORT_RANGE_HOST
    - "8001:8001"                 # Must match GLUETUN_API_PORT
```

### Redundant VPN Mode (High Availability)

**Use Case:** Mission-critical deployments requiring zero downtime during VPN failures.

**Docker Compose:** `docker-compose.gluetun-redundant.yml`

**Key Features:**
- Two independent VPN connections
- Automatic failover when one VPN fails
- Zero downtime for active streams
- Load balanced across VPNs
- Independent port forwarding per VPN

**Configuration:**

1. **Edit `.env`:**
```bash
# Core settings
API_KEY=your-secure-api-key-here
ENGINE_VARIANT=krinkuto11-amd64
MIN_REPLICAS=6  # Recommended minimum for redundant mode
MAX_REPLICAS=40

# Redundant VPN Configuration
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME=gluetun1
GLUETUN_CONTAINER_NAME_2=gluetun2
GLUETUN_API_PORT=8001

# VPN-specific port ranges (must match docker-compose mappings)
GLUETUN_PORT_RANGE_1=19000-19499  # For gluetun1
GLUETUN_PORT_RANGE_2=19500-19999  # For gluetun2
PORT_RANGE_HOST=19000-19999       # Full range covering both

# VPN health settings
VPN_RESTART_ENGINES_ON_RECONNECT=true
VPN_UNHEALTHY_RESTART_TIMEOUT_S=60
GLUETUN_HEALTH_CHECK_INTERVAL_S=5
```

2. **Edit `docker-compose.gluetun-redundant.yml`:**
```yaml
# Configure VPN credentials for both containers
gluetun1:
  environment:
    - WIREGUARD_PRIVATE_KEY=YOUR_KEY_1_HERE
    - SERVER_COUNTRIES=Spain,France,Germany

gluetun2:
  environment:
    - WIREGUARD_PRIVATE_KEY=YOUR_KEY_2_HERE
    - SERVER_COUNTRIES=Netherlands,Switzerland,Sweden  # Different for redundancy
```

3. **Start services:**
```bash
docker-compose -f docker-compose.gluetun-redundant.yml up -d
```

4. **Verify redundancy:**
```bash
# Check both VPNs are healthy
curl http://localhost:8000/vpn/status | jq

# Verify engines distributed across both VPNs
curl http://localhost:8000/engines | jq '.[] | {name: .container_name, vpn: .vpn_container, port: .port}'

# Should show:
# - Some engines on gluetun1 (ports 19000-19499)
# - Some engines on gluetun2 (ports 19500-19999)
# - At least one forwarded engine per VPN
```

**Failover Testing:**

```bash
# Stop VPN1 to test failover
docker stop gluetun1

# Check that engines on VPN1 are hidden but VPN2 engines still available
curl http://localhost:8000/engines | jq 'length'

# Restart VPN1 to test recovery
docker start gluetun1

# Wait 60-90 seconds for recovery
# Check engines are back
curl http://localhost:8000/engines | jq 'length'
```

---

## Configuration

### Essential Environment Variables

```bash
# Security (REQUIRED)
API_KEY=your-secure-random-api-key

# Engine Configuration
ENGINE_VARIANT=krinkuto11-amd64  # or jopsis-amd64, jopsis-arm32, jopsis-arm64
MIN_REPLICAS=3                   # Initial engine pool size
MAX_REPLICAS=20                  # Maximum concurrent engines

# Port Ranges (REQUIRED)
PORT_RANGE_HOST=19000-19999      # External ports for engine access
ACE_HTTP_RANGE=40000-44999       # Internal HTTP ports
ACE_HTTPS_RANGE=45000-49999      # Internal HTTPS ports

# Container Management
CONTAINER_LABEL=orchestrator.managed=acestream
AUTO_DELETE=true                 # Delete engines when streams end
ENGINE_GRACE_PERIOD_S=30         # Wait before deleting idle engines
```

### VPN-Specific Variables

```bash
# Single VPN Mode
VPN_MODE=single
GLUETUN_CONTAINER_NAME=gluetun
GLUETUN_API_PORT=8001

# Redundant VPN Mode
VPN_MODE=redundant
GLUETUN_CONTAINER_NAME=gluetun1
GLUETUN_CONTAINER_NAME_2=gluetun2
GLUETUN_PORT_RANGE_1=19000-19499
GLUETUN_PORT_RANGE_2=19500-19999
```

### Monitoring and Performance

```bash
# Health Monitoring
MONITOR_INTERVAL_S=10            # Docker health checks
GLUETUN_HEALTH_CHECK_INTERVAL_S=5

# Stats Collection
COLLECT_INTERVAL_S=1             # How often to poll stream stats (default: 1s)
STATS_HISTORY_MAX=720

# Performance
MAX_CONCURRENT_PROVISIONS=5
MIN_PROVISION_INTERVAL_S=0.5
```

---

## Production Checklist

### Security

- [ ] **Set strong API key** - Use a random, long string
- [ ] **Restrict firewall** - Only allow necessary ports
- [ ] **Enable HTTPS** - Use reverse proxy (nginx, traefik) for TLS
- [ ] **Secure VPN credentials** - Use environment variables or secrets
- [ ] **Regular updates** - Keep Docker images updated
- [ ] **Network isolation** - Use Docker networks appropriately

### Data Persistence

- [ ] **Mount database volume**
  ```yaml
  volumes:
    - ./orchestrator.db:/app/orchestrator.db
  ```
- [ ] **Backup database** - Regular backups of `orchestrator.db`
- [ ] **Log rotation** - Configure Docker logging driver

### Monitoring

- [ ] **Dashboard access** - Verify panel at `/panel` works
- [ ] **API health** - Monitor `/health` endpoint
- [ ] **Prometheus metrics** - Scrape `/metrics` endpoint
- [ ] **VPN status** - Monitor `/vpn/status` if using VPN
- [ ] **Engine health** - Track healthy vs unhealthy engines
- [ ] **Alert setup** - Configure alerts for critical issues

### Capacity Planning

- [ ] **Port ranges** - Ensure sufficient ports for MAX_REPLICAS
- [ ] **Resource limits** - Set Docker CPU/memory limits if needed
- [ ] **Disk space** - Monitor space for database and logs
- [ ] **Network bandwidth** - Ensure adequate bandwidth for streams

### VPN Configuration (if applicable)

- [ ] **VPN credentials** - Valid and working credentials
- [ ] **Port forwarding** - Enabled if needed for P2P
- [ ] **Health checks** - Gluetun healthcheck configured
- [ ] **DNS configuration** - Working DNS resolution
- [ ] **Kill switch** - Gluetun firewall enabled
- [ ] **Auto-restart** - `restart: unless-stopped` set

---

## Monitoring

### Dashboard

Access the web dashboard at `http://localhost:8000/panel`

Features:
- Real-time engine status
- Active stream monitoring
- VPN health status
- Performance metrics
- Engine management

### API Endpoints

```bash
# Health check
curl http://localhost:8000/health

# Engine list
curl http://localhost:8000/engines

# Active streams
curl http://localhost:8000/streams?status=started

# VPN status (if configured)
curl http://localhost:8000/vpn/status

# Prometheus metrics
curl http://localhost:8000/metrics
```

### Prometheus Metrics

Add to Prometheus config:
```yaml
scrape_configs:
  - job_name: 'acestream-orchestrator'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
```

Key metrics:
- `orch_engines_total` - Total number of engines
- `orch_streams_active` - Currently active streams
- `orch_vpn_health_status` - VPN health (0=unhealthy, 1=healthy)
- `orch_provision_total` - Total provision requests

### Logs

```bash
# Orchestrator logs
docker logs -f orchestrator

# Gluetun logs (if using VPN)
docker logs -f gluetun

# Engine logs
docker logs <engine-container-name>
```

---

## Troubleshooting

### Orchestrator Won't Start

1. **Check logs:**
   ```bash
   docker logs orchestrator
   ```

2. **Verify configuration:**
   ```bash
   docker exec orchestrator printenv | grep -E 'API_KEY|PORT_RANGE|ENGINE_VARIANT'
   ```

3. **Check port conflicts:**
   ```bash
   netstat -tuln | grep 8000
   ```

### Engines Not Provisioning

1. **Check Docker socket:**
   ```bash
   docker exec orchestrator docker ps
   ```

2. **Verify port availability:**
   ```bash
   curl http://localhost:8000/engines | jq 'length'
   ```

3. **Check logs for errors:**
   ```bash
   docker logs orchestrator | grep -i error
   ```

### VPN Issues

1. **Verify Gluetun is running:**
   ```bash
   docker ps | grep gluetun
   docker logs gluetun | tail -50
   ```

2. **Check VPN status:**
   ```bash
   curl http://localhost:8000/vpn/status | jq
   ```

3. **Test VPN connectivity:**
   ```bash
   docker exec gluetun wget -qO- ifconfig.me
   ```

4. **Verify port forwarding:**
   ```bash
   curl http://localhost:8001/v1/openvpn/portforwarded
   ```

### Engines Can't Connect to VPN

1. **Check network mode:**
   ```bash
   docker inspect <engine-container> | jq '.[].HostConfig.NetworkMode'
   # Should be "container:gluetun"
   ```

2. **Verify Gluetun health:**
   ```bash
   docker inspect gluetun | jq '.[].State.Health'
   ```

3. **Restart orchestrator:**
   ```bash
   docker-compose restart orchestrator
   ```

### Redundant Mode Issues

1. **Verify both VPNs running:**
   ```bash
   docker ps | grep gluetun
   ```

2. **Check port ranges don't overlap:**
   ```bash
   docker port gluetun1 | grep 19000
   docker port gluetun2 | grep 19500
   ```

3. **Verify engine distribution:**
   ```bash
   curl http://localhost:8000/engines | jq 'group_by(.vpn_container) | map({vpn: .[0].vpn_container, count: length})'
   ```

### Performance Issues

1. **Check resource usage:**
   ```bash
   docker stats orchestrator
   ```

2. **Verify engine count:**
   ```bash
   curl http://localhost:8000/engines | jq 'length'
   ```

3. **Check for unhealthy engines:**
   ```bash
   curl http://localhost:8000/engines | jq '[.[] | select(.health_status != "healthy")]'
   ```

---

## Advanced Configuration

### Reverse Proxy (HTTPS)

Example nginx configuration:
```nginx
server {
    listen 443 ssl;
    server_name orchestrator.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Docker Socket Alternatives

For enhanced security, consider using Docker contexts or SSH:
```bash
# Remote Docker host
export DOCKER_HOST=tcp://remote-host:2376
export DOCKER_TLS_VERIFY=1

# Or configure in .env
DOCKER_HOST=tcp://remote-host:2376
```

### Database Backup

Automated backup script:
```bash
#!/bin/bash
DATE=$(date +%Y%m%d-%H%M%S)
cp orchestrator.db backups/orchestrator-$DATE.db
find backups/ -name "orchestrator-*.db" -mtime +7 -delete
```

---

## Related Documentation

- [Configuration Reference](CONFIG.md) - Complete environment variable guide
- [Gluetun Integration](GLUETUN_INTEGRATION.md) - VPN setup details
- [Gluetun Failure & Recovery](GLUETUN_FAILURE_RECOVERY.md) - Failure scenarios
- [Engine Variants](ENGINE_VARIANTS.md) - Engine variant options
- [API Documentation](API.md) - API endpoint reference
- [Health Monitoring](HEALTH_MONITORING.md) - Monitoring guide