import docker
from docker.errors import APIError, DockerException
import time
import logging
import socket
import os

logger = logging.getLogger(__name__)

# Cache for detected orchestrator network
_orchestrator_network = None

def get_client(timeout: int = 30):
    """
    Get Docker client with retry logic for connection.
    
    Args:
        timeout: Socket timeout in seconds (default: 30s, increased from 15s for better 
                resilience during VPN container lifecycle events)
    """
    max_retries = 10
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            client = docker.from_env(timeout=timeout)
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

def get_orchestrator_network() -> str | None:
    """Detect the Docker network the orchestrator is running on."""
    global _orchestrator_network
    if _orchestrator_network:
        return _orchestrator_network
        
    # Check if we are running in Docker
    if not os.path.exists('/.dockerenv'):
        # Not in Docker, skip detection
        return None
        
    try:
        client = get_client()
        # Default Docker behavior: hostname matches container ID or name
        hostname = socket.gethostname()
        container = client.containers.get(hostname)
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        if networks:
            network_names = list(networks.keys())
            # If in multiple networks and "bridge" is one of them, prioritize others
            # as they are likely user-defined bridge networks from docker-compose.
            if len(network_names) > 1 and "bridge" in network_names:
                _orchestrator_network = [n for n in network_names if n != "bridge"][0]
            else:
                _orchestrator_network = network_names[0]
                
            logger.info(f"Detected orchestrator network: '{_orchestrator_network}'. Engines will be provisioned in this network by default.")
            return _orchestrator_network
    except Exception as e:
        logger.debug(f"Failed to detect orchestrator Docker network: {e}")
        
    return None
