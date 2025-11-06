# AceStream Orchestrator v1.0.0 - First Stable Release 🎉

We're excited to announce the first stable release of AceStream Orchestrator! This release brings a mature, production-ready orchestration service for managing AceStream engines with comprehensive documentation, VPN integration, and modern monitoring capabilities.

## 🌟 What is AceStream Orchestrator?

AceStream Orchestrator is a dynamic orchestration service that provisions AceStream engine containers on-demand, monitors their health, collects stream statistics, and provides a modern web dashboard for operational visibility. It seamlessly integrates with proxy services that request engines when needed.

## 🚀 Key Features

### Core Functionality
- **Dynamic Engine Provisioning**: Automatically provision AceStream engines on-demand based on traffic
- **Multiple Deployment Modes**: Standalone, single VPN, or redundant VPN with automatic failover
- **Multi-Architecture Support**: Native support for AMD64, ARM64, and ARM32 architectures
- **Health Monitoring**: Automatic detection of engine issues with real-time status updates
- **Usage Tracking**: Intelligent idle time tracking for optimal load balancing
- **Modern Web Dashboard**: Beautiful React-based interface with Material-UI for real-time monitoring

### VPN Integration
- **Gluetun VPN Support**: Full integration with Gluetun for secure streaming
- **Single VPN Mode**: Basic VPN protection for all engines
- **Redundant VPN Mode**: High availability setup with automatic failover
- **Port Forwarding**: Intelligent P2P port management for optimal connectivity
- **Forwarded Engines**: Advanced port forwarding configuration for improved peer connectivity

### Monitoring & Operations
- **Prometheus Metrics**: Built-in metrics endpoint for monitoring and alerting
- **Stale Stream Detection**: Automatic cleanup of stopped streams
- **Cache Management**: Intelligent cache cleanup for resource optimization
- **RESTful API**: Complete API for provisioning, events, and status monitoring
- **Docker Compose Templates**: Pre-configured templates for all deployment modes

## 📚 Comprehensive Documentation

This release includes extensive documentation covering all aspects of deployment and operations:

- **[Deployment Guide](docs/DEPLOY.md)** - Step-by-step deployment instructions
- **[Configuration Reference](docs/CONFIG.md)** - Complete environment variable documentation
- **[API Documentation](docs/API.md)** - Full API endpoint reference
- **[Architecture & Operations](docs/ARCHITECTURE.md)** - System design and internals
- **[Engine Variants](docs/ENGINE_VARIANTS.md)** - Multi-architecture engine configuration
- **[Gluetun Integration](docs/GLUETUN_INTEGRATION.md)** - VPN setup and configuration
- **[Gluetun Failure & Recovery](docs/GLUETUN_FAILURE_RECOVERY.md)** - VPN failure handling
- **[Health Monitoring](docs/HEALTH_MONITORING.md)** - Health check system details
- **[Dashboard Guide](docs/PANEL.md)** - Web interface usage guide
- **[Security Considerations](docs/SECURITY.md)** - Security best practices
- **[Testing Guide](docs/TESTING_GUIDE.md)** - Testing procedures

## 🛠️ Quick Start

### Standalone Deployment
```bash
cp .env.example .env
# Edit .env: Set API_KEY and configure port ranges
docker-compose up -d
```

### With VPN (Single)
```bash
cp .env.example .env
# Edit .env: Set API_KEY and VPN settings
# Edit docker-compose.gluetun.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun.yml up -d
```

### With Redundant VPN (High Availability)
```bash
cp .env.example .env
# Edit .env: Set API_KEY and redundant VPN settings
# Edit docker-compose.gluetun-redundant.yml: Configure VPN credentials
docker-compose -f docker-compose.gluetun-redundant.yml up -d
```

**Dashboard**: Access at `http://localhost:8000/panel`

## 🐳 Docker Images

Docker images are available on GitHub Container Registry (GHCR):

```bash
docker pull ghcr.io/krinkuto11/acestream-orchestrator:v1.0.0
```

Multi-architecture support:
- `linux/amd64`
- `linux/arm64`
- `linux/arm/v7`

## 📋 Requirements

- Docker 24+ with access to Docker socket or docker:dind
- Free ports within the ranges defined in `.env`
- For VPN mode: Valid VPN credentials (ProtonVPN, NordVPN, etc.)

## 🔐 Security

- API key authentication for all provisioning and event endpoints
- Secure VPN credential management
- Network isolation support
- See [Security Considerations](docs/SECURITY.md) for best practices

## 🧪 Testing

Comprehensive test suite included:
```bash
# Unit tests
python -m pytest tests/

# Manual testing
python tests/manual_test_forwarded.py
```

## 💡 Use Cases

Perfect for:
- Home media servers with dynamic AceStream needs
- Multi-user environments requiring load balancing
- Secure streaming through VPN tunnels
- High-availability streaming setups
- Monitoring and tracking stream usage

## 🙏 Acknowledgments

Special thanks to:
- The AceStream community for the excellent streaming technology
- Gluetun project for VPN integration capabilities
- All contributors and testers who helped shape this release

## 📝 License

See LICENSE file for details.

## 🔗 Links

- **Repository**: https://github.com/krinkuto11/acestream-orchestrator
- **Issues**: https://github.com/krinkuto11/acestream-orchestrator/issues
- **Docker Hub**: ghcr.io/krinkuto11/acestream-orchestrator

## 🚧 What's Next?

Future releases will include:
- Enhanced metrics and monitoring
- Additional VPN provider support
- Performance optimizations
- Extended API capabilities

---

**Full Changelog**: Initial stable release

Thank you for using AceStream Orchestrator! 🎉
