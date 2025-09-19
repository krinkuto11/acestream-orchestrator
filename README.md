# Orchestrator + Panel

Endpoints: /provision, /provision/acestream, /events/*, /engines, /streams, /streams/{id}/stats, /containers/{id}, /by-label, /metrics, /panel.

Quickstart:
cp .env.example .env

Abre `http://localhost:8000/panel`.

# Requisitos

 - Docker 24+ y docker:dind en compose.

 - Python 3.12 en imagen.

 - Puertos libres dentro de los rangos definidos en .env.

# Estructura

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

# Documentación
* [README](README.md)
* [Visión general](docs/OVERVIEW.md)
* [Configuración](docs/CONFIG.md)
* [API](docs/API.md)
* [Eventos](docs/EVENTS.md)
* [Panel](docs/PANEL.md)
* [Esquema de BD](docs/DB_SCHEMA.md)
* [Despliegue](docs/DEPLOY.md)
* [Operación](docs/OPERATIONS.md)
* [Troubleshooting](docs/TROUBLESHOOTING.md)
* [Seguridad](docs/SECURITY.md)
* [Integración con el proxy](docs/PROXY_INTEGRATION.md)


