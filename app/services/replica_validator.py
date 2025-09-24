"""
Replica validation service - provides reliable Docker socket validation and replica counting.
Ensures consistent state synchronization between in-memory state and actual Docker containers.
"""
import logging
from typing import Dict, List, Set, Tuple, Optional
from datetime import datetime, timezone
from .health import list_managed
from .state import state
from ..core.config import cfg

logger = logging.getLogger(__name__)

class ReplicaValidator:
    """Centralized service for validating replica counts against Docker socket."""
    
    def __init__(self):
        self._last_validation = None
        self._validation_cache_ttl_s = 5  # Cache validation results for 5 seconds
        self._cached_result = None
    
    def get_docker_container_status(self) -> Dict[str, any]:
        """Get comprehensive status from Docker socket."""
        try:
            managed_containers = list_managed()
            running_containers = [c for c in managed_containers if c.status == "running"]
            
            container_ids = {c.id for c in running_containers}
            container_details = {
                c.id: {
                    'status': c.status,
                    'name': c.name,
                    'labels': c.labels or {},
                    'created': c.attrs.get('Created', ''),
                    'ports': c.attrs.get('NetworkSettings', {}).get('Ports', {})
                }
                for c in managed_containers
            }
            
            return {
                'total_managed': len(managed_containers),
                'total_running': len(running_containers),
                'running_container_ids': container_ids,
                'container_details': container_details,
                'containers': running_containers
            }
        except Exception as e:
            logger.error(f"Failed to get Docker container status: {e}")
            return {
                'total_managed': 0,
                'total_running': 0,
                'running_container_ids': set(),
                'container_details': {},
                'containers': []
            }
    
    def validate_and_sync_state(self, force_reindex: bool = False) -> Tuple[int, int, int]:
        """
        Validate state against Docker socket and sync if needed.
        Returns: (total_running, used_engines, free_count)
        """
        now = datetime.now(timezone.utc)
        
        # Use cached result if recent enough and not forcing
        if (not force_reindex and 
            self._cached_result and 
            self._last_validation and 
            (now - self._last_validation).total_seconds() < self._validation_cache_ttl_s):
            return self._cached_result
        
        # Get current state
        all_engines = state.list_engines()
        active_streams = state.list_streams(status="started")
        docker_status = self.get_docker_container_status()
        
        # Find engines currently in use
        used_container_ids = {stream.container_id for stream in active_streams}
        
        # Docker containers as source of truth for total running count
        total_running = docker_status['total_running']
        used_engines = len(used_container_ids)
        free_count = total_running - used_engines
        
        # Check for state/Docker discrepancies
        state_engine_count = len(all_engines)
        running_container_ids = docker_status['running_container_ids']
        state_container_ids = {engine.container_id for engine in all_engines}
        
        sync_needed = False
        
        # Check if counts don't match
        if state_engine_count != total_running:
            logger.warning(f"State/Docker engine count mismatch: state={state_engine_count}, docker={total_running}")
            sync_needed = True
        
        # Check for orphaned engines in state
        orphaned_engines = state_container_ids - running_container_ids
        if orphaned_engines:
            logger.info(f"Found {len(orphaned_engines)} orphaned engines in state: {orphaned_engines}")
            sync_needed = True
        
        # Check for engines missing from state
        missing_engines = running_container_ids - state_container_ids
        if missing_engines:
            logger.info(f"Found {len(missing_engines)} Docker containers missing from state: {missing_engines}")
            sync_needed = True
        
        # Perform synchronization if needed
        if sync_needed or force_reindex:
            logger.info("Triggering state synchronization with Docker")
            self._sync_state_with_docker(orphaned_engines, docker_status)
            
            # Recalculate after sync
            all_engines = state.list_engines()
            total_running = docker_status['total_running']  # Docker count remains the same
            free_count = total_running - used_engines
            
            logger.info(f"After sync: state_engines={len(all_engines)}, docker_running={total_running}, free={free_count}")
        
        result = (total_running, used_engines, free_count)
        self._cached_result = result
        self._last_validation = now
        
        return result
    
    def _sync_state_with_docker(self, orphaned_engines: Set[str], docker_status: Dict[str, any]):
        """Synchronize state with Docker containers."""
        # Remove orphaned engines from state
        for container_id in orphaned_engines:
            logger.info(f"Removing orphaned engine {container_id[:12]} from state")
            state.remove_engine(container_id)
        
        # Trigger reindex to pick up missing containers
        try:
            from .reindex import reindex_existing
            logger.info("Running reindex to sync with Docker containers")
            reindex_existing()
        except Exception as e:
            logger.error(f"Failed to reindex: {e}")
    
    def get_replica_deficit(self, min_replicas: int) -> int:
        """Calculate how many additional replicas are needed."""
        total_running, used_engines, free_count = self.validate_and_sync_state()
        deficit = min_replicas - free_count
        return max(0, deficit)
    
    def get_validation_status(self) -> Dict[str, any]:
        """Get current validation status for monitoring/debugging."""
        try:
            all_engines = state.list_engines()
            active_streams = state.list_streams(status="started")
            docker_status = self.get_docker_container_status()
            
            used_container_ids = {stream.container_id for stream in active_streams}
            
            state_container_ids = {engine.container_id for engine in all_engines}
            running_container_ids = docker_status['running_container_ids']
            
            orphaned_engines = state_container_ids - running_container_ids
            missing_engines = running_container_ids - state_container_ids
            
            total_running = docker_status['total_running']
            used_engines = len(used_container_ids)
            free_count = total_running - used_engines
            
            return {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'state_consistent': len(all_engines) == total_running,
                'counts': {
                    'state_engines': len(all_engines),
                    'docker_running': total_running,
                    'docker_total': docker_status['total_managed'],
                    'used_engines': used_engines,
                    'free_engines': free_count
                },
                'discrepancies': {
                    'orphaned_in_state': len(orphaned_engines),
                    'missing_from_state': len(missing_engines),
                    'orphaned_ids': list(orphaned_engines),
                    'missing_ids': list(missing_engines)
                },
                'cache_info': {
                    'last_validation': self._last_validation.isoformat() if self._last_validation else None,
                    'cache_ttl_s': self._validation_cache_ttl_s,
                    'has_cached_result': self._cached_result is not None
                },
                'config': {
                    'min_replicas': cfg.MIN_REPLICAS,
                    'max_replicas': cfg.MAX_REPLICAS,
                    'deficit': max(0, cfg.MIN_REPLICAS - free_count)
                }
            }
        except Exception as e:
            return {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'error': str(e),
                'state_consistent': False
            }
    
    def is_state_consistent(self) -> bool:
        """Check if state is consistent with Docker without forcing sync."""
        try:
            all_engines = state.list_engines()
            docker_status = self.get_docker_container_status()
            
            state_count = len(all_engines)
            docker_count = docker_status['total_running']
            
            return state_count == docker_count
        except Exception as e:
            logger.error(f"Error checking state consistency: {e}")
            return False


# Global instance
replica_validator = ReplicaValidator()