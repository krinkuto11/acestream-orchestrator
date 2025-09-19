# Orchestrator + Panel

Endpoints: /provision, /provision/acestream, /events/*, /engines, /streams, /streams/{id}/stats, /containers/{id}, /by-label, /metrics, /panel.

Quickstart:
```bash
cp .env.example .env
```

Open `http://localhost:8000/panel`.

# Requirements

 - Docker 24+ and docker:dind in compose.

 - Python 3.12 in image.

 - Free ports within the ranges defined in .env.

# Structure

```md
app/
  main.py
  core/config.py
  models/{schemas.py,db_models.py}
  services/*.py
  static/panel/index.html
docker-compose.yml
Dockerfile
requirements.txt
.env.example
```

# Documentation
* [README](README.md)
* [Overview](docs/OVERVIEW.md)
* [Configuration](docs/CONFIG.md)
* [API](docs/API.md)
* [Events](docs/EVENTS.md)
* [Panel](docs/PANEL.md)
* [Database Schema](docs/DB_SCHEMA.md)
* [Deployment](docs/DEPLOY.md)
* [Operations](docs/OPERATIONS.md)
* [Troubleshooting](docs/TROUBLESHOOTING.md)
* [Security](docs/SECURITY.md)
* [Proxy Integration](docs/PROXY_INTEGRATION.md)


