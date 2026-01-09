FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Update pip and install Node.js, npm, Redis, and FFmpeg
RUN pip install --upgrade pip && \
    apt-get update && \
    apt-get install -y nodejs npm redis-server ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Install Python dependencies with proper SSL handling
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

# Copy application files
COPY app ./app

# Build React dashboard (always rebuild to include latest changes)
WORKDIR /app/app/static/panel-react
# Clean any existing build artifacts first
RUN rm -rf ../panel
RUN npm install --ignore-scripts && npm run build

# Return to app directory
WORKDIR /app

# Create startup script that runs both Redis and the app
RUN echo '#!/bin/bash\n\
# Configure memory overcommit (fixes Redis warning)\n\
echo 1 > /proc/sys/vm/overcommit_memory 2>/dev/null || true\n\
\n\
# Start Redis in the background\n\
redis-server --daemonize yes --bind 127.0.0.1 --port 6379\n\
\n\
# Wait for Redis to be ready\n\
timeout 10 bash -c "until redis-cli ping > /dev/null 2>&1; do sleep 0.1; done"\n\
\n\
# Start the FastAPI application\n\
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log\n\
' > /app/start.sh && chmod +x /app/start.sh

EXPOSE 8000
CMD ["/app/start.sh"]
