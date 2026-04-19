"""
Service for generating sequential container names.
"""
import re
import threading
from typing import Optional
from ..persistence.db import SessionLocal
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


def generate_container_name(prefix: str = "engine", extra_exclude: list[str] = None) -> str:
    """
    Generate a sequential container name with the given prefix using lowest available number.

    Instead of always incrementing, this finds the lowest available number in the range [1, N+1]
    by checking existing containers in DB, Docker, and any pending creation intents in the State.

    Args:
        prefix (str): Prefix for the container name (default: "engine")
        extra_exclude (list[str]): Optional list of full container names to exclude (useful for loop-local reservations)

    Returns:
        str: Next available container name in sequence (e.g., "acestream-11")
    """
    with _name_generation_lock:
        with SessionLocal() as session:
            # Get all existing container names from DB that follow the pattern '{prefix}-N'
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

            # Plus check Docker for existing containers with the same pattern
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
                pass

            # Plus check pending intents in State to avoid collisions with in-flight requests
            try:
                from ..services.state import state
                pending_intents = state.list_pending_scaling_intents(intent_type="create_request")
                for intent in pending_intents:
                    intent_name = intent.get("details", {}).get("container_name")
                    if intent_name:
                        match = pattern.match(intent_name)
                        if match:
                            numbers.add(int(match.group(1)))
            except Exception:
                # If state check fails, continue with known numbers
                pass

            # Plus handle any extra exclusions provided by the caller (burst reservations)
            if extra_exclude:
                for name in extra_exclude:
                    match = pattern.match(name)
                    if match:
                        numbers.add(int(match.group(1)))

            # Find the lowest available number starting from 1
            next_num = 1
            while next_num in numbers:
                next_num += 1

            return f"{prefix}-{next_num}"
