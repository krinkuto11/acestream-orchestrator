"""
Service for fetching Docker container statistics.
Provides CPU, memory, network I/O, and block I/O metrics for containers.
"""

import logging
import re
import subprocess
from typing import Dict, Optional, List
from .docker_client import get_client
from docker.errors import NotFound, APIError

logger = logging.getLogger(__name__)

# Docker stats command format for batch collection
DOCKER_STATS_FORMAT = '{{.Container}}\t{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}'


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


def _parse_size_value(size_str: str) -> int:
    """
    Parse a size string like "111.8MiB", "3.17GB", "425MB" to bytes.
    
    Args:
        size_str: Size string with unit
        
    Returns:
        Size in bytes
    """
    size_str = size_str.strip()
    if not size_str or size_str == "0B":
        return 0
    
    # Extract number and unit
    match = re.match(r'([\d.]+)\s*([KMGT]?i?B)', size_str, re.IGNORECASE)
    if not match:
        return 0
    
    value = float(match.group(1))
    unit = match.group(2).upper()
    
    # Convert to bytes
    multipliers = {
        'B': 1,
        'KB': 1000,
        'KIB': 1024,
        'MB': 1000 * 1000,
        'MIB': 1024 * 1024,
        'GB': 1000 * 1000 * 1000,
        'GIB': 1024 * 1024 * 1024,
        'TB': 1000 * 1000 * 1000 * 1000,
        'TIB': 1024 * 1024 * 1024 * 1024,
    }
    
    return int(value * multipliers.get(unit, 1))


def _parse_io_value(io_str: str) -> tuple:
    """
    Parse an I/O string like "3.17GB / 6.29GB" or "425MB / 388MB" to (rx_bytes, tx_bytes).
    
    Args:
        io_str: I/O string with format "received / transmitted"
        
    Returns:
        Tuple of (rx_bytes, tx_bytes)
    """
    parts = io_str.split('/')
    if len(parts) != 2:
        return (0, 0)
    
    rx = _parse_size_value(parts[0].strip())
    tx = _parse_size_value(parts[1].strip())
    return (rx, tx)


def _parse_memory_usage(mem_str: str) -> tuple:
    """
    Parse memory usage string like "111.8MiB / 16.02GiB" to (usage_bytes, limit_bytes).
    
    Args:
        mem_str: Memory string with format "usage / limit"
        
    Returns:
        Tuple of (usage_bytes, limit_bytes)
    """
    parts = mem_str.split('/')
    if len(parts) != 2:
        return (0, 0)
    
    usage = _parse_size_value(parts[0].strip())
    limit = _parse_size_value(parts[1].strip())
    return (usage, limit)


def _parse_percent(percent_str: str) -> float:
    """
    Parse a percentage string like "0.28%" to a float.
    
    Args:
        percent_str: Percentage string
        
    Returns:
        Percentage as float
    """
    try:
        return float(percent_str.rstrip('%'))
    except (ValueError, AttributeError):
        return 0.0


def get_all_container_stats_batch() -> Dict[str, Dict]:
    """
    Get statistics for all containers using a single docker stats command.
    This is much more efficient than querying each container individually.
    
    Returns:
        Dictionary mapping container_id to stats dictionary.
        Returns empty dict if docker command fails.
    """
    try:
        # Run docker stats with --no-stream to get a single snapshot
        result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--no-trunc', '--format', DOCKER_STATS_FORMAT],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            logger.warning(f"Docker stats command failed: {result.stderr}")
            return {}
        
        # Parse the output
        stats_dict = {}
        lines = result.stdout.strip().split('\n')
        
        for line in lines:
            if not line.strip():
                continue
            
            parts = line.split('\t')
            if len(parts) < 7:
                continue
            
            container_id = parts[0].strip()
            cpu_percent = _parse_percent(parts[2])
            memory_usage, memory_limit = _parse_memory_usage(parts[3])
            memory_percent = _parse_percent(parts[4])
            network_rx_bytes, network_tx_bytes = _parse_io_value(parts[5])
            block_read_bytes, block_write_bytes = _parse_io_value(parts[6])
            
            stats_dict[container_id] = {
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
        
        return stats_dict
        
    except subprocess.TimeoutExpired:
        logger.warning("Docker stats command timed out")
        return {}
    except FileNotFoundError:
        logger.warning("Docker command not found")
        return {}
    except Exception as e:
        logger.error(f"Error running docker stats batch command: {e}")
        return {}


def get_multiple_container_stats(container_ids: List[str]) -> Dict[str, Dict]:
    """
    Get statistics for multiple containers using optimized batch collection.
    
    Args:
        container_ids: List of Docker container IDs
        
    Returns:
        Dictionary mapping container_id to stats dictionary
    """
    if not container_ids:
        return {}
    
    # Try batch approach first (much more efficient)
    all_stats = get_all_container_stats_batch()
    
    if all_stats:
        # Build lookup for efficient matching (handles both short and full IDs)
        stats_lookup = {}
        for stats_id, stats in all_stats.items():
            stats_lookup[stats_id] = stats
            # Also index by short ID (first 12 chars) for matching
            if len(stats_id) > 12:
                stats_lookup[stats_id[:12]] = stats
        
        # Filter to only requested containers
        results = {}
        for container_id in container_ids:
            # Try exact match first
            if container_id in stats_lookup:
                results[container_id] = stats_lookup[container_id]
            # Try short ID match
            elif len(container_id) >= 12 and container_id[:12] in stats_lookup:
                results[container_id] = stats_lookup[container_id[:12]]
        return results
    
    # Fallback to individual queries if batch fails
    logger.info("Batch stats collection failed, falling back to individual queries")
    results = {}
    for container_id in container_ids:
        stats = get_container_stats(container_id)
        if stats:
            results[container_id] = stats
    return results


def get_total_stats(container_ids: List[str]) -> Dict:
    """
    Get aggregated statistics across multiple containers using optimized batch collection.
    
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
    
    if not container_ids:
        return total
    
    # Use batch collection for efficiency
    stats_dict = get_multiple_container_stats(container_ids)
    
    for stats in stats_dict.values():
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
