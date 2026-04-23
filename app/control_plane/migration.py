"""
Stream migration service - Manages reassigning active streams to new engines.
Replaces the legacy ProxyManager migration logic.
"""

import logging
import asyncio
from typing import Any, Dict, Optional

from ..core.config import cfg
from ..models.schemas import EngineState
from ..services.state import state
from ..infrastructure.engine_selection import select_best_engine

logger = logging.getLogger(__name__)

async def migrate_stream(
    stream_key: str,
    new_engine: Optional[EngineState] = None,
    old_container_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Migrate an active stream key to a new healthy engine."""
    normalized_key = str(stream_key or "").strip()
    if not normalized_key:
        return {"migrated": False, "reason": "invalid_stream_key"}

    selected_engine = new_engine
    if selected_engine is None:
        try:
            additional_load = {}
            if old_container_id:
                additional_load[str(old_container_id)] = int(cfg.MAX_STREAMS_PER_ENGINE)

            selected_engine, _ = select_best_engine(
                additional_load_by_engine=additional_load,
                reserve_pending=False,
            )
        except Exception as e:
            return {
                "migrated": False,
                "reason": f"engine_selection_failed:{e}",
            }

    if not selected_engine:
        return {"migrated": False, "reason": "no_target_engine"}

    target_container = str(selected_engine.container_id or "").strip()
    if not target_container:
        return {"migrated": False, "reason": "invalid_target_engine"}

    if old_container_id and target_container == str(old_container_id):
        return {
            "migrated": False,
            "reason": "target_equals_source",
            "old_container_id": str(old_container_id),
            "new_container_id": target_container,
        }

    result: Dict[str, Any] = {"migrated": True, "old_container_id": old_container_id}
    session_updates: Dict[str, Any] = {}



    # 2. Update state (which triggers Redis update and notifies Go data plane)
    try:
        updated_streams = state.reassign_active_streams_to_engine_by_key(
            stream_key=normalized_key,
            old_container_id=old_container_id,
            new_engine=selected_engine,
            session_updates=session_updates,
        )
        result["state_streams_reassigned"] = int(updated_streams)
        result["new_container_id"] = selected_engine.container_id
    except Exception as e:
        logger.warning("State reassignment after migration failed for key=%s: %s", normalized_key, e)
        result["migrated"] = False
        result["reason"] = f"state_sync_error:{e}"

    return result
