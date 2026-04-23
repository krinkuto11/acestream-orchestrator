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
    apt-get install -y --no-install-recommends gcc g++ git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install \
    --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Stage 3: Collect binary dependencies (Redis, GPG)
FROM debian:12-slim AS dependency-builder
RUN apt-get update && \
    apt-get install -y --no-install-recommends redis-server redis-tools gnupg && \
    mkdir -p /bundle && \
    # Collect Redis
    cp --parents /usr/bin/redis-server /bundle/ && \
    cp --parents /usr/bin/redis-cli /bundle/ && \
    ldd /usr/bin/redis-server | grep "=> /" | awk '{print $3}' | xargs -I '{}' cp --parents '{}' /bundle/ && \
    # Collect GnuPG
    cp --parents /usr/bin/gpg /bundle/ && \
    cp --parents /usr/bin/gpgconf /bundle/ && \
    ldd /usr/bin/gpg | grep "=> /" | awk '{print $3}' | xargs -I '{}' cp --parents '{}' /bundle/ && \
    ldd /usr/bin/gpgconf | grep "=> /" | awk '{print $3}' | xargs -I '{}' cp --parents '{}' /bundle/ && \
    mkdir -p /bundle/usr/lib /bundle/usr/share && \
    cp -a /usr/lib/gnupg /bundle/usr/lib/ && \
    cp -a /usr/share/gnupg /bundle/usr/share/ && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Stage 4: Build Go proxy binary
FROM golang:1.23 AS go-builder
WORKDIR /proxy
COPY app/proxy-go/ .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o acestream-proxy ./cmd/proxy

# Stage 5: Final runtime image with Distroless
FROM gcr.io/distroless/python3-debian12:latest
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/usr/local/lib/python3.11/site-packages
ENV GNUPGHOME=/tmp/.gnupg

# Copy Python dependencies from builder
COPY --from=python-builder /install /usr/local

# Copy application files
COPY app ./app

# Copy built React panel from panel-builder
COPY --from=panel-builder /build/panel ./app/static/panel

# Copy collected binary dependencies (Redis, GPG)
COPY --from=dependency-builder /bundle/ /

# Copy Go proxy binary
COPY --from=go-builder /proxy/acestream-proxy /usr/local/bin/acestream-proxy

# Startup script: Redis → Go proxy (port 8000) → Python FastAPI (port 8001)
# Go proxy reverse-proxies all non-stream requests to Python, so clients always hit port 8000.
COPY --chmod=755 <<'EOF' /app/start.py
#!/usr/bin/env python3
import os
import sys
import time
import signal
import subprocess

_procs = []

def _stop_all(signum=None, frame=None):
    for p in _procs:
        try:
            p.terminate()
        except Exception:
            pass
    sys.exit(0)

signal.signal(signal.SIGTERM, _stop_all)
signal.signal(signal.SIGINT, _stop_all)

def start_redis():
    redis_cmd = [
        '/usr/bin/redis-server',
        '--daemonize', 'yes',
        '--bind', '0.0.0.0',
        '--port', '6379',
        '--save', '',
        '--appendonly', 'no',
        '--dir', '/tmp',
        '--protected-mode', 'no'
    ]
    subprocess.run(redis_cmd, check=True)
    print("Redis server started", flush=True)
    for _ in range(50):
        try:
            r = subprocess.run(['/usr/bin/redis-cli', 'ping'], capture_output=True, timeout=1)
            if r.returncode == 0:
                print("Redis is ready", flush=True)
                return
        except Exception:
            pass
        time.sleep(0.1)
    print("Redis failed to start", flush=True)
    sys.exit(1)

def start_go_proxy():
    env = os.environ.copy()
    env.setdefault('PROXY_LISTEN_ADDR', ':8000')
    env.setdefault('ORCHESTRATOR_URL', 'http://localhost:8001')
    env.setdefault('REDIS_HOST', 'localhost')
    env.setdefault('REDIS_PORT', '6379')
    p = subprocess.Popen(
        ['/usr/local/bin/acestream-proxy'],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    _procs.append(p)
    print(f"Go proxy started (pid={p.pid})", flush=True)
    return p

def start_python():
    env = os.environ.copy()
    p = subprocess.Popen(
        [
            'python3', '-m', 'uvicorn',
            'app.main:app',
            '--host', '127.0.0.1',
            '--port', '8001',
            '--no-access-log',
        ],
        cwd='/app',
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    _procs.append(p)
    print(f"Python orchestrator process started (pid={p.pid})", flush=True)
    return p

def main():
    print("Starting AceStream Orchestrator Stack...", flush=True)
    start_redis()
    
    # Start Go data plane and Python control plane
    go_proc = start_go_proxy()
    py_proc = start_python()

    print("Orchestrator stack initialized. Monitoring processes...", flush=True)
    
    # Monitor: exit if either child dies unexpectedly
    while True:
        time.sleep(2)
        if go_proc.poll() is not None:
            print(f"CRITICAL: Go proxy exited (code={go_proc.returncode}), stopping stack", flush=True)
            _stop_all()
        if py_proc.poll() is not None:
            print(f"CRITICAL: Python orchestrator exited (code={py_proc.returncode}), stopping stack", flush=True)
            _stop_all()

if __name__ == '__main__':
    main()
EOF

EXPOSE 8000
CMD ["/app/start.py"]
