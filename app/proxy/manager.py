"""
ProxyManager - Entry point for AceStream proxy.
Wraps the battle-tested ProxyServer for FastAPI integration.
"""

import logging
from typing import Any, Dict, Optional

from ..models.schemas import EngineState
from .server import ProxyServer

logger = logging.getLogger(__name__)


class ProxyManager:
    """Singleton wrapper around ProxyServer for FastAPI integration."""
    
    @classmethod
    def get_instance(cls):
        """Get the ProxyServer singleton instance."""
        return ProxyServer.get_instance()

    @classmethod
    def migrate_stream(
        cls,
        stream_key: str,
        new_engine: Optional[EngineState] = None,
        old_container_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Migrate an active stream key to a new healthy engine (TS or HLS)."""
        normalized_key = str(stream_key or "").strip()
        if not normalized_key:
            return {"migrated": False, "reason": "invalid_stream_key"}

        selected_engine = new_engine
        if selected_engine is None:
            try:
                from ..core.config import cfg
                from ..services.engine_selection import select_best_engine

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

        ts_server = ProxyServer.get_instance()
        result: Dict[str, Any]

        try:
            if normalized_key in ts_server.stream_managers:
                result = ts_server.migrate_stream(normalized_key, selected_engine)
            else:
                from .hls_proxy import HLSProxyServer

                hls_server = HLSProxyServer.get_instance()
                if normalized_key in hls_server.stream_managers:
                    result = hls_server.migrate_stream(normalized_key, selected_engine)
                else:
                    return {
                        "migrated": False,
                        "reason": "stream_not_found_in_proxy",
                        "stream_key": normalized_key,
                    }
        except Exception as e:
            logger.warning("Proxy stream migration failed for key=%s: %s", normalized_key, e)
            return {
                "migrated": False,
                "reason": f"proxy_migration_failed:{e}",
                "stream_key": normalized_key,
            }

        if not bool(result.get("migrated")):
            return result

        try:
            from ..services.state import state

            updated_streams = state.reassign_active_streams_to_engine_by_key(
                stream_key=normalized_key,
                old_container_id=str(result.get("old_container_id") or old_container_id or ""),
                new_engine=selected_engine,
                session_updates=result.get("session_updates") or {},
            )
            result["state_streams_reassigned"] = int(updated_streams)
        except Exception as e:
            logger.warning("State reassignment after proxy migration failed for key=%s: %s", normalized_key, e)
            result["state_sync_error"] = str(e)

        return result
