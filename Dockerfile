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

# Stage 4: Build Go unified binary (proxy + orchestrator + controlplane)
FROM golang:1.25 AS go-builder
WORKDIR /orchestrator
COPY app/orchestrator/ .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o acestream-unified ./cmd

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

# Copy Go unified binary
COPY --from=go-builder /orchestrator/acestream-unified /usr/local/bin/acestream-unified

# Startup: Redis → acestream-unified (proxy :8000 + orchestrator :8083 + controlplane) → proton-sidecar (:9099)
# acestream-unified is the single Go binary for all planes.
# proton-sidecar is the only Python process; it handles Proton VPN server list updates.
COPY --chmod=755 <<'EOF' /app/start.py
#!/usr/bin/env python3
"""
AceStream Orchestrator startup script (unified Go binary).

Process tree:
  Redis             → in-process key/value store
  acestream-unified → proxy (:8000) + orchestrator (:8083) + controlplane (embedded)
  proton-sidecar    → optional Proton VPN server updater (:9099)
"""
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
    subprocess.run([
        '/usr/bin/redis-server',
        '--daemonize', 'yes',
        '--bind', '0.0.0.0',
        '--port', '6379',
        '--save', '',
        '--appendonly', 'no',
        '--dir', '/tmp',
        '--protected-mode', 'no',
    ], check=True)
    print("Redis started", flush=True)
    for _ in range(50):
        try:
            r = subprocess.run(['/usr/bin/redis-cli', 'ping'], capture_output=True, timeout=1)
            if r.returncode == 0:
                print("Redis ready", flush=True)
                return
        except Exception:
            pass
        time.sleep(0.1)
    print("Redis failed to start", flush=True)
    sys.exit(1)

def start_go_acestream():
    env = os.environ.copy()
    env.setdefault('PROXY_LISTEN_ADDR', ':8000')
    env.setdefault('ORCHESTRATOR_LISTEN_ADDR', ':8083')
    env.setdefault('REDIS_HOST', 'localhost')
    env.setdefault('REDIS_PORT', '6379')
    p = subprocess.Popen(
        ['/usr/local/bin/acestream-unified'],
        env=env, stdout=sys.stdout, stderr=sys.stderr,
    )
    _procs.append(p)
    print(f"Go unified binary started (pid={p.pid})", flush=True)
    return p

def start_proton_sidecar():
    """Start the Proton VPN server updater sidecar (optional)."""
    if os.getenv('DISABLE_PROTON_SIDECAR', '').lower() in ('1', 'true', 'yes'):
        print("Proton sidecar disabled via DISABLE_PROTON_SIDECAR", flush=True)
        return None
    env = os.environ.copy()
    env.setdefault('PROTON_STORAGE_PATH', '/app/app/config/proton')
    p = subprocess.Popen(
        [
            'python3', '-m', 'uvicorn',
            'app.proton_service:app',
            '--host', '127.0.0.1',
            '--port', '9099',
            '--no-access-log',
        ],
        cwd='/app',
        env=env, stdout=sys.stdout, stderr=sys.stderr,
    )
    _procs.append(p)
    print(f"Proton sidecar started (pid={p.pid})", flush=True)
    return p

def main():
    print("Starting AceStream Orchestrator Stack (unified Go binary)...", flush=True)
    start_redis()

    go_proc = start_go_acestream()
    proton_proc = start_proton_sidecar()

    print("Stack initialized. Monitoring critical processes...", flush=True)

    while True:
        time.sleep(2)
        if go_proc.poll() is not None:
            print(f"CRITICAL: Go unified binary exited (code={go_proc.returncode})", flush=True)
            _stop_all()
        # Proton sidecar is optional — restart it if it dies, don't kill the stack.
        if proton_proc is not None and proton_proc.poll() is not None:
            print(f"WARNING: Proton sidecar exited (code={proton_proc.returncode}), restarting...", flush=True)
            _procs.remove(proton_proc)
            proton_proc = start_proton_sidecar()

if __name__ == '__main__':
    main()
EOF

EXPOSE 8000
CMD ["/app/start.py"]
