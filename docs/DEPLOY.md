# Deployment Guide

This guide covers deployment of the AceStream Orchestrator in various configurations. Configuration is managed via the **Settings Dashboard**.

## Table of Contents

- [Quick Start](#quick-start)
- [Deployment Modes](#deployment-modes)
  - [Standalone (No VPN)](#standalone-no-vpn)
  - [Dynamic VPN Mode (Orchestrator-Managed)](#dynamic-vpn-mode-orchestrator-managed)
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

### With VPN (Orchestrator-Managed)

For VPN-protected engines with dynamic Gluetun provisioning:

```bash
# 1. Copy and edit the environment file
cp .env.example .env
# Edit .env: set API_KEY and dynamic VPN defaults (provider/protocol)

# 2. Start orchestrator
docker-compose up -d

# 3. Access the dashboard and configure VPN credentials in Settings -> VPN
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

### Dynamic VPN Mode (Orchestrator-Managed)

**Use Case:** Production deployments requiring VPN protection with automatic Gluetun lifecycle management.

**Docker Compose:** `docker-compose.yml`

**Configuration:**

1. **Start services:**
```bash
docker-compose up -d
```

2. **Enable VPN in Dashboard:**
- Go to **Settings > VPN**.
- Set **VPN Integration** to Enabled.
- Enable **Dynamic VPN Management**.
- Configure provider/protocol defaults and add VPN credentials.

3. **High availability behavior:**
- The controller provisions multiple dynamic VPN nodes as load grows.
- Desired VPN node count is derived from active engine demand and `PREFERRED_ENGINES_PER_VPN`.

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