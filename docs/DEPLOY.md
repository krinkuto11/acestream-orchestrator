# Deployment Guide

This guide covers deployment of the AceStream Orchestrator in various configurations. Configuration is managed via the **Settings Dashboard**.

## Table of Contents

- [Quick Start](#quick-start)
- [Deployment Modes](#deployment-modes)
  - [Standalone (No VPN)](#standalone-no-vpn)
  - [Single VPN Mode](#single-vpn-mode)
  - [Redundant VPN Mode](#redundant-vpn-mode-high-availability)
- [Production Checklist](#production-checklist)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Standalone Mode (No VPN)

The simplest setup without VPN integration:

```bash
# 1. Start the orchestrator
docker-compose up -d

# 2. Access the dashboard
open http://localhost:8000/panel
```

### With VPN (Single)

For VPN-protected engines:

```bash
# 1. Copy and edit the environment file
cp .env.example .env
# Edit .env: set API_KEY, GLUETUN_CONTAINER_NAME, VPN credentials in docker-compose.gluetun.yml

# 2. Start with VPN
docker-compose -f docker-compose.gluetun.yml up -d

# 3. Access the dashboard
open http://localhost:8000/panel
```

---

## Deployment Modes

### Standalone (No VPN)

**Use Case:** Testing, development, or when VPN is not required.

**Docker Compose:** `docker-compose.yml`

**Key Features:**
- Simple setup with zero initial configuration
- Direct network access for engines
- No VPN overhead

**Configuration:**

Configuration is handled in the Settings dashboard after the services are running.

1. **Start services:**
```bash
docker-compose up -d
```

2. **Verify deployment:**
```bash
# Check orchestrator logs
docker logs orchestrator

# Access dashboard to configure your API key and replicas
open http://localhost:8000/panel
```

### Single VPN Mode

**Use Case:** Production deployments requiring VPN protection for all engines.

**Docker Compose:** `docker-compose.gluetun.yml`

**Configuration:**

1. **Edit `docker-compose.gluetun.yml`:**
```yaml
# Configure VPN credentials in the environment section of the gluetun service
- WIREGUARD_PRIVATE_KEY=YOUR_WIREGUARD_PRIVATE_KEY_HERE
- VPN_SERVICE_PROVIDER=protonvpn
```

2. **Start services:**
```bash
docker-compose -f docker-compose.gluetun.yml up -d
```

3. **Enable VPN in Dashboard:**
- Go to **Settings > VPN**.
- Set **VPN Integration** to Enabled.
- Ensure the **Container Name** matches your Gluetun service name (Default: `gluetun`).

### Redundant VPN Mode (High Availability)

**Use Case:** Mission-critical deployments requiring zero downtime during VPN failures.

**Docker Compose:** `docker-compose.gluetun-redundant.yml`

**Configuration:**

1. **Edit `docker-compose.gluetun-redundant.yml`:**
Configure both `gluetun1` and `gluetun2` credentials.

2. **Start services:**
```bash
docker-compose -f docker-compose.gluetun-redundant.yml up -d
```

3. **Configure Redundancy in Dashboard:**
- Go to **Settings > VPN**.
- Enable VPN.
- Change **VPN Mode** to Redundant.
- Provide names for both containers (`gluetun1`, `gluetun2`).
- Go to **Expert Settings** to verify port ranges assigned to each VPN.

---

## Production Checklist

### Security

- [ ] **Set strong API key** — Set `API_KEY` in `.env` or in Settings > Orchestrator.  
  Requests to protected endpoints must include `Authorization: Bearer <API_KEY>`.
- [ ] **Restrict firewall** - Only allow necessary ports
- [ ] **Enable HTTPS** - Use reverse proxy (nginx, traefik) for TLS

### Data Persistence

- [ ] **Mount config volume**
  All UI-based settings are saved in `/app/app/config`. Ensure this volume is persisted.
  ```yaml
  volumes:
    - ./config:/app/app/config
  ```
- [ ] **Mount database volume**
  ```yaml
  volumes:
    - ./orchestrator.db:/app/orchestrator.db
  ```
- [ ] **Log rotation** - Configure Docker logging driver

### Monitoring

- [ ] **Dashboard access** - Verify panel at `/panel` works
- [ ] **API health** - Monitor `/health` endpoint
- [ ] **Prometheus metrics** - Scrape `/metrics` endpoint
- [ ] **VPN status** - Monitor `/vpn/status` or Dashboard

### Troubleshooting

- [ ] **Check logs**: `docker logs orchestrator`
- [ ] **Check config files**: Inspect `app/config/orchestrator_settings.json` if values seem wrong.
- [ ] **Reset configuration**: Deleting the `.json` files in `config/` will reset the app to systemic defaults.