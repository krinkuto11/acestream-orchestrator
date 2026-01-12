# Stage 1: Build React panel
FROM node:20-slim AS panel-builder
WORKDIR /build/panel-react
COPY app/static/panel-react/package*.json ./
RUN npm install --ignore-scripts
COPY app/static/panel-react/ ./
# This will output to /build/panel (one level up, as configured in vite.config.js)
RUN npm run build

# Stage 2: Build Python dependencies (use 3.11 to match Distroless)
FROM python:3.11-slim AS python-builder
WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install \
    --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Stage 3: Install Redis and collect all dependencies (use Debian 12 to match Distroless)
FROM debian:12-slim AS redis-builder
RUN apt-get update && \
    apt-get install -y --no-install-recommends redis-server redis-tools && \
    mkdir -p /redis-bundle/bin /redis-bundle/lib && \
    cp /usr/bin/redis-server /redis-bundle/bin/ && \
    cp /usr/bin/redis-cli /redis-bundle/bin/ && \
    ldd /usr/bin/redis-server | grep "=> /" | awk '{print $3}' | xargs -I '{}' cp -v '{}' /redis-bundle/lib/ && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Stage 4: Final runtime image with Distroless
FROM gcr.io/distroless/python3-debian12:latest
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/usr/local/lib/python3.11/site-packages

# Copy Python dependencies from builder
COPY --from=python-builder /install /usr/local

# Copy application files
COPY app ./app

# Copy built React panel from panel-builder (output is in /build/panel)
COPY --from=panel-builder /build/panel ./app/static/panel

# Copy Redis binaries and libraries
COPY --from=redis-builder /redis-bundle/bin/redis-server /usr/bin/redis-server
COPY --from=redis-builder /redis-bundle/bin/redis-cli /usr/bin/redis-cli
COPY --from=redis-builder /redis-bundle/lib/* /usr/lib/x86_64-linux-gnu/

# Create a startup script for handling Redis + app
COPY --chmod=755 <<'EOF' /app/start.py
#!/usr/bin/env python3
import os
import sys
import time
import signal
import subprocess
from pathlib import Path

def start_redis():
    """Start Redis server in background"""
    redis_cmd = [
        '/usr/bin/redis-server',
        '--daemonize', 'yes',
        '--bind', '127.0.0.1',
        '--port', '6379',
        '--save', '',
        '--appendonly', 'no',
        '--dir', '/tmp'
    ]
    
    try:
        subprocess.run(redis_cmd, check=True)
        print("Redis server started", flush=True)
    except Exception as e:
        print(f"Failed to start Redis: {e}", flush=True)
        sys.exit(1)
    
    # Wait for Redis to be ready
    for i in range(50):
        try:
            result = subprocess.run(
                ['/usr/bin/redis-cli', 'ping'],
                capture_output=True,
                timeout=1
            )
            if result.returncode == 0:
                print("Redis is ready", flush=True)
                return
        except:
            pass
        time.sleep(0.1)
    
    print("Redis failed to start in time", flush=True)
    sys.exit(1)

def main():
    # Start Redis
    start_redis()
    
    # Start FastAPI application using Python module
    os.execvp('python3', [
        'python3',
        '-m', 'uvicorn',
        'app.main:app',
        '--host', '0.0.0.0',
        '--port', '8000',
        '--no-access-log'
    ])

if __name__ == '__main__':
    main()
EOF

EXPOSE 8000
CMD ["/app/start.py"]
