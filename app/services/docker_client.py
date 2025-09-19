import docker
from docker.errors import APIError, DockerException
import time
import logging

logger = logging.getLogger(__name__)

def get_client():
    """Get Docker client with retry logic for connection."""
    max_retries = 10
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            client = docker.from_env(timeout=15)
            # Test the connection
            client.ping()
            return client
        except (DockerException, Exception) as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to connect to Docker after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Docker connection attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 10)  # Exponential backoff, max 10s

def safe(container_call, *args, **kwargs):
    try:
        return container_call(*args, **kwargs)
    except APIError as e:
        raise RuntimeError(str(e)) from e
