FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Update pip and install certificates
RUN pip install --upgrade pip && \
    apt-get update && \
    apt-get install -y ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# Install dependencies with proper SSL handling
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
