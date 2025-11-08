"""
Service for generating sequential container names.
"""
import re
import threading
from typing import Optional
from .db import SessionLocal
from ..models.db_models import EngineRow
from .docker_client import get_client

# Global lock for name generation to prevent race conditions
_name_generation_lock = threading.Lock()


def generate_engine_name() -> str:
    """
    Generate a sequential engine name like 'engine-1', 'engine-2', etc.
    
    Returns:
        str: Next available engine name in sequence
    """
    with _name_generation_lock:
        with SessionLocal() as session:
            # Get all existing engine names that follow the pattern 'engine-N'
            engines = session.query(EngineRow).filter(
                EngineRow.container_name.like('engine-%')
            ).all()
            
            # Extract numbers from existing engine names
            numbers = []
            pattern = re.compile(r'^engine-(\d+)$')
            
            for engine in engines:
                if engine.container_name:
                    match = pattern.match(engine.container_name)
                    if match:
                        numbers.append(int(match.group(1)))
            
            # Find the next available number
            if not numbers:
                next_num = 1
            else:
                next_num = max(numbers) + 1
            
            return f"engine-{next_num}"


def generate_container_name(prefix: str = "engine") -> str:
    """
    Generate a sequential container name with the given prefix using lowest available number.
    
    Instead of always incrementing (which leads to acestream-11 with only 10 engines),
    this finds the lowest available number in the range [1, N+1] where N is the number
    of existing containers with this prefix.
    
    Args:
        prefix (str): Prefix for the container name (default: "engine")
        
    Returns:
        str: Next available container name in sequence (e.g., "acestream-3" if 3 is the lowest free number)
    """
    with _name_generation_lock:
        with SessionLocal() as session:
            # Get all existing container names that follow the pattern '{prefix}-N'
            engines = session.query(EngineRow).filter(
                EngineRow.container_name.like(f'{prefix}-%')
            ).all()
            
            # Extract numbers from existing container names in database
            numbers = set()
            pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$')
            
            for engine in engines:
                if engine.container_name:
                    match = pattern.match(engine.container_name)
                    if match:
                        numbers.add(int(match.group(1)))
            
            # Also check Docker for existing containers with the same pattern
            # This prevents naming conflicts when containers exist in Docker but not in database
            try:
                cli = get_client()
                docker_containers = cli.containers.list(all=True)
                
                for container in docker_containers:
                    container_name = container.name
                    if container_name:
                        match = pattern.match(container_name)
                        if match:
                            numbers.add(int(match.group(1)))
            except Exception:
                # If Docker check fails, continue with database-only numbers
                # This ensures the function still works even if Docker is unavailable
                pass
            
            # Find the lowest available number starting from 1
            next_num = 1
            while next_num in numbers:
                next_num += 1
            
            return f"{prefix}-{next_num}"