# Container Provisioning Troubleshooting Guide

## Issue: MIN_REPLICAS containers not being started

This guide helps troubleshoot when the orchestrator is configured with `MIN_REPLICAS > 0` but containers are not being provisioned automatically.

## Quick Verification

Run the verification script to test your setup:

```bash
python test_min_replicas.py
```

Or run diagnostics:

```bash
python diagnose_provisioning.py
```

## Common Issues and Solutions

### 1. Docker Image Not Found

**Symptoms:**
- Orchestrator starts but no containers appear
- Error logs mention "not found" or "pull access denied"

**Cause:** The `TARGET_IMAGE` specified in your `.env` file doesn't exist or isn't accessible.

**Solution:**
```bash
# Check if the image exists
docker pull acestream/engine:latest

# If it fails, try a working acestream image:
# In your .env file, change:
TARGET_IMAGE=blaiseio/acestream
# or
TARGET_IMAGE=sparklyballs/acestream
```

**Verification:**
```bash
docker images | grep acestream
```

### 2. MIN_REPLICAS Set to 0

**Symptoms:**
- No containers started even though you expect them

**Cause:** `MIN_REPLICAS=0` in configuration.

**Solution:**
Check your `.env` file:
```bash
# Set to desired number of minimum containers
MIN_REPLICAS=3
MAX_REPLICAS=10
```

**Verification:**
```bash
grep MIN_REPLICAS .env
```

### 3. Docker Daemon Not Running

**Symptoms:**
- Orchestrator fails to start with Docker connection errors

**Cause:** Docker daemon is not running or not accessible.

**Solution:**
```bash
# Start Docker daemon
sudo systemctl start docker
sudo systemctl enable docker

# Add user to docker group (logout/login required)
sudo usermod -aG docker $USER
```

**Verification:**
```bash
docker ps
```

### 4. Container Startup Timeout

**Symptoms:**
- Containers start but then get removed
- Logs show "Container failed to start within XXs"

**Cause:** Some images (like acestream) take longer to start than the default timeout.

**Solution:**
In your `.env` file:
```bash
# Increase startup timeout for slow-starting images
STARTUP_TIMEOUT_S=60
```

### 5. Network Issues

**Symptoms:**
- Containers start but are not reachable
- API shows containers but they appear unreachable

**Cause:** Docker network configuration issues.

**Solution:**
```bash
# Try without custom network first
DOCKER_NETWORK=

# Or specify a working network
DOCKER_NETWORK=bridge
```

### 6. Permission Issues

**Symptoms:**
- "Permission denied" errors when starting containers

**Cause:** User doesn't have Docker permissions.

**Solution:**
```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Or run with sudo (not recommended for production)
sudo python -m uvicorn app.main:app
```

## Manual Testing Steps

### 1. Test Docker Connectivity
```bash
docker ps
docker run --rm hello-world
```

### 2. Test Image Availability
```bash
# Test pulling your configured image
docker pull $(grep TARGET_IMAGE .env | cut -d'=' -f2)
```

### 3. Test Container Creation
```bash
# Test manual container creation with your image
docker run -d --label "test.manual=true" $(grep TARGET_IMAGE .env | cut -d'=' -f2)
docker ps -a --filter "label=test.manual=true"
docker rm -f $(docker ps -aq --filter "label=test.manual=true")
```

### 4. Check Orchestrator Logs
```bash
# Run orchestrator with verbose logging
export PYTHONPATH=$(pwd)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level debug
```

### 5. Verify API Response
```bash
# Check engines endpoint
curl http://localhost:8000/engines

# Check managed containers (requires API_KEY)
curl -H "Authorization: Bearer YOUR_API_KEY" \
  "http://localhost:8000/by-label?key=ondemand.app&value=myservice"
```

## Environment Configuration Check

Create a test environment file:

```bash
# .env.test
MIN_REPLICAS=2
MAX_REPLICAS=5
TARGET_IMAGE=nginx:alpine  # Use reliable image for testing
CONTAINER_LABEL=test.orchestrator=true
STARTUP_TIMEOUT_S=30
API_KEY=test-key-123
APP_PORT=8001
```

Then test:
```bash
cp .env .env.backup
cp .env.test .env
python test_min_replicas.py
cp .env.backup .env
```

## Working acestream Images

If `acestream/engine:latest` doesn't work, try these verified images:

1. `blaiseio/acestream` - Well maintained
2. `sparklyballs/acestream` - Popular choice  
3. `wafy80/acestream` - Latest and lightest
4. `cturra/acestream` - Console version

Example configuration:
```bash
TARGET_IMAGE=blaiseio/acestream
MIN_REPLICAS=3
STARTUP_TIMEOUT_S=45  # acestream takes time to start
```

## Debug Mode

Enable debug logging in your orchestrator:

```python
# In app/main.py or run with environment variable
import logging
logging.basicConfig(level=logging.DEBUG)
```

Or run with debug environment:
```bash
PYTHONPATH=$(pwd) python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from app.services.autoscaler import ensure_minimum
ensure_minimum()
"
```

## Expected Behavior

When `MIN_REPLICAS=3`:

1. **Startup:** Orchestrator calls `ensure_minimum()` during startup
2. **Container Creation:** 3 containers should be created with your `CONTAINER_LABEL`  
3. **Status Check:** Containers should show as "running" in `docker ps`
4. **API Response:** `/engines` endpoint should return 3 engines
5. **Reachability:** Containers should be accessible via Docker socket and orchestrator API

## Support

If these steps don't resolve your issue:

1. Run `python diagnose_provisioning.py` and share the output
2. Check `docker logs` for container startup errors
3. Share your `.env` configuration (redact sensitive values)
4. Include orchestrator startup logs