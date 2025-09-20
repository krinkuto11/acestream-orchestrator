"""
Service for generating sequential container names.
"""
import re
from typing import Optional
from .db import SessionLocal
from ..models.db_models import EngineRow


def generate_engine_name() -> str:
    """
    Generate a sequential engine name like 'engine-1', 'engine-2', etc.
    
    Returns:
        str: Next available engine name in sequence
    """
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
    Generate a sequential container name with the given prefix.
    
    Args:
        prefix (str): Prefix for the container name (default: "engine")
        
    Returns:
        str: Next available container name in sequence
    """
    with SessionLocal() as session:
        # Get all existing container names that follow the pattern '{prefix}-N'
        engines = session.query(EngineRow).filter(
            EngineRow.container_name.like(f'{prefix}-%')
        ).all()
        
        # Extract numbers from existing container names
        numbers = []
        pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$')
        
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
        
        return f"{prefix}-{next_num}"