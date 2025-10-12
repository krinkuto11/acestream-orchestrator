FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Update pip and install certificates, Node.js and npm
RUN pip install --upgrade pip && \
    apt-get update && \
    apt-get install -y ca-certificates curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Install Python dependencies with proper SSL handling
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt

# Copy application files
COPY app ./app

# Build React dashboard
WORKDIR /app/app/static/panel-react
RUN npm install && npm run build

# Return to app directory
WORKDIR /app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--access-log", "false"]
