"""
Service for fetching Docker container statistics.
Provides CPU, memory, network I/O, and block I/O metrics for containers.
"""

import logging
from typing import Dict, Optional, List
from .docker_client import get_client
from docker.errors import NotFound, APIError

logger = logging.getLogger(__name__)


def get_container_stats(container_id: str) -> Optional[Dict]:
    """
    Get statistics for a single container.
    
    Args:
        container_id: Docker container ID
        
    Returns:
        Dictionary with stats or None if container not found or error occurred
        Stats include:
        - cpu_percent: CPU usage percentage
        - memory_usage: Memory usage in bytes
        - memory_limit: Memory limit in bytes
        - memory_percent: Memory usage percentage
        - network_rx_bytes: Network bytes received
        - network_tx_bytes: Network bytes transmitted
        - block_read_bytes: Block I/O bytes read
        - block_write_bytes: Block I/O bytes written
    """
    try:
        client = get_client()
        container = client.containers.get(container_id)
        
        # Get stats with stream=False to get a single snapshot
        stats = container.stats(stream=False)
        
        # Extract CPU stats
        cpu_stats = stats.get('cpu_stats', {})
        precpu_stats = stats.get('precpu_stats', {})
        
        cpu_percent = 0.0
        cpu_delta = cpu_stats.get('cpu_usage', {}).get('total_usage', 0) - \
                    precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
        system_delta = cpu_stats.get('system_cpu_usage', 0) - \
                       precpu_stats.get('system_cpu_usage', 0)
        
        # Get number of online CPUs. If not provided by Docker, use the length of percpu_usage array
        # This fallback handles cases where online_cpus field is not populated by the Docker API
        online_cpus = cpu_stats.get('online_cpus', 0)
        if not online_cpus:
            online_cpus = len(cpu_stats.get('cpu_usage', {}).get('percpu_usage', [])) or 1
        
        if system_delta > 0 and cpu_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * online_cpus * 100.0
        
        # Extract memory stats
        memory_stats = stats.get('memory_stats', {})
        memory_usage = memory_stats.get('usage', 0)
        memory_limit = memory_stats.get('limit', 0)
        memory_percent = 0.0
        if memory_limit > 0:
            memory_percent = (memory_usage / memory_limit) * 100.0
        
        # Extract network stats
        networks = stats.get('networks', {})
        network_rx_bytes = 0
        network_tx_bytes = 0
        for interface_stats in networks.values():
            network_rx_bytes += interface_stats.get('rx_bytes', 0)
            network_tx_bytes += interface_stats.get('tx_bytes', 0)
        
        # Extract block I/O stats
        blkio_stats = stats.get('blkio_stats', {})
        io_service_bytes = blkio_stats.get('io_service_bytes_recursive', [])
        
        block_read_bytes = 0
        block_write_bytes = 0
        for entry in io_service_bytes:
            op = entry.get('op', '')
            value = entry.get('value', 0)
            if op.lower() == 'read':
                block_read_bytes += value
            elif op.lower() == 'write':
                block_write_bytes += value
        
        return {
            'container_id': container_id,
            'cpu_percent': round(cpu_percent, 2),
            'memory_usage': memory_usage,
            'memory_limit': memory_limit,
            'memory_percent': round(memory_percent, 2),
            'network_rx_bytes': network_rx_bytes,
            'network_tx_bytes': network_tx_bytes,
            'block_read_bytes': block_read_bytes,
            'block_write_bytes': block_write_bytes
        }
        
    except NotFound:
        logger.debug(f"Container {container_id[:12]} not found when fetching stats")
        return None
    except APIError as e:
        logger.warning(f"Docker API error fetching stats for {container_id[:12]}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching stats for container {container_id[:12]}: {e}")
        return None


def get_multiple_container_stats(container_ids: List[str]) -> Dict[str, Dict]:
    """
    Get statistics for multiple containers.
    
    Args:
        container_ids: List of Docker container IDs
        
    Returns:
        Dictionary mapping container_id to stats dictionary
    """
    results = {}
    for container_id in container_ids:
        stats = get_container_stats(container_id)
        if stats:
            results[container_id] = stats
    return results


def get_total_stats(container_ids: List[str]) -> Dict:
    """
    Get aggregated statistics across multiple containers.
    
    Args:
        container_ids: List of Docker container IDs
        
    Returns:
        Dictionary with total stats:
        - total_cpu_percent: Sum of CPU usage percentages
        - total_memory_usage: Sum of memory usage in bytes
        - total_network_rx_bytes: Sum of network bytes received
        - total_network_tx_bytes: Sum of network bytes transmitted
        - total_block_read_bytes: Sum of block I/O bytes read
        - total_block_write_bytes: Sum of block I/O bytes written
        - container_count: Number of containers with stats
    """
    total = {
        'total_cpu_percent': 0.0,
        'total_memory_usage': 0,
        'total_network_rx_bytes': 0,
        'total_network_tx_bytes': 0,
        'total_block_read_bytes': 0,
        'total_block_write_bytes': 0,
        'container_count': 0
    }
    
    for container_id in container_ids:
        stats = get_container_stats(container_id)
        if stats:
            total['total_cpu_percent'] += stats['cpu_percent']
            total['total_memory_usage'] += stats['memory_usage']
            total['total_network_rx_bytes'] += stats['network_rx_bytes']
            total['total_network_tx_bytes'] += stats['network_tx_bytes']
            total['total_block_read_bytes'] += stats['block_read_bytes']
            total['total_block_write_bytes'] += stats['block_write_bytes']
            total['container_count'] += 1
    
    # Round CPU percent
    total['total_cpu_percent'] = round(total['total_cpu_percent'], 2)
    
    return total
